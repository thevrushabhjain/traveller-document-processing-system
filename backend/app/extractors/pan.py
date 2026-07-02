"""PAN (Permanent Account Number) card extractor.

Indian PAN cards contain:
- a 10-character alphanumeric PAN (5 letters, 4 digits, 1 letter)
- the cardholder's name
- the father's name
- date of birth
- "Income Tax Department / Govt. of India" boilerplate + signature area

Layout is simple (each field is printed as its own line, usually without
an explicit "Name:" label - the label is a small caption line followed by
the value on the next line), so we rely on the shared layout module to
recover reading order and then apply keyword + positional heuristics.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..schemas import DocumentType, FieldValue, OCRToken, TravellerData
from ..services.layout import (
    Line,
    clean_name_prefix,
    field_from_candidates,
    find_label_candidates,
    group_lines,
    is_noise_line,
    name_like,
    respace_using_reference,
    scan_pattern,
)

_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
_DOB_RE = re.compile(r"\b(\d{2}[/\-]\d{2}[/\-]\d{4})\b")

_NAME_LABELS = ["name"]
_FATHER_LABELS = ["father", "father's name", "fathers name"]


def _find_pan_number(lines: List[Line]) -> Optional[FieldValue]:
    candidates = scan_pattern(lines, _PAN_RE)
    best = field_from_candidates(candidates)
    return best


def _find_dob(lines: List[Line]) -> Optional[FieldValue]:
    candidates = scan_pattern(lines, _DOB_RE)
    return field_from_candidates(candidates)


def _find_name_and_father(lines: List[Line], pan_line_idx: Optional[int]) -> tuple[Optional[FieldValue], Optional[FieldValue]]:
    """PAN cards print Name then Father's Name as consecutive caps lines,
    typically located above the DOB / PAN number block. We collect every
    plausible name-like line (in reading order) and assign the first one
    to the cardholder and the second to the father, unless explicit
    "Father" labels are present.
    """
    father_candidates = find_label_candidates(
        lines, _FATHER_LABELS, value_validator=name_like, max_lines_below=1, clean_fn=clean_name_prefix
    )
    father = field_from_candidates(father_candidates)

    name_candidates = find_label_candidates(
        lines, _NAME_LABELS, value_validator=name_like, max_lines_below=1, clean_fn=clean_name_prefix
    )
    # Exclude a match that is actually the father's name label bleeding in.
    name_candidates = [c for c in name_candidates if not father or c.value.upper() != father.value.upper()]
    name = field_from_candidates(name_candidates)

    if name and father:
        return name, father

    # Fallback: scan every all-caps name-like line in reading order and
    # take the first two distinct ones as Name / Father's Name.
    plain_names: List[FieldValue] = []
    for line in lines:
        text = line.text.strip()
        if name_like(text) and not is_noise_line(text) and text.upper() not in {"PAN", "INCOME TAX DEPARTMENT"}:
            plain_names.append(FieldValue(value=text, confidence=line.confidence))
    if not name and plain_names:
        name = plain_names[0]
    if not father and len(plain_names) > 1:
        father = plain_names[1]
    return name, father


def extract(tokens: List[OCRToken]) -> TravellerData:
    data = TravellerData(document_type=DocumentType.PAN)
    lines = group_lines(tokens)

    pan = _find_pan_number(lines)
    if pan:
        data.document_number = FieldValue(value=pan.value.replace(" ", "").upper(), confidence=pan.confidence)

    dob = _find_dob(lines)
    if dob:
        data.date_of_birth = dob

    name, father = _find_name_and_father(lines, None)
    if name and father and father.value:
        name.value = respace_using_reference(name.value, father.value)
    if father and name and name.value:
        father.value = respace_using_reference(father.value, name.value)
    if name:
        data.full_name = name
    if father:
        data.father_name = father

    data.nationality = FieldValue(value="INDIAN", confidence=0.95)
    data.country = FieldValue(value="INDIA", confidence=0.95)
    data.issuing_authority = FieldValue(value="INCOME TAX DEPARTMENT", confidence=0.95)
    return data
