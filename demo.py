"""Presidio POC demo: detect PII, then anonymize it with several strategies.

Run:  .venv\\Scripts\\python demo.py
"""
from pii_engine import analyze, anonymize_numbered, deanonymize_numbered

# Fictional customer complaint (all personal details are made up)
SAMPLE = """From: michael.thompson82@example.com
To: Office of the President, AT&T
Date: November 10, 2026
Subject: Escalation - Repeated internet outages and incorrect billing on account 287419365012

Dear Office of the President,

My name is Michael Thompson and I have been an AT&T customer for over 8 years. I am writing
to escalate an unresolved issue with my AT&T Fiber 1 Gig internet and wireless service at
4521 Maple Ridge Drive, Plano, TX 75024.

Since October 3, 2026, my internet has dropped several times a day. Technician Sarah Collins
visited on October 15 and replaced the gateway (serial number 5H7K2M9P4Q), but the outages
continue. I also opened tickets CS-88213456 and CS-88214077 with customer care, and both were
closed without a fix.

In addition, my October bill shows $189.47 instead of my usual $95.00 for the unlimited
wireless plan on my line (469) 555-0182. My wife Jennifer's line (469) 555-0147 was also
charged an international roaming fee although she has not left the country.

Account details for verification:
- Account number: 287419365012
- Account PIN: 4829
- Last 4 of SSN: 6721 (full SSN on file: 536-22-8726)
- Card on file for autopay: Visa 4111 1111 1111 1111
- Router IP shown in my gateway logs: 192.168.1.254

Please have someone call me at (469) 555-0182 or email me at michael.thompson82@example.com.
I can also be reached at my office, Lakeside Dental Group, at (972) 555-0190.

Sincerely,
Michael Thompson
"""


def banner(title):
    print("\n" + "=" * 80 + f"\n{title}\n" + "=" * 80)


def main():
    banner("ORIGINAL TEXT")
    print(SAMPLE)

    banner("1. DETECTION (AnalyzerEngine)")
    results = sorted(analyze(SAMPLE), key=lambda r: r.start)
    print(f"{'ENTITY':<18}{'SCORE':>6}  TEXT")
    for r in results:
        print(f"{r.entity_type:<18}{r.score:>6.2f}  {SAMPLE[r.start:r.end]}")

    banner("2. REPLACE - numbered placeholders (each person/value gets its own number)")
    out, mapping = anonymize_numbered(SAMPLE)
    print(out.text)

    banner("3. PLACEHOLDER MAPPING (keep this private - it reverses the anonymization)")
    for placeholder, value in mapping.items():
        print(f"{placeholder:<22} {value}")

    banner("4. DE-ANONYMIZE - e.g. a reply written using the placeholders")
    reply = ("Dear <PERSON_1>, we have credited the roaming fee on line <PHONE_NUMBER_2> "
             "and asked <PERSON_2> to revisit. We will call you at <PHONE_NUMBER_1>.")
    print("Reply with placeholders:\n " + reply)
    print("\nReply sent to customer:\n " + deanonymize_numbered(reply, mapping))


if __name__ == "__main__":
    main()
