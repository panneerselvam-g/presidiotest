# Microsoft Presidio POC

This POC finds personal data (PII) in free text and anonymizes it with [Microsoft Presidio](https://microsoft.github.io/presidio/).

## Setup (already done in `.venv`)
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m spacy download en_core_web_lg
```

## Run
```powershell
# Console demo: detection, then each anonymization strategy, then an encrypt/decrypt round trip
.\.venv\Scripts\python demo.py

# REST API: open http://127.0.0.1:8000/docs
.\.venv\Scripts\uvicorn api:app --reload
```

API endpoints (send the header `X-API-Key` when the `API_KEY` environment variable is set):
- `POST /analyze` with `{"text": "..."}` returns the detected entities, their positions, and confidence scores.
- `POST /anonymize` with `{"text": "..."}` returns `anonymized_text` with numbered placeholders (`<PERSON_1>`, …) and a `mapping` from placeholders to the original values.
- `POST /deanonymize` with `{"text": "...", "mapping": {...}}` puts the original values back.
- `GET /health` is the health check.

The service is stateless and never stores PII. The caller keeps the mapping, which contains the real PII, so the caller must store it securely and must not log it.

## Deploy with Docker
```powershell
docker build -t pii-service:1.0.0 .
docker run -d -p 8000:8000 -e API_KEY=<strong-random-key> -e WORKERS=2 --memory=3g pii-service:1.0.0
```
- The image includes the spaCy model, so nothing is downloaded at runtime.
- The container runs as a non-root user and has a built-in health check.
- Each worker loads its own copy of the model and needs about 1 GB of RAM. Size the memory limit to WORKERS × 1 GB plus some headroom.
- Settings: `API_KEY` (if it is unset, authentication is disabled), `WORKERS` (default 2), and `MAX_TEXT_CHARS` (default 100000).

## Files
| File | Purpose |
|---|---|
| `pii_engine.py` | Sets up the analyzer and anonymizer, and the strategy presets |
| `demo.py` | Console walkthrough of all features |
| `api.py` | FastAPI wrapper |

## What it demonstrates
- **Built-in detection**: PERSON, EMAIL, PHONE, CREDIT_CARD, US_SSN, IP, LOCATION, DATE_TIME, URL, and others. It uses spaCy NER plus regex, checksums, and context words.
- **Custom recognizer `TELECOM_ACCOUNT`**: detects 9–12 digit numbers, but only when a word like "account", "billing" or "subscriber" appears just before them. The built-in `UsBankRecognizer` is removed because it labeled telecom account numbers as `US_BANK_NUMBER`.
- **Anonymization operators**: replace, redact, mask, hash (SHA-256), encrypt (AES), and a mixed policy that applies a different operator to each entity type.
- **Reversible anonymization**: encrypted text is decrypted back to the exact original.

## Observations and limitations
- **False positives**: spaCy sometimes tags number groups as `DATE_TIME`, and an email domain also matches as `URL`. You can tune this with `score_threshold`, by limiting the `entities=[...]` list, or by removing recognizers.
- **Invalid test values**: Presidio rejects known invalid test values. For example, the sample SSN `078-05-1120` is not detected as an SSN.
- **Overlapping matches**: when two detections overlap, the anonymizer keeps the one with the higher score.
- **Language**: only English is configured. Other languages need their own spaCy or Stanza model and an NLP engine config.
- **Possible next steps**:
  - Use transformer-based NER for better accuracy on names and locations.
  - Use `presidio-image-redactor` for images and scanned documents.
  - Use `presidio-structured` for CSV files and DataFrames.
  - Use the Docker images (`mcr.microsoft.com/presidio-analyzer` and `-anonymizer`) to deploy Presidio as a service.
  - Redact prompts before sending them to an LLM, then de-anonymize the responses.
