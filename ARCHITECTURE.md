# PII Anonymization: Caller / Service Design

**Status:** Draft, based on the Presidio POC in this folder
**Presidio version:** 2.2.366 · **NLP model:** spaCy `en_core_web_lg` 3.8.0

## 1. Purpose

This design hides customer PII in free text, such as customer emails to the Office of the President, before people or downstream systems (for example an LLM) process the text. Afterwards it can put the real values back into replies.

The POC currently runs everything in one process ([pii_engine.py](pii_engine.py) and [api.py](api.py)). This design splits the work into two parts:

- **Service** (shared and stateless): detects PII. It runs the official Presidio Analyzer with our recognizer config.
- **Caller** (each consuming application): turns the detections into numbered placeholders, keeps the placeholder-to-value mapping, and restores the values later.

## 2. Architecture

```
┌──────────────────────────── Caller application ────────────────────────────┐
│                                                                             │
│  1. POST /analyze ─────────────────────────────────┐                        │
│  2. Resolve overlapping detections (anonymizer "keep")                      │
│  3. Assign numbered placeholders                    │                       │
│       (<PERSON_1>, <PHONE_NUMBER_2>, ...)           │                       │
│  4. Replace (anonymizer "replace")                  │                       │
│  5. Store mapping securely (placeholder -> value)   │                       │
│  6. Later: de-anonymize replies using the mapping   │                       │
│                                                     │                       │
│  presidio-anonymizer library (Python callers)       │                       │
│   OR Presidio Anonymizer service (other languages)  │                       │
└─────────────────────────────────────────────────────┼───────────────────────┘
                                                      │ HTTPS (internal only)
                                                      ▼
                          ┌────────── PII Detection Service ──────────┐
                          │  Official Presidio Analyzer image          │
                          │  + recognizers.yaml (our config)           │
                          │  spaCy en_core_web_lg, about 1 GB RAM      │
                          │  Stateless: stores no text and no PII      │
                          └────────────────────────────────────────────┘
```

### Why the split falls here

| Concern | Service | Caller |
|---|---|---|
| ML model (spaCy, about 1 GB of RAM) | ✅ Heavy, so it is shared and scaled on its own | |
| Detection rules (recognizers, context words) | ✅ One config file, so every caller gets the same detections | |
| Numbered placeholders and name linking | | ✅ Presidio's REST API cannot do this, because its `custom` operator takes a Python function |
| Mapping (it contains the real PII) | | ✅ Never leaves the caller, so the service stores no PII |
| De-anonymization | | ✅ Only the caller holds the mapping |

## 3. Service: PII Detection (Presidio Analyzer)

### 3.1 What it is
The service is the official Presidio Analyzer REST app. It is a Flask app that listens on port 3000 inside the container and reads its configuration from these environment variables:

| Variable | Purpose |
|---|---|
| `RECOGNIZER_REGISTRY_CONF_FILE` | Which recognizers to load. **This is where our config goes.** |
| `NLP_CONF_FILE` | The NLP engine and model. The default is spaCy `en_core_web_lg`. |
| `ANALYZER_CONF_FILE` | Supported languages and the default score threshold |
| `PORT` | The port to listen on (default `3000`) |
| `LOG_LEVEL` | Logging verbosity |

### 3.2 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/analyze` | Detect PII |
| `GET` | `/recognizers?language=en` | List the loaded recognizers. Use this to check the config was applied. |
| `GET` | `/supportedentities?language=en` | List the entity types it can detect |
| `GET` | `/health` | Health check |

**Request**
```json
POST /analyze
{
  "text": "Billing issue on account 287419365012 for John Smith, call (469) 555-0182.",
  "language": "en",
  "score_threshold": 0.4
}
```
**Response** (a list of detections; character offsets refer to the original text)
```json
[
  {"entity_type": "TELECOM_ACCOUNT", "start": 25, "end": 37, "score": 0.45},
  {"entity_type": "PERSON",          "start": 42, "end": 52, "score": 0.85},
  {"entity_type": "PHONE_NUMBER",    "start": 59, "end": 73, "score": 0.40}
]
```

### 3.3 Recognizer config: `service/conf/recognizers.yaml`

> Tested with presidio-analyzer 2.2.366. It loads `TelecomAccountRecognizer`. It does not load `UsBankRecognizer`. The text `Order 300145678912` is correctly *not* flagged.

```yaml
supported_languages:
  - en
global_regex_flags: 26

recognizers:
  # Built-in recognizers we keep. UsBankRecognizer is intentionally left out:
  # it labels any 8-17 digit number near "account" as US_BANK_NUMBER.
  - {name: SpacyRecognizer, type: predefined}   # spaCy NER: PERSON, LOCATION, DATE_TIME, NRP
  - {name: CreditCardRecognizer, type: predefined}
  - {name: UsSsnRecognizer, type: predefined}
  - {name: UsLicenseRecognizer, type: predefined}
  - {name: UsItinRecognizer, type: predefined}
  - {name: UsPassportRecognizer, type: predefined}
  - {name: EmailRecognizer, type: predefined}
  - {name: PhoneRecognizer, type: predefined}
  - {name: IpRecognizer, type: predefined}
  - {name: UrlRecognizer, type: predefined}
  - {name: DateRecognizer, type: predefined}
  - {name: IbanRecognizer, type: predefined}
  - {name: CryptoRecognizer, type: predefined}
  - {name: MacAddressRecognizer, type: predefined}
  - {name: UuidRecognizer, type: predefined}
  - {name: MedicalLicenseRecognizer, type: predefined}

  # Custom: telecom service account number (assumed 9-12 digits; see Open Items)
  - name: TelecomAccountRecognizer
    type: custom
    supported_language: en
    supported_entity: TELECOM_ACCOUNT
    patterns:
      - name: telecom_account
        regex: "\\b\\d{9,12}\\b"
        score: 0.1          # too low to report on its own
    # A word from this list just before the number adds +0.35, giving 0.45,
    # which passes the 0.4 threshold. A bare number without one is ignored.
    context: [account, acct, a/c, subscriber, billing, service, wireless, postpaid, prepaid, fiber, broadband]
```

All detection rules belong in this file, not in caller code, so every caller gets the same results.

### 3.4 Image: `service/Dockerfile`

Build a thin image on top of the official one so the config is versioned with it:

```dockerfile
FROM mcr.microsoft.com/presidio-analyzer:<pinned-version>
COPY conf/recognizers.yaml /config/recognizers.yaml
ENV RECOGNIZER_REGISTRY_CONF_FILE=/config/recognizers.yaml
```

Alternatively, run the official image unchanged and mount the file, for example from a Kubernetes ConfigMap, setting the same environment variable. Use a pinned version tag, never `latest`.

### 3.5 Running and sizing
```bash
docker build -t pii-detection:1.0.0 service/
docker run -d -p 5002:3000 --memory=2g pii-detection:1.0.0
curl "http://localhost:5002/recognizers?language=en"   # verify TelecomAccountRecognizer is listed
```
- Plan for about 1 GB of RAM per instance, mostly the spaCy model. Startup takes about 10–20 seconds while the model loads, so point the readiness probe at `/health`.
- Scale by adding replicas. A typical email takes tens of milliseconds.
- **Network:** keep the service internal only, behind TLS, and reachable only from approved callers. Requests contain raw PII.
- **Logging:** never log request or response bodies, here or in any gateway or APM tool in front of the service.

## 4. Caller: placeholder logic

### 4.1 Anonymization flow

| Step | Action | Uses |
|---|---|---|
| 1 | Send the text to `POST /analyze` | Detection service |
| 2 | Resolve overlapping detections, for example a phone number that was also tagged as a date | Anonymizer `keep` operator |
| 3 | Assign placeholders (rules in 4.2) | Caller code |
| 4 | Rename each detection's `entity_type` to its placeholder (`PERSON` becomes `PERSON_1`), then apply `replace`, which writes `<PERSON_1>` | Anonymizer `replace` operator |
| 5 | Store the mapping, for example `{"<PERSON_1>": "John Smith"}`, with the case or ticket | Caller storage |

The anonymizer steps (2 and 4) use only built-in operators, so they work in either setup:
- **Python callers:** `pip install presidio-anonymizer`. It is a small pure-Python library with no ML model.
- **Other languages:** call the official Presidio Anonymizer service (`POST /anonymize`). Its request body is `text`, `anonymizers` (the operator config) and `analyzer_results`.

### 4.2 Placeholder rules
1. Number each entity type in reading order: `<PERSON_1>`, `<PERSON_2>`, `<PHONE_NUMBER_1>`, and so on.
2. **The same value always gets the same placeholder.** Matching ignores case and whitespace, so "JOHN SMITH", "John Smith" and "john  smith" all match.
3. **Short forms of a name link to the full name:** a one-word PERSON ("John" or "Smith") reuses the placeholder of an earlier full name that contains that word ("John Smith"). This happens **only if exactly one** earlier full name matches, so "John Smith" and "John Brown" are never merged.
4. Overlapping detections are resolved *before* numbering (step 2), so numbers have no gaps.

### 4.3 Reference implementation (Python)

> This logic was tested against the analyzer configured as in section 3.3. It produced `<PERSON_1>` for "John Smith", "John" and "JOHN SMITH", `<PERSON_2>` for "Mary", and two different phone numbers as `<PHONE_NUMBER_1>` and `<PHONE_NUMBER_2>`.

```python
import requests
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig, RecognizerResult

ANALYZER_URL = "https://pii-detection.internal"   # from config
_anonymizer = AnonymizerEngine()


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


def anonymize(text: str, score_threshold: float = 0.4):
    """Return (anonymized_text, mapping). The mapping contains PII: store it securely."""
    resp = requests.post(f"{ANALYZER_URL}/analyze", timeout=10,
                         json={"text": text, "language": "en", "score_threshold": score_threshold})
    resp.raise_for_status()
    detections = [RecognizerResult(d["entity_type"], d["start"], d["end"], d["score"])
                  for d in resp.json()]

    # Step 2: resolve overlaps. "keep" leaves the text unchanged, so offsets stay valid.
    kept = _anonymizer.anonymize(text, detections, {"DEFAULT": OperatorConfig("keep")}).items

    # Step 3: assign placeholders
    placeholder, mapping, counter = {}, {}, {}
    for item in sorted(kept, key=lambda i: i.start):
        value = text[item.start:item.end]
        key = (item.entity_type, _norm(value))
        if key in placeholder:
            continue
        if item.entity_type == "PERSON" and " " not in key[1]:          # rule 3
            matches = {p for (e, full), p in placeholder.items()
                       if e == "PERSON" and " " in full and key[1] in full.split()}
            if len(matches) == 1:
                placeholder[key] = matches.pop()
                continue
        counter[item.entity_type] = counter.get(item.entity_type, 0) + 1
        placeholder[key] = f"{item.entity_type}_{counter[item.entity_type]}"
        mapping[f"<{placeholder[key]}>"] = value

    # Step 4: rename entity types, then plain replace writes "<PERSON_1>"
    renamed = [RecognizerResult(placeholder[(i.entity_type, _norm(text[i.start:i.end]))],
                                i.start, i.end, 1.0) for i in kept]
    out = _anonymizer.anonymize(text, renamed, {"DEFAULT": OperatorConfig("replace")})
    return out.text, mapping


def deanonymize(text: str, mapping: dict) -> str:
    """Put the original values back, e.g. into a reply drafted with placeholders."""
    for ph, value in mapping.items():
        text = text.replace(ph, value)
    return text
```

### 4.4 Example
```
Input : John Smith called about account 287419365012. Later John said his wife Mary
        uses (469) 555-0147. Call John at (469) 555-0182. JOHN SMITH
Output: <PERSON_1> called about account <TELECOM_ACCOUNT_1>. Later <PERSON_1> said his wife <PERSON_2>
        uses <PHONE_NUMBER_1>. Call <PERSON_1> at <PHONE_NUMBER_2>. <PERSON_1>
Mapping: {"<PERSON_1>": "John Smith", "<TELECOM_ACCOUNT_1>": "287419365012",
          "<PERSON_2>": "Mary", "<PHONE_NUMBER_1>": "(469) 555-0147",
          "<PHONE_NUMBER_2>": "(469) 555-0182"}
```

### 4.5 Caller responsibilities
- **The mapping is PII.** Store it encrypted, next to the case or ticket record, and delete it under the same retention policy. Never log it and never send it to an LLM.
- **Placeholders are scoped to one document.** `<PERSON_1>` means different people in different emails. Always restore with the mapping from the same document.
- **Handle service failures by failing closed:** if `/analyze` errors or times out, do **not** pass the original text on to the next system. Set timeouts and retries.
- Use one shared internal package for this logic. Do not copy and paste it between applications.

## 5. Security and privacy summary

| Item | Rule |
|---|---|
| Transport | HTTPS only; the service is reachable on the internal network only |
| Authentication | Put the service behind the API gateway or a service mesh with caller authentication. The official image has no built-in authentication. |
| Logging | No request or response bodies and no mappings in any log |
| Data at rest | The service stores nothing. The caller encrypts mappings. |
| Model and dependencies | Pin image versions and scan images for vulnerabilities |
| Licenses | Presidio, spaCy and `en_core_web_lg` are MIT-licensed |

## 6. Known gaps and open items

| # | Item | Owner / Action |
|---|---|---|
| 1 | The telecom account number format is **assumed** to be 9–12 digits | Confirm the real format and tighten the regex |
| 2 | Not detected yet: account PIN, SSN last 4, ticket IDs (`CS-…`), gateway serial numbers | Add custom recognizers to `recognizers.yaml` |
| 3 | `DATE_TIME` false positives (for example "Last 4" and "over 8 years"); dates also remove the complaint timeline | Decide whether to exclude `DATE_TIME` (send `entities` in the request) |
| 4 | Short-form name linking only works when the full name appears first, and nicknames (Mike and Michael) are not linked | Accept for v1 |
| 5 | Removing `UsBankRecognizer` means real US bank account numbers are not detected | Confirm that bank details are out of scope |
| 6 | The Presidio project has moved to a new home (docs now redirect to presidio.dataprivacystack.org) | Confirm the official image registry and tag before pinning |
| 7 | Accuracy has not been measured on real data | Run 100–200 approved real emails and record what is missed and what is wrongly flagged |

## 7. Testing
- **Service:** after deployment, `GET /recognizers` must list `TelecomAccountRecognizer` and must not list `UsBankRecognizer`.
- **Caller unit tests:** run the placeholder rules against fake analyzer responses. Cover repeated names, short forms, different case, two different Johns, and overlapping detections.
- **Regression set:** keep a set of sample emails with their expected anonymized output, and run it in CI against the service on every config or version change.

## 8. Migration from the POC

| POC file | Goes to |
|---|---|
| `pii_engine.py`: `telecom_account_recognizer`, removal of `UsBankRecognizer` | **Service:** `service/conf/recognizers.yaml` |
| `pii_engine.py`: `anonymize_numbered`, `_normalize`, `_link_partial_name`, `deanonymize_numbered` | **Caller:** a shared package such as `pii_client` (section 4.3) |
| `api.py`, `Dockerfile` (single combined service) | Replaced by the official analyzer image plus `service/Dockerfile` |
| `demo.py` | Becomes the caller demo, calling the service over HTTP |
