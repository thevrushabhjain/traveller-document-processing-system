"""Indian Driving Licence extractor.

Driving licences (both the older card format and the newer transport
department format) print:
- DL number (state code + RTO code + year + serial, e.g. "MH12 20110012345")
- Name, Date of Birth, Blood Group
- Address (multi-line, at the bottom)
- Date of Issue and one or more validity/expiry dates
  (Non-Transport / Transport validity are sometimes both present)
- Issuing authority ("Transport Department", a state RTO, ...)
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

_DL_NUMBER_RE = re.compile(r"\b([A-Z]{2}[-\s]?\d{1,2}[-\s]?\d{4}[-\s]?\d{6,9})\b")
_DATE_RE = re.compile(r"\b(\d{2}[/\-]\d{2}[/\-]\d{4})\b")
_BLOOD_GROUP_RE = re.compile(r"\b(A|B|AB|O)[+-]\b", re.IGNORECASE)

_NAME_LABELS = ["name"]
_DOB_LABELS = ["dob", "date of birth"]
_ADDRESS_LABELS = ["address"]
_AUTHORITY_LABELS = ["issuing authority", "authority", "rto", "transport department"]
_RELATIVE_LABELS = ["son", "daughter", "wife of", "s/o", "d/o", "w/o", "father"]


def _extract_date(text: str) -> str:
    """Pull just the date substring out of a candidate that OCR line-grouping
    merged with neighbouring fields (e.g. "27-11-2004 Blood Group: ...")."""
    m = _DATE_RE.search(text)
    return m.group(1) if m else text.strip()


def _find_dl_number(lines: List[Line]) -> Optional[FieldValue]:
    candidates = scan_pattern(lines, _DL_NUMBER_RE)
    best = field_from_candidates(candidates)
    if best:
        best.value = re.sub(r"[\s\-]+", "", best.value).upper()
    return best


def _find_dates(lines: List[Line]) -> tuple[Optional[FieldValue], Optional[FieldValue], Optional[FieldValue]]:
    """Return (date_of_birth, date_of_issue, date_of_expiry).

    Driving licences usually print 2-3 dates: DOB, issue date, and one or
    two validity/expiry dates. We locate the DOB via its label first, then
    treat the earliest remaining date as issue and the latest as expiry.
    """
    dob_candidates = find_label_candidates(
        lines, _DOB_LABELS, value_validator=lambda v: bool(_DATE_RE.search(v)), clean_fn=_extract_date
    )
    dob = field_from_candidates(dob_candidates)

    all_dates = scan_pattern(lines, _DATE_RE)
    remaining = [c for c in all_dates if not dob or c.value != dob.value]
    if not remaining:
        return dob, None, None

    # Sort by textual date value using day-first parsing for ordering.
    def _key(c):
        m = _DATE_RE.search(c.value)
        if not m:
            return (0, 0, 0)
        d, mo, y = re.split(r"[/\-]", m.group(1))
        return (int(y), int(mo), int(d))

    remaining_sorted = sorted(remaining, key=_key)
    issue = FieldValue(value=remaining_sorted[0].value, confidence=remaining_sorted[0].confidence)
    expiry = FieldValue(value=remaining_sorted[-1].value, confidence=remaining_sorted[-1].confidence) if len(remaining_sorted) > 1 else None
    return dob, issue, expiry


def _find_address(lines: List[Line]) -> Optional[FieldValue]:
    candidates = find_label_candidates(lines, _ADDRESS_LABELS, max_lines_below=3)
    if not candidates:
        return None
    # Merge consecutive next-line candidates from the same label hit into
    # one address block instead of keeping only a single line.
    same_label = [c for c in candidates if c.source in ("same_line", "next_line")]
    same_label.sort(key=lambda c: c.line_index)
    if not same_label:
        return None
    merged = " ".join(dict.fromkeys(c.value.strip() for c in same_label))
    merged = despace_merged_words(merged)
    merged = fix_trailing_pincode(merged)
    avg_conf = sum(c.confidence for c in same_label) / len(same_label)
    return FieldValue(value=merged, confidence=round(avg_conf, 4))


def extract(tokens: List[OCRToken]) -> TravellerData:
    data = TravellerData(document_type=DocumentType.DRIVING_LICENSE)
    lines = group_lines(tokens)
    text = full_text(lines)

    dl_number = _find_dl_number(lines)
    if dl_number:
        data.document_number = dl_number

    dob, issue, expiry = _find_dates(lines)
    if dob:
        data.date_of_birth = dob
    if issue:
        data.date_of_issue = issue
    if expiry:
        data.date_of_expiry = expiry

    name_candidates = find_label_candidates(lines, _NAME_LABELS, value_validator=name_like, clean_fn=clean_name_prefix)
    name = field_from_candidates(name_candidates)
    if name:
        data.full_name = name

    father_candidates = find_label_candidates(
        lines, _RELATIVE_LABELS, value_validator=name_like, clean_fn=clean_name_prefix
    )
    father = field_from_candidates(father_candidates)
    if father:
        data.father_name = father

    address = _find_address(lines)
    if address:
        data.address = address

    authority_candidates = find_label_candidates(lines, _AUTHORITY_LABELS)
    authority = field_from_candidates(authority_candidates)
    data.issuing_authority = authority or FieldValue(value="TRANSPORT DEPARTMENT", confidence=0.6)

    bg_match = _BLOOD_GROUP_RE.search(text)
    if bg_match:
        data.additional_fields["blood_group"] = FieldValue(value=bg_match.group(0).upper(), confidence=0.7)

    data.nationality = FieldValue(value="INDIAN", confidence=0.9)
    data.country = FieldValue(value="INDIA", confidence=0.9)
    return data
