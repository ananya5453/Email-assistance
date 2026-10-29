"""PII detection & redaction with a local, reversible vault.

Strategy (privacy-aware chunking / PII handling lesson):
- Detect PII with layered regexes: emails, phone numbers, credit cards
  (Luhn-validated), SSN, IBAN, OTP/verification codes.
- Replace with stable surrogates like `[EMAIL_1]` — stable within a *vault
  lifetime* so a thread still reads coherently and the same person is always
  `[EMAIL_1]`, not a random new token each occurrence.
- The mapping original ↔ surrogate lives ONLY in `~/.email_assistant/vault.json`
  (chmod 600). Redacted text goes to the index/LLM; re-identification happens
  locally, at display time, if the user opts in.

Everything here is local — no network calls.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from config import VAULT_PATH

# --- regex layers -----------------------------------------------------------
EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
# international-ish phone: +country, (555) 123-4567, 555-123-4567, 0412 345 678
PHONE_RE = re.compile(
    r"(?<![\w-])(\+?\d{1,3}[\s.-]?)?(\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])"
    r"\d{3,4}[\s.-]?\d{3,4}(?![\w-])"
)
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Luhn-validated 13-19 digit cards; the validator runs post-match.
CARD_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
OTP_RE = re.compile(
    r"\b(?:otp|code|pin)\D{0,20}?(\d{4,8})\b", re.IGNORECASE
)
CURRENCY_RE = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*(?:\.\d{2})?\s?(?:USD|EUR|GBP|AUD)\b")

KIND_PREFIX = {
    "email": "EMAIL",
    "phone": "PHONE",
    "ssn": "SSN",
    "card": "CARD",
    "iban": "IBAN",
    "otp": "OTP",
    "money": "MONEY",
}


@dataclass
class PiiFinding:
    kind: str
    value: str
    start: int
    end: int
    surrogate: str = ""


@dataclass
class RedactionResult:
    text: str
    findings: list[PiiFinding] = field(default_factory=list)


class PiiVault:
    """Stores original↔surrogate mappings locally; reversible, never networked."""

    def __init__(self, path=VAULT_PATH) -> None:
        self.path = Path(path)
        self._map: dict[str, dict[str, str]] = {}   # kind -> {original: surrogate}
        self._reverse: dict[str, str] = {}          # surrogate -> original
        self._counters: dict[str, int] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._map = data.get("map", {})
            except Exception:
                self._map = {}
        else:
            self._map = {}
        self._reverse = {
            sur: orig
            for kind, m in self._map.items()
            for orig, sur in m.items()
        }
        self._counters = {
            kind: len(m) for kind, m in self._map.items()
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"map": self._map}, indent=0), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # -- core operations -----------------------------------------------------
    def _surrogate(self, kind: str, original: str) -> str:
        m = self._map.setdefault(kind, {})
        if original not in m:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            m[original] = f"[{KIND_PREFIX.get(kind, 'PII')}_{self._counters[kind]}]"
        sur = m[original]
        self._reverse[sur] = original
        return sur

    def detect(self, text: str) -> list[PiiFinding]:
        """Layered detection: specific patterns first, then generic, no double-count."""
        spans: list[PiiFinding] = []

        def add(kind: str, match: re.Match) -> None:
            value = match.group(0)
            spans.append(PiiFinding(kind, value, match.start(), match.end()))

        for m in SSN_RE.finditer(text):
            add("ssn", m)
        for m in CARD_CANDIDATE_RE.finditer(text):
            if _luhn_ok(re.sub(r"[ -]", "", m.group(0))):
                add("card", m)
        for m in IBAN_RE.finditer(text):
            add("iban", m)
        for m in EMAIL_RE.finditer(text):
            add("email", m)
        for m in OTP_RE.finditer(text):
            add("otp", m)
        for m in PHONE_RE.finditer(text):
            add("phone", m)
        for m in CURRENCY_RE.finditer(text):
            add("money", m)

        # de-overlap: keep the first (most specific) claim on each span
        spans.sort(key=lambda f: (f.start, -(f.end - f.start)))
        result: list[PiiFinding] = []
        last_end = -1
        for f in spans:
            if f.start >= last_end:
                result.append(f)
                last_end = f.end
        return result

    def redact(self, text: str) -> RedactionResult:
        findings = self.detect(text)
        if not findings:
            return RedactionResult(text, [])
        out = []
        cursor = 0
        finals: list[PiiFinding] = []
        for f in findings:
            out.append(text[cursor:f.start])
            f.surrogate = self._surrogate(f.kind, f.value)
            out.append(f.surrogate)
            cursor = f.end
            finals.append(f)
        out.append(text[cursor:])
        return RedactionResult("".join(out), finals)

    def reidentify(self, text: str) -> tuple[str, int]:
        """Restore originals from surrogates. Local-only operation."""
        count = 0

        def repl(m: re.Match) -> str:
            nonlocal count
            sur = m.group(0)
            if sur in self._reverse:
                count += 1
                return self._reverse[sur]
            return sur

        pattern = re.compile(r"\[(?:EMAIL|PHONE|SSN|CARD|IBAN|OTP|MONEY|PII)_\d+\]")
        return pattern.sub(repl, text), count

    def stats(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._map.items()}


def _luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0
