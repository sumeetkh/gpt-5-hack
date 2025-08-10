from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

import uvicorn
from new_agent import generate_plan_from_doc_url, PlanV1
from google_tools import fetch_google_url_private
from fastapi.middleware.cors import CORSMiddleware



app = FastAPI(
    title="Contract Assistant Agent API",
    description="Generates PlanV1 patches for a Google Doc",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or restrict to your script origin
    allow_credentials=True,
    allow_methods=["*"],  # ["POST"] if you want to be strict
    allow_headers=["*"],
)

# ---- Request schema ----
class GeneratePlanRequest(BaseModel):
    doc_url: str
    controls_text: Optional[str] = None
    plan_id: str = "plan_generated_001"
    stream: bool = True  # keep streaming flag so we can disable if needed

# ---- Response schema ----
class GeneratePlanResponse(PlanV1):
    pass

import re

import re

def _extract_controls_block(doc_text: str) -> str:
    """
    Extract the first section's body text from a markdown-like doc.
    Assumes the first '## ' heading is the Control section.
    Captures everything until the next '## ' heading or EOF.
    """
    if not doc_text:
        return ""

    # Normalize line endings and strip BOM if present
    text = doc_text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip("\n")

    # Find all top-level markdown headings starting with '## '
    heads = [m.start() for m in re.finditer(r"(?m)^##\s", text)]
    if not heads:
        return ""  # No headings found

    first = heads[0]
    # End-of-line for the heading
    eol = text.find("\n", first)
    if eol == -1:
        return ""  # Heading but no body

    # Find next heading if any
    next_heads = [pos for pos in heads if pos > first]
    end = next_heads[0] if len(next_heads) > 1 else len(text)

    # Extract and clean the block
    body = text[eol+1:end]
    return body.strip("\n")

# ---- Endpoint ----
@app.post("/plan/generate", response_model=GeneratePlanResponse)
def generate_plan(req: GeneratePlanRequest):
    """
    Given a Google Doc URL, read the control block from the doc
    and generate a structured PlanV1 with patches.
    """
    # Step 1: Fetch doc content as plain text
    fetched = fetch_google_url_private(req.doc_url)
    doc_text = fetched.get("content", "")
    print(doc_text[:800])

    # Step 2: Extract the "## Control" block until next heading
    controls_text = _extract_controls_block(doc_text)

    if not controls_text.strip():
        raise ValueError("Could not find '## Control' block in document.")

    # Step 3: Call your existing plan generator
    plan = generate_plan_from_doc_url(
        doc_url=req.doc_url,
        controls_text=controls_text,
        plan_id=req.plan_id,
        stream=req.stream,
    )
    return plan


# ---- For local running ----
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)