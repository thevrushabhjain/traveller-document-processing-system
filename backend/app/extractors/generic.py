"""Generic government-issued document extractor.

Used whenever the classifier cannot confidently match a document to one
of the specialised types (passport, Aadhaar, PAN, driving licence, voter
ID). Rather than relying on rigid regular expressions, this extractor:

1. Rebuilds the document's reading order from OCR bounding boxes
   (see :mod:`app.services.layout`).
2. Scores multiple candidate lines for every field using keyword/context
   proximity (label immediately before a value, same line or the line
   below) instead of a single fixed pattern.
3. Filters out common boilerplate/noise (e.g. "Government of India",
   "UIDAI", "Online Authentication") before picking a winner.
4. Falls back to structural heuristics (longest alphanumeric token for a
   document number, longest capitalised line for a name, first date-like
   token for DOB) only when no labelled candidate exists.

The result is a best-effort structured record for *any* government ID,
including ones the system has never seen a template for.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..schemas import DocumentType, FieldValue, OCRToken, TravellerData
from ..services.layout import (
    Line,
    despace_merged_words,
    field_from_candidates,
    find_label_candidates,
    fix_trailing_pincode,
    full_text,
    group_lines,
    is_noise_line,
    name_like,
    scan_pattern,
)

_DATE_RE = re.compile(r"\b(\d{1,2}[\-/.](?:\d{1,2}|[A-Za-z]{3,})[\-/.]\d{2,4})\b")
_DOC_NUMBER_RE = re.compile(r"\b(?=[A-Z0-9]{6,15}\b)(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,15}\b")
_GENDER_RE = re.compile(r"\b(male|female|transgender|m|f)\b", re.IGNORECASE)
_PIN_RE = re.compile(r"\b\d{6}\b")

_NAME_LABELS = ["name", "full name", "holder", "applicant"]
_DOB_LABELS = ["dob", "date of birth", "born on", "birth"]
_GENDER_LABELS = ["sex", "gender"]
_NATIONALITY_LABELS = ["nationality", "citizen of"]
_ADDRESS_LABELS = ["address", "residing at", "permanent address"]
_DOC_NUMBER_LABELS = ["no.", "number", "id no", "card no", "reg no", "serial no"]
_ISSUE_LABELS = ["date of issue", "issued on", "issue date"]
_EXPIRY_LABELS = ["date of expiry", "valid until", "valid up to", "expiry"]
_AUTHORITY_LABELS = ["issuing authority", "authority", "issued by", "department"]
_FATHER_LABELS = ["father", "father's name", "s/o", "d/o", "c/o", "guardian"]
_COUNTRY_LABELS = ["country", "republic of"]

# Country names we can recognise without a label, to fill `country` when a
# document simply states its issuing nation somewhere on the page.
_KNOWN_COUNTRIES = [
    "india", "united states", "united kingdom", "canada", "australia",
    "singapore", "united arab emirates", "germany", "france", "japan",
]


def _find_document_number(lines: List[Line]) -> Optional[FieldValue]:
    labelled = find_label_candidates(
        lines, _DOC_NUMBER_LABELS, value_validator=lambda v: bool(_DOC_NUMBER_RE.search(v.replace(" ", "")))
    )
    best = field_from_candidates(labelled)
    if best:
        m = _DOC_NUMBER_RE.search(best.value.replace(" ", "").upper())
        if m:
            best.value = m.group(0)
        return best

    # Fallback: the longest alphanumeric token that mixes letters + digits
    # and is not a date, scored by length (longer IDs are usually more
    # specific / less likely to be incidental noise).
    pattern_hits = scan_pattern(lines, _DOC_NUMBER_RE)
    pattern_hits = [c for c in pattern_hits if not _DATE_RE.fullmatch(c.value)]
    if not pattern_hits:
        return None
    pattern_hits.sort(key=lambda c: (len(c.value), c.confidence), reverse=True)
    top = pattern_hits[0]
    return FieldValue(value=top.value.upper(), confidence=round(top.confidence, 4))


def _find_name(lines: List[Line]) -> Optional[FieldValue]:
    labelled = find_label_candidates(lines, _NAME_LABELS, value_validator=name_like)
    best = field_from_candidates(labelled)
    if best:
        return best

    # Fallback: the longest all-caps, name-shaped line that is not noise.
    scored: List[FieldValue] = []
    for line in lines:
        txt = line.text.strip()
        if name_like(txt) and txt.upper() == txt and not is_noise_line(txt):
            scored.append(FieldValue(value=txt, confidence=line.confidence))
    if not scored:
        return None
    scored.sort(key=lambda f: len(f.value or ""), reverse=True)
    return scored[0]


def _find_dob(lines: List[Line]) -> Optional[FieldValue]:
    labelled = find_label_candidates(lines, _DOB_LABELS, value_validator=lambda v: bool(_DATE_RE.search(v)))
    best = field_from_candidates(labelled)
    if best:
        m = _DATE_RE.search(best.value)
        if m:
            best.value = m.group(1)
        return best
    hits = scan_pattern(lines, _DATE_RE)
    return field_from_candidates(hits)


def _find_issue_expiry(lines: List[Line]) -> tuple[Optional[FieldValue], Optional[FieldValue]]:
    issue = field_from_candidates(
        find_label_candidates(lines, _ISSUE_LABELS, value_validator=lambda v: bool(_DATE_RE.search(v)))
    )
    expiry = field_from_candidates(
        find_label_candidates(lines, _EXPIRY_LABELS, value_validator=lambda v: bool(_DATE_RE.search(v)))
    )
    return issue, expiry


def _find_address(lines: List[Line]) -> Optional[FieldValue]:
    candidates = find_label_candidates(lines, _ADDRESS_LABELS, max_lines_below=4)
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda c: c.line_index)
    merged_parts = list(dict.fromkeys(c.value.strip() for c in ordered if not is_noise_line(c.value)))
    if not merged_parts:
        return None
    merged = " ".join(merged_parts)
    merged = despace_merged_words(merged)
    merged = fix_trailing_pincode(merged)
    avg_conf = sum(c.confidence for c in ordered) / len(ordered)
    return FieldValue(value=merged, confidence=round(avg_conf, 4))


def _find_country(lines: List[Line], full: str) -> Optional[FieldValue]:
    labelled = find_label_candidates(lines, _COUNTRY_LABELS)
    best = field_from_candidates(labelled)
    if best:
        return best
    lower = full.lower()
    for country in _KNOWN_COUNTRIES:
        if country in lower:
            return FieldValue(value=country.upper(), confidence=0.6)
    return None


def extract(tokens: List[OCRToken]) -> TravellerData:
    data = TravellerData(document_type=DocumentType.GENERIC)
    lines = group_lines(tokens)
    text = full_text(lines)

    doc_number = _find_document_number(lines)
    if doc_number:
        data.document_number = doc_number

    name = _find_name(lines)
    if name:
        data.full_name = name

    dob = _find_dob(lines)
    if dob:
        data.date_of_birth = dob

    gender_candidates = find_label_candidates(lines, _GENDER_LABELS, value_validator=lambda v: bool(_GENDER_RE.search(v)))
    gender = field_from_candidates(gender_candidates)
    if not gender:
        m = _GENDER_RE.search(text)
        if m:
            gender = FieldValue(value=m.group(1).upper(), confidence=0.6)
    if gender:
        data.gender = gender

    nationality = field_from_candidates(find_label_candidates(lines, _NATIONALITY_LABELS))
    if nationality:
        data.nationality = nationality

    country = _find_country(lines, text)
    if country:
        data.country = country

    address = _find_address(lines)
    if address:
        data.address = address

    issue, expiry = _find_issue_expiry(lines)
    if issue:
        data.date_of_issue = issue
    if expiry:
        data.date_of_expiry = expiry

    authority = field_from_candidates(find_label_candidates(lines, _AUTHORITY_LABELS))
    if authority:
        data.issuing_authority = authority

    father = field_from_candidates(find_label_candidates(lines, _FATHER_LABELS, value_validator=name_like))
    if father:
        data.father_name = father

    # Store every retained line as contextual metadata so the UI / API
    # consumer can inspect the raw layout that led to these decisions.
    extras: dict[str, FieldValue] = {}
    for i, line in enumerate(lines[:60]):
        if line.text.strip() and not is_noise_line(line.text):
            extras[f"line_{i:02d}"] = FieldValue(value=line.text, confidence=round(line.confidence, 4))
    data.additional_fields = extras
    return data
