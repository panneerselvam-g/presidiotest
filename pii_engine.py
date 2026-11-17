"""Shared Presidio setup: analyzer (built-in recognizers only) + anonymizer."""
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine, DeanonymizeEngine
from presidio_anonymizer.entities import OperatorConfig

# Telecom service account number. Assumed format: 9-12 digits (adjust to the real one).
# A bare number is ambiguous, so the base score is low and only passes the 0.4
# threshold when a telecom/account word is within a few words before it (+0.35 boost).
telecom_account_recognizer = PatternRecognizer(
    supported_entity="TELECOM_ACCOUNT",
    patterns=[Pattern(name="telecom_account", regex=r"\b\d{9,12}\b", score=0.1)],
    context=["account", "acct", "a/c", "subscriber", "billing", "service",
             "wireless", "postpaid", "prepaid", "fiber", "broadband"],
)

analyzer = AnalyzerEngine()  # loads spaCy en_core_web_lg + built-in recognizers
# Built-in US bank rule = "any 8-17 digits near 'account'"; it mislabels telecom accounts.
analyzer.registry.remove_recognizer("UsBankRecognizer")
analyzer.registry.add_recognizer(telecom_account_recognizer)
anonymizer = AnonymizerEngine()
deanonymizer = DeanonymizeEngine()

# AES key for reversible encryption (16/24/32 chars). Use a secret store in real life.
DEMO_AES_KEY = "WmZq4t7w!z%C&F)J"

# Operator presets -------------------------------------------------------------
STRATEGIES = {
    "replace": {"DEFAULT": OperatorConfig("replace")},  # -> <PERSON>
    "redact": {"DEFAULT": OperatorConfig("redact")},
    "mask": {
        "DEFAULT": OperatorConfig(
            "mask", {"masking_char": "*", "chars_to_mask": 100, "from_end": False}),
        "PHONE_NUMBER": OperatorConfig(
            "mask", {"masking_char": "*", "chars_to_mask": 6, "from_end": True}),
        "CREDIT_CARD": OperatorConfig(
            "mask", {"masking_char": "*", "chars_to_mask": 12, "from_end": False}),
    },
    "hash": {"DEFAULT": OperatorConfig("hash", {"hash_type": "sha256"})},
    "encrypt": {"DEFAULT": OperatorConfig("encrypt", {"key": DEMO_AES_KEY})},
    "mixed": {
        "DEFAULT": OperatorConfig("replace"),
        "PERSON": OperatorConfig("replace", {"new_value": "<NAME>"}),
        "EMAIL_ADDRESS": OperatorConfig(
            "mask", {"masking_char": "*", "chars_to_mask": 5, "from_end": False}),
        "CREDIT_CARD": OperatorConfig(
            "mask", {"masking_char": "*", "chars_to_mask": 12, "from_end": False}),
        "US_SSN": OperatorConfig("redact"),
    },
}


def analyze(text: str, language: str = "en", score_threshold: float = 0.4, entities=None):
    """Detect PII entities in text."""
    return analyzer.analyze(text=text, language=language, entities=entities,
                            score_threshold=score_threshold)


def anonymize(text: str, strategy: str = "replace", score_threshold: float = 0.4):
    """Detect PII, then anonymize it with one of the STRATEGIES presets."""
    results = analyze(text, score_threshold=score_threshold)
    out = anonymizer.anonymize(text=text, analyzer_results=results, operators=STRATEGIES[strategy])
    return results, out


def anonymize_numbered(text: str, score_threshold: float = 0.4):
    """Replace PII with numbered placeholders: <PERSON_1>, <PERSON_2>, <PHONE_NUMBER_1>, ...

    The same value always gets the same placeholder, and numbers follow reading order.
    Returns (anonymized_result, mapping) where mapping is {placeholder: original value}.
    """
    results = analyze(text, score_threshold=score_threshold)

    # Pass 1: "keep" changes nothing but lets the anonymizer resolve overlapping
    # detections, so we number only the spans that will actually be replaced.
    kept = anonymizer.anonymize(text=text, analyzer_results=results,
                                operators={"DEFAULT": OperatorConfig("keep")})

    placeholders = {}  # (entity_type, normalized value) -> "<ENTITY_n>"
    mapping = {}       # "<ENTITY_n>" -> original value (first/fullest form seen)
    counters = {}      # entity_type -> last number used
    for item in sorted(kept.items, key=lambda i: i.start):
        value = text[item.start:item.end]
        key = (item.entity_type, _normalize(value))
        if key in placeholders:
            continue
        linked = _link_partial_name(item.entity_type, key[1], placeholders)
        if linked:
            placeholders[key] = linked  # "John" / "Smith" -> same placeholder as "John Smith"
            continue
        counters[item.entity_type] = counters.get(item.entity_type, 0) + 1
        placeholders[key] = f"<{item.entity_type}_{counters[item.entity_type]}>"
        mapping[placeholders[key]] = value

    # Pass 2: a "custom" operator per entity type looks up the placeholder for each value.
    operators = {
        entity: OperatorConfig("custom", {
            "lambda": lambda value, e=entity: placeholders[(e, _normalize(value))]})
        for entity in counters
    }
    out = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
    return out, mapping


def _normalize(value: str) -> str:
    """Case- and whitespace-insensitive key, so 'JOHN' and 'john' match."""
    return " ".join(value.lower().split())


def _link_partial_name(entity_type: str, name: str, placeholders: dict):
    """Link a one-word PERSON ('John', 'Smith') to an earlier full name ('John Smith').

    Only links when exactly one earlier full name contains that word, so two different
    people who share a first name are never merged.
    """
    if entity_type != "PERSON" or " " in name:
        return None
    matches = {ph for (e, full), ph in placeholders.items()
               if e == "PERSON" and " " in full and name in full.split()}
    return matches.pop() if len(matches) == 1 else None


def deanonymize_numbered(text: str, mapping: dict) -> str:
    """Put the original values back, e.g. into an LLM reply that uses the placeholders."""
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)
    return text
