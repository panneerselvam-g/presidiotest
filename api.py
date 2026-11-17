"""REST API for PII anonymization with numbered placeholders.

Local:  .venv\\Scripts\\uvicorn api:app --reload      (docs at http://127.0.0.1:8000/docs)
Docker: see README.md

Stateless by design: /anonymize returns the placeholder mapping to the caller, and the
caller sends it back to /deanonymize. The service never stores PII.
"""
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from pii_engine import analyze, anonymize_numbered, deanonymize_numbered

MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "100000"))
API_KEY = os.getenv("API_KEY")  # if unset, auth is disabled (local development only)

app = FastAPI(title="PII Anonymization Service", version="1.0.0")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(api_key_header)):
    """Reject requests without the right X-API-Key header (when API_KEY is set)."""
    if API_KEY and not (key and secrets.compare_digest(key, API_KEY)):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class TextIn(BaseModel):
    text: str = Field(..., max_length=MAX_TEXT_CHARS,
                      examples=["My name is John Smith. Call John at (469) 555-0182."])
    score_threshold: float = Field(0.4, ge=0.0, le=1.0)


class DeanonymizeIn(BaseModel):
    text: str = Field(..., max_length=MAX_TEXT_CHARS,
                      examples=["Dear <PERSON_1>, we will call you at <PHONE_NUMBER_1>."])
    mapping: dict[str, str] = Field(
        ..., examples=[{"<PERSON_1>": "John Smith", "<PHONE_NUMBER_1>": "(469) 555-0182"}])


@app.post("/analyze", dependencies=[Depends(require_api_key)])
def analyze_endpoint(body: TextIn):
    """Return detected PII entities with positions and confidence scores."""
    results = analyze(body.text, score_threshold=body.score_threshold)
    return [
        {"entity": r.entity_type, "start": r.start, "end": r.end,
         "score": round(r.score, 2), "text": body.text[r.start:r.end]}
        for r in sorted(results, key=lambda r: r.start)
    ]


@app.post("/anonymize", dependencies=[Depends(require_api_key)])
def anonymize_endpoint(body: TextIn):
    """Replace PII with numbered placeholders; return the text and the mapping."""
    out, mapping = anonymize_numbered(body.text, score_threshold=body.score_threshold)
    return {"anonymized_text": out.text, "mapping": mapping}


@app.post("/deanonymize", dependencies=[Depends(require_api_key)])
def deanonymize_endpoint(body: DeanonymizeIn):
    """Put the original values back into text that uses the placeholders."""
    return {"text": deanonymize_numbered(body.text, body.mapping)}


@app.get("/health")
def health():
    """Liveness/readiness probe. The model is loaded at import, so 'ok' means ready."""
    return {"status": "ok"}
