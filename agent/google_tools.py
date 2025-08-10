import io
import re
from urllib.parse import urlparse, parse_qs
from typing import Optional, Tuple

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.auth.transport.requests import Request
import os
import json
import uuid


class GoogleFetchError(Exception):
    pass

class GoogleDocCommentError(Exception):
    pass

def _extract_id(url: str) -> Optional[str]:
    m = re.search(r"/d/([a-zA-Z0-9_-]{20,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/file/d/([a-zA-Z0-9_-]{20,})", url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
    if "id" in qs and qs["id"]:
        return qs["id"][0]
    return None

def _detect_app(url: str) -> str:
    host = urlparse(url).netloc
    path = urlparse(url).path
    if "docs.google.com" in host:
        if path.startswith("/document/"):
            return "docs"
        if path.startswith("/spreadsheets/"):
            return "sheets"
        if path.startswith("/presentation/"):
            return "slides"
    if "drive.google.com" in host:
        return "drive"
    return "unknown"

def _choose_export_mime(app: str) -> Tuple[str, str]:
    """
    Returns (mime_for_export, logical_type) where logical_type is 'text' or 'binary'
    """
    if app == "docs":
        return "text/plain", "text"
    if app == "sheets":
        return "text/csv", "text"
    if app == "slides":
        # Slides → plain text of slide contents
        return "text/plain", "text"
    # Fallback for other Drive files uses get_media (binary)
    return "application/octet-stream", "binary"

# Scopes: full Docs + Drive access for reading/writing + commenting
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]

def _get_creds():
    """One-time auth to create token.json; after that, loads from file."""
    if os.path.exists("token.json"):
        return Credentials.from_authorized_user_file("token.json", SCOPES)

    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)
    with open("token.json", "w") as token:
        token.write(creds.to_json())
    return creds




def fetch_google_url_private(url: str) -> dict:
    """
    Fetch readable content from a PRIVATE Google Doc/Sheet/Slide (or Drive file).
    Returns: {'content': str, 'mime_type': str, 'source': 'drive_export'|'drive_download'}
    Raises GoogleFetchError on problems.
    """
    file_id = _extract_id(url)
    if not file_id:
        raise GoogleFetchError("Could not find a Google file ID in the URL.")

    app = _detect_app(url)
    creds = _get_creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    if app in ("docs", "sheets", "slides"):
        export_mime, logical = _choose_export_mime(app)
        buf = io.BytesIO()
        try:
            data = drive.files().export(fileId=file_id, mimeType=export_mime).execute()
        except Exception as e:
            raise GoogleFetchError(f"Export failed (check access and API enablement): {e}") from e
        buf.write(data)
        content_bytes = buf.getvalue()
        try:
            text = content_bytes.decode("utf-8", errors="replace")
        except Exception:
            text = content_bytes.decode("utf-8", errors="replace")
        return {"content": text, "mime_type": export_mime, "source": "drive_export"}

    elif app in ("drive", "unknown"):
        # Generic Drive file: try raw download (binary) and best-effort decode.
        request = drive.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        try:
            while not done:
                status, done = downloader.next_chunk()
        except Exception as e:
            raise GoogleFetchError(f"Download failed (check access and file type): {e}") from e
        content = buf.getvalue()
        # best-effort decode for text-like files
        try:
            text = content.decode("utf-8")
            mime = "text/plain"
            return {"content": text, "mime_type": mime, "source": "drive_download"}
        except UnicodeDecodeError:
            # If it’s truly binary, return a marker; your agent can branch on this
            return {"content": "", "mime_type": "application/octet-stream", "source": "drive_download"}

    else:
        raise GoogleFetchError("Unrecognized Google URL type.")

def _flatten_text(elems, acc, idx_map):
    """
    Walk Google Docs structural elements and build a flat string, while
    mapping each character position -> document index (startIndex-based).
    """
    for el in elems:
        if "paragraph" in el:
            for ce in el["paragraph"].get("elements", []):
                start = ce.get("startIndex")
                end = ce.get("endIndex")
                txt = ce.get("textRun", {}).get("content")
                if txt and start is not None and end is not None:
                    # Append and map indices
                    base = len(acc["text"])
                    acc["text"] += txt
                    # Map every character to doc index (costly but simple & robust)
                    for i, _ in enumerate(txt):
                        idx_map[base + i] = start + i
        elif "table" in el:
            for row in el["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    _flatten_text(cell.get("content", []), acc, idx_map)
        elif "sectionBreak" in el:
            # ignore
            pass

def _find_segment_indices(doc, segment: str) -> Tuple[int, int]:
    body = doc.get("body", {})
    content = body.get("content", [])
    acc = {"text": ""}
    idx_map = {}
    _flatten_text(content, acc, idx_map)
    hay = acc["text"]
    pos = hay.find(segment)
    if pos == -1:
        raise GoogleDocCommentError("Segment text not found in document.")
    start_doc_index = idx_map[pos]
    end_doc_index = idx_map[pos + len(segment) - 1] + 1
    return start_doc_index, end_doc_index

def patch_with_strikethrough_and_color(docs, file_id, start_idx, end_idx, new_text):
    # 1) Apply strikethrough + red color to the old text
    style_old = {
        "updateTextStyle": {
            "range": {"startIndex": start_idx, "endIndex": end_idx},
            "textStyle": {
                "strikethrough": True,
                "foregroundColor": {
                    "color": {"rgbColor": {"red": 0.8, "green": 0.0, "blue": 0.0}}
                }
            },
            "fields": "strikethrough,foregroundColor"
        }
    }

    # 2) Insert replacement text right after old text
    insert_new = {
        "insertText": {
            "location": {"index": end_idx},
            "text": " " + new_text  # space for separation
        }
    }

    # 3) Style the new text (bold + green)
    style_new = {
        "updateTextStyle": {
            "range": {
                "startIndex": end_idx + 1,  # +1 for the space
                "endIndex": end_idx + 1 + len(new_text)
            },
            "textStyle": {
                "bold": True,
                "foregroundColor": {
                    "color": {"rgbColor": {"red": 0.0, "green": 0.6, "blue": 0.0}}
                }
            },
            "fields": "bold,foregroundColor"
        }
    }

    # Run as a batch
    docs.documents().batchUpdate(
        documentId=file_id,
        body={"requests": [style_old, insert_new, style_new]}
    ).execute()


def add_comment_to_segment(doc_url: str, segment_text: str, replacement_text: str) -> dict:
    """
    Finds `segment_text` in the Doc, styles it as stricken with red background,
    and inserts `replacement_text` right after it with green background (no strikethrough).
    """
    file_id = _extract_id(doc_url)
    creds = _get_creds()

    docs = build("docs", "v1", credentials=creds, cache_discovery=False)

    # 1) Fetch doc structure and locate the segment
    doc = docs.documents().get(documentId=file_id).execute()
    start_idx, end_idx = _find_segment_indices(doc, segment_text)

    # Colors: soft red/green so the text stays readable
    red_bg = {"color": {"rgbColor": {"red": 1.0, "green": 0.85, "blue": 0.85}}}
    green_bg = {"color": {"rgbColor": {"red": 0.85, "green": 1.0, "blue": 0.85}}}

    # We’ll insert a single space then the replacement text right after the old segment.
    sep = " "
    inserted = sep + replacement_text

    requests = [
        # A) Style the OLD text (Text 1): strikethrough + red background
        {
            "updateTextStyle": {
                "range": {"startIndex": start_idx, "endIndex": end_idx},
                "textStyle": {
                    "strikethrough": True,
                    "backgroundColor": red_bg,
                },
                "fields": "strikethrough,backgroundColor"
            }
        },
        # B) Insert the NEW text right after the old segment
        {
            "insertText": {
                "location": {"index": end_idx},
                "text": inserted
            }
        },
        # C) Style the NEW text (Text 2): green background, EXPLICITLY clear strikethrough
        {
            "updateTextStyle": {
                "range": {
                    "startIndex": end_idx + len(sep),
                    "endIndex": end_idx + len(sep) + len(replacement_text)
                },
                "textStyle": {
                    "strikethrough": False,
                    "backgroundColor": green_bg
                },
                "fields": "strikethrough,backgroundColor"
            }
        }
    ]

    docs.documents().batchUpdate(documentId=file_id, body={"requests": requests}).execute()

    payload = {
        "file_id": file_id,
        "original_text": segment_text,
        "replacement_text": replacement_text,
        "original_start": start_idx,
        "original_end": end_idx,
        "new_text_start": end_idx + len(sep),
        "new_text_end": end_idx + len(sep) + len(replacement_text),
    }
    print(payload)
    return payload

# print(result["content"][:8000])

if __name__ == "__main__":
    # Example usage (set GOOGLE_CLIENT_SECRET_FILE or GOOGLE_CLIENT_SECRET_JSON and GOOGLE_TOKEN_PATH first):
    # url = "https://docs.google.com/document/d/.../edit"
    # print(fetch_google_url_private(url))
    pass