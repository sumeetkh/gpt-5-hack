#!/usr/bin/env python3
"""
new_agent.py
------------
Reads a Google Doc and emits a PlanV1 (preamble + patches) matching the sidebar contract,
using OpenAI Responses API with Structured Outputs + streaming.

Patch {
  id: str
  section: str                 # exact heading line, e.g., "## 5. Service Levels (SLA)"
  segment: Optional[str]
  orig_text: str               # exact line present under `section`
  replace_text: str            # exact replacement line
  rationale: Optional[str]
  topic: Optional[str]
  choice_group: Optional[str]
}

PlanV1 {
  schema_version: "1.0"
  plan_id: str
  preamble: { summary?: str, considerations?: List[str] }
  patches: List[Patch]
}

CLI:
  python new_agent.py --doc-url "<google doc url>" [--controls controls.txt] [--out plan.json] [--plan-id PLAN01] [--no-stream]
"""

from __future__ import annotations

import os
import re
import json
import argparse
from typing import List, Optional, Tuple, Literal

from pydantic import BaseModel, ValidationError
from openai import OpenAI

# Your existing tools (must be on PYTHONPATH)
from google_tools import fetch_google_url_private


# =========================
# Pydantic data contract
# =========================

class Preamble(BaseModel):
    summary: Optional[str] = None
    considerations: Optional[List[str]] = None

class Patch(BaseModel):
    id: str
    section: str
    segment: Optional[str] = None
    orig_text: str
    replace_text: str
    rationale: Optional[str] = None
    topic: Optional[str] = None
    choice_group: Optional[str] = None

class PlanV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: str
    preamble: Optional[Preamble] = None
    patches: List[Patch]


# =========================
# Prompt & schema
# =========================

SYS_PROMPT = (
    "You are a careful contract editor. Return ONLY JSON that matches the provided JSON schema. "
    "Every `section` must be an exact heading line present in the document (starts with '## '). "
    "Every `orig_text` must be an exact line present under that section. "
    "Edits should be minimal and localized; preserve formatting and defined terms. "
    "Use `choice_group` for mutually exclusive alternatives (e.g., uptime), and `topic` for related independent edits."
)

USER_TMPL = """Document headings (use exactly as `section`):
{headings}

Controls (may be empty; use to bias suggestions):
{controls}

Contract text (truncated for context):
{doc_snippet}
"""

# Strict JSON schema for Structured Outputs
PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "plan_id", "patches"],
    "properties": {
        "schema_version": {"const": "1.0"},
        "plan_id": {"type": "string"},
        "preamble": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "considerations": {"type": "array", "items": {"type": "string"}}
            }
        },
        "patches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "section", "orig_text", "replace_text"],
                "properties": {
                    "id": {"type": "string"},
                    "section": {"type": "string"},
                    "segment": {"type": "string"},
                    "orig_text": {"type": "string"},
                    "replace_text": {"type": "string"},
                    "rationale": {"type": "string"},
                    "topic": {"type": "string"},
                    "choice_group": {"type": "string"}
                }
            }
        }
    }
}


# =========================
# Doc parsing / linting
# =========================

Heading = Tuple[str, int, int]  # (heading_line, start_idx, end_idx) in lines[]

def extract_headings_and_ranges(doc_text: str) -> List[Heading]:
    lines = doc_text.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.strip().startswith("## ")]
    out: List[Heading] = []
    for idx, h_i in enumerate(heads):
        heading_line = lines[h_i].rstrip()
        end_i = heads[idx + 1] if idx + 1 < len(heads) else len(lines)
        out.append((heading_line, h_i, end_i))
    return out

def section_range_for(heading_line: str, headings: List[Heading]) -> Optional[Tuple[int, int]]:
    for h, start, end in headings:
        if h.strip() == heading_line.strip():
            return (start, end)
    return None

def line_exists_in_section(orig_line: str, section_range: Tuple[int, int], doc_text: str) -> bool:
    start, end = section_range
    lines = doc_text.splitlines()
    needle = orig_line.strip()
    for i in range(start + 1, end):
        if lines[i].strip() == needle:
            return True
    return False

def lint_plan_against_doc(plan: PlanV1, doc_text: str) -> PlanV1:
    """Drop patches whose section or orig_text cannot be verified."""
    headings = extract_headings_and_ranges(doc_text)
    keep: List[Patch] = []
    for p in plan.patches:
        rng = section_range_for(p.section, headings)
        if not rng:
            continue
        if not line_exists_in_section(p.orig_text, rng, doc_text):
            continue
        keep.append(p)
    return PlanV1(schema_version="1.0", plan_id=plan.plan_id, preamble=plan.preamble, patches=keep)


# =========================
# LLM calls (stream / non-stream)
# =========================

def build_user_message(doc_text: str, controls_text: Optional[str]) -> str:
    headings = extract_headings_and_ranges(doc_text)
    head_block = "\n".join(h for (h, _, _) in headings) or "(no headings found)"
    controls_block = (controls_text or "").strip() or "(none provided)"
    snippet = doc_text[:18000]  # keep it sane
    return USER_TMPL.format(headings=head_block, controls=controls_block, doc_snippet=snippet)

def call_llm_structured(doc_text: str, controls_text: Optional[str], plan_id: str, stream: bool = True) -> PlanV1:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5")

    user_msg = build_user_message(doc_text, controls_text)
    messages = [
        {"role": "system", "content": SYS_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    def _parse_to_plan(text: str) -> PlanV1:
        try:
            data = json.loads(text)
        except Exception:
            # salvage JSON substring
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end >= start:
                data = json.loads(text[start:end+1])
            else:
                data = {"schema_version": "1.0", "plan_id": plan_id, "patches": []}
        data.setdefault("plan_id", plan_id)
        try:
            plan = PlanV1(**data)
        except ValidationError:
            # salvage valid patches if envelope is off
            good = []
            for p in data.get("patches", []) or []:
                try:
                    good.append(Patch(**p))
                except ValidationError:
                    pass
            plan = PlanV1(schema_version="1.0", plan_id=data.get("plan_id", plan_id), patches=good)
        return lint_plan_against_doc(plan, doc_text)

    # Prefer Responses API if available
    has_responses = hasattr(client, "responses") and hasattr(client.responses, "create")

    if has_responses:
        if stream:
            # Responses API streaming
            events = client.responses.create(
                model=model,
                input=messages,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "PlanV1", "schema": {**PLAN_SCHEMA, "strict": True}}},
                #temperature=0.2,
                stream=True,
            )
            buf = []
            print("\n--- streaming model output (structured JSON via Responses) ---\n", flush=True)
            for event in events:
                if event.type == "response.output_text.delta":
                    print(event.delta, end="", flush=True)
                    buf.append(event.delta)
                elif event.type == "response.error":
                    raise RuntimeError(getattr(event, "error", "model stream error"))
            print("\n\n--- end stream ---\n", flush=True)
            text = "".join(buf)
            return _parse_to_plan(text)
        else:
            final = client.responses.create(
                model=model,
                input=messages,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "PlanV1", "schema": {**PLAN_SCHEMA, "strict": True}}},
                #temperature=0.2,
            )
            text = getattr(final, "output_text", None) or ""
            return _parse_to_plan(text)

    # Fallback: Chat Completions (with streaming)
    if stream:
        kwargs = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        # Try to keep Structured Outputs on chat; if SDK rejects, we’ll remove it.
        try:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "PlanV1", "schema": {**PLAN_SCHEMA, "strict": True}}}
            stream_obj = client.chat.completions.create(**kwargs)
        except TypeError:
            # Older submodule path: no response_format on chat
            kwargs.pop("response_format", None)
            stream_obj = client.chat.completions.create(**kwargs)

        buf = []
        print("\n--- streaming model output (chat.completions) ---\n", flush=True)
        for chunk in stream_obj:
            try:
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    print(delta.content, end="", flush=True)
                    buf.append(delta.content)
            except Exception:
                pass
        print("\n\n--- end stream ---\n", flush=True)
        return _parse_to_plan("".join(buf))

    # Fallback: Chat Completions (non-stream)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "PlanV1", "schema": {**PLAN_SCHEMA, "strict": True}}},
            #temperature=0.2,
        )
    except TypeError:
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0.2)

    text = (resp.choices[0].message.content or "")
    return _parse_to_plan(text)


# =========================
# Public API
# =========================

# def generate_plan_from_doc_url(doc_url: str, controls_text: Optional[str] = None, plan_id: str = "plan_generated_001", stream: bool = True) -> PlanV1:
#     doc = fetch_google_url_private(doc_url)  # expects {"content": "..."}
#     doc_text = doc.get("content", "")
#     return call_llm_structured(doc_text, controls_text, plan_id, stream=stream)

from openai import OpenAI
import json, os
from typing import Optional, List
from pydantic import ValidationError

def call_llm_chat_streaming(doc_text: str, controls_text: Optional[str], plan_id: str) -> PlanV1:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5")

    user_msg = build_user_message(doc_text, controls_text)
    messages = [
        {"role": "system", "content": SYS_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    # Try: schema-constrained streaming -> json_schema, then json_object, then no constraint
    def _stream_with_kwargs(**kwargs):
        print("\n--- streaming (chat.completions) ---\n", flush=True)
        buf: List[str] = []
        stream = client.chat.completions.create(stream=True, **kwargs)
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    print(delta.content, end="", flush=True)
                    buf.append(delta.content)
            except Exception:
                pass
        print("\n\n--- end stream ---\n", flush=True)
        return "".join(buf)

    try:
        # Newer SDKs may support json_schema on chat
        text = _stream_with_kwargs(
            model=model,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "PlanV1", "schema": {**PLAN_SCHEMA, "strict": True}},
            },
            #temperature=0.2,
        )
    except TypeError:
        try:
            # Widely supported: json_object
            text = _stream_with_kwargs(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                #temperature=0.2,
            )
        except TypeError:
            # Oldest fallback: no response_format; rely on prompt
            text = _stream_with_kwargs(
                model=model,
                messages=messages,
                #temperature=0.2,
            )

    # Parse → Pydantic → lint
    def _parse(text: str) -> PlanV1:
        try:
            data = json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end >= start:
                data = json.loads(text[start:end+1])
            else:
                data = {"schema_version": "1.0", "plan_id": plan_id, "patches": []}
        data.setdefault("plan_id", plan_id)
        try:
            plan = PlanV1(**data)
        except ValidationError:
            good = []
            for p in data.get("patches", []) or []:
                try:
                    good.append(Patch(**p))
                except ValidationError:
                    pass
            plan = PlanV1(schema_version="1.0", plan_id=data.get("plan_id", plan_id), patches=good)
        return lint_plan_against_doc(plan, doc_text)

    return _parse(text)


def generate_plan_from_doc_url(doc_url: str, controls_text: Optional[str] = None, plan_id: str = "plan_generated_001", stream: bool = True) -> PlanV1:
    doc = fetch_google_url_private(doc_url)
    doc_text = doc.get("content", "")
    # Force streaming via Chat Completions
    return call_llm_chat_streaming(doc_text, controls_text, plan_id)


# =========================
# CLI
# =========================

def main():
    ap = argparse.ArgumentParser(description="Generate PlanV1 patches for a Google Doc (streamed structured JSON).")
    ap.add_argument("--doc-url", required=True, help="Google Doc URL")
    ap.add_argument("--controls", help="Path to a text file with natural-language controls")
    ap.add_argument("--out", help="Write JSON plan to this path (default: stdout)")
    ap.add_argument("--plan-id", default="plan_generated_001", help="Plan id to stamp")
    ap.add_argument("--no-stream", action="store_true", help="Disable streaming (use non-streaming call)")
    args = ap.parse_args()

    controls_text = None
    if args.controls:
        with open(args.controls, "r", encoding="utf-8") as f:
            controls_text = f.read()

    plan = generate_plan_from_doc_url(
        args.doc_url,
        controls_text=controls_text,
        plan_id=args.plan_id,
        stream=not args.no_stream,
    )

    payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.__dict__
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nWrote {args.out}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
