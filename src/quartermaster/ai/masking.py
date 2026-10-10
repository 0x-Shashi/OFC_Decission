"""Redact personal and financial identifiers before text is sent to an LLM."""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<EMAIL>"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "<IBAN>"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<SSN>"),
]

# Phone, bank-account and card numbers. Requiring 9+ digits keeps dates (8 digits) and
# ordinary amounts readable, since the model needs those to reason about a test.
_LONG_NUMBER = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def _mask_long_number(m: re.Match[str]) -> str:
    return "<NUMBER>" if sum(c.isdigit() for c in m.group()) >= 9 else m.group()


def mask(text: str) -> str:
    for pattern, token in _RULES:
        text = pattern.sub(token, text)
    return _LONG_NUMBER.sub(_mask_long_number, text)
