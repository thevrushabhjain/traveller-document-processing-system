"""Voter ID (EPIC - Elector's Photo Identity Card) extractor.

Indian voter ID cards print:
- EPIC number (3 letters + 7 digits, e.g. "ABC1234567")
- Elector's name, Father's/Husband's name
- Gender, Age or Date of Birth
- Address (multi-line)
- Assembly constituency / part number (issuing context)
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..schemas import DocumentType, FieldValue, OCRToken, TravellerData
from ..services.layout import (
    Line,
    clean_name_prefix,
    despace_merged_words,
    field_from_candidates,
    find_label_candidates,
    fix_trailing_pincode,
    full_text,
    group_lines,
    name_like,
    scan_pattern,
)

_EPIC_RE = re.compile(r"\b([A-Z]{3}[0-9]{7})\b")
_AGE_RE = re.compile(r"\bage\s*[:\-]?\s*(\d{1,3})\b", re.IGNORECASE)
_DOB_RE = re.compile(r"\b(\d{2}[/\-]\d{2}[/\-]\d{4})\b")
_GENDER_RE = re.compile(r"\b(male|female|transgender)\b", re.IGNORECASE)

_NAME_LABELS = ["elector's name", "electors name", "elector name"]
_RELATIVE_LABELS = ["father's name", "fathers name", "husband's name", "husbands name", "father/husband"]
_ADDRESS_LABELS = ["address"]


def _find_epic(lines: List[Line]) -> Optional[FieldValue]:
    candidates = scan_pattern(lines, _EPIC_RE)
    return field_from_candidates(candidates)


def _find_address(lines: List[Line]) -> Optional[FieldValue]:
    candidates = find_label_candidates(lines, _ADDRESS_LABELS, max_lines_below=3)
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda c: c.line_index)
    merged = " ".join(dict.fromkeys(c.value.strip() for c in ordered))
    merged = despace_merged_words(merged)
    merged = fix_trailing_pincode(merged)
    avg_conf = sum(c.confidence for c in ordered) / len(ordered)
    return FieldValue(value=merged, confidence=round(avg_conf, 4))


def extract(tokens: List[OCRToken]) -> TravellerData:
    data = TravellerData(document_type=DocumentType.VOTER_ID)
    lines = group_lines(tokens)
    text = full_text(lines)

    epic = _find_epic(lines)
    if epic:
        data.document_number = epic

    relative_candidates = find_label_candidates(
        lines, _RELATIVE_LABELS, value_validator=name_like, clean_fn=clean_name_prefix
    )
    relative = field_from_candidates(relative_candidates)
    if relative:
        data.father_name = relative

    name_candidates = find_label_candidates(
        lines, _NAME_LABELS, value_validator=name_like, clean_fn=clean_name_prefix
    )
    name = field_from_candidates(name_candidates)
    if not name:
        # Fall back to a bare "name" label, but skip any line that is
        # actually the father's/husband's name to avoid mixing the two up.
        broad_candidates = find_label_candidates(
            lines, ["name"], value_validator=name_like, clean_fn=clean_name_prefix
        )
        broad_candidates = [
            c for c in broad_candidates
            if not any(lbl in lines[c.line_index].text.lower() for lbl in _RELATIVE_LABELS)
        ]
        name = field_from_candidates(broad_candidates)
    if name:
        data.full_name = name

    gender_match = _GENDER_RE.search(text)
    if gender_match:
        data.gender = FieldValue(value=gender_match.group(1).upper(), confidence=0.85)

    dob_match = _DOB_RE.search(text)
    if dob_match:
        data.date_of_birth = FieldValue(value=dob_match.group(1), confidence=0.7)
    else:
        age_match = _AGE_RE.search(text)
        if age_match:
            data.additional_fields["age"] = FieldValue(value=age_match.group(1), confidence=0.6)

    address = _find_address(lines)
    if address:
        data.address = address

    data.nationality = FieldValue(value="INDIAN", confidence=0.95)
    data.country = FieldValue(value="INDIA", confidence=0.95)
    data.issuing_authority = FieldValue(value="ELECTION COMMISSION OF INDIA", confidence=0.95)
    return data
