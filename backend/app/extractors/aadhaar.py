"""Aadhaar (Indian UID) extractor.

Aadhaar cards contain:
- 12-digit UID (usually printed as XXXX XXXX XXXX)
- Full name (English + regional-language transliteration on new cards)
- DOB / Year of Birth
- Gender
- Address (multi-line, often at the bottom, ending in a 6-digit PIN code)
- Father's/Guardian's name (older cards)

Reading order is reconstructed from OCR bounding boxes so the name (which
sits just above the DOB line) and the address block (usually the last
few lines before the PIN code) can be located reliably even when the
raw OCR token order is not strictly top-to-bottom.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..schemas import DocumentType, FieldValue, OCRToken, TravellerData
from ..services.layout import (
    Line,
    fix_trailing_pincode,
    despace_merged_words,
    full_text,
    group_lines,
    is_noise_line,
    name_like,
    respace_using_reference,
)

_UID_RE = re.compile(r"\b(\d{4}\s?\d{4}\s?\d{4})\b")
_DOB_RE = re.compile(r"\b(\d{2}[/\-]\d{2}[/\-]\d{4})\b")
_YOB_RE = re.compile(r"year of birth\s*[:\-]?\s*(\d{4})", re.IGNORECASE)
_GENDER_RE = re.compile(r"\b(male|female|transgender)\b", re.IGNORECASE)
_PIN_RE = re.compile(r"\b\d{6}\b")

_NAME_STOPWORDS = {
    "AADHAAR", "AADHAR", "PASSPORT", "GOVERNMENT", "INDIA", "UIDAI",
    "NAME", "MALE", "FEMALE", "DOB", "GENDER", "YEAR", "ADDRESS",
}


def _find_uid(lines: List[Line]) -> Optional[FieldValue]:
    for line in lines:
        m = _UID_RE.search(line.text)
        if m:
            digits = re.sub(r"\s+", "", m.group(1))
            return FieldValue(value=digits, confidence=round(line.confidence, 4))
    return None


def _find_dob(lines: List[Line]) -> Optional[FieldValue]:
    for line in lines:
        m = _DOB_RE.search(line.text)
        if m:
            return FieldValue(value=m.group(1), confidence=round(line.confidence, 4))
    for line in lines:
        m = _YOB_RE.search(line.text)
        if m:
            return FieldValue(value=f"01-01-{m.group(1)}", confidence=0.55)
    return None


def _find_name(lines: List[Line]) -> Optional[FieldValue]:
    """The English name usually sits right next to the DOB / gender line -
    above it on the compact card layout, below it on the "Aadhaar letter"
    layout - so both directions are checked around whichever anchor line
    is found first."""
    for idx, line in enumerate(lines):
        if _DOB_RE.search(line.text) or "dob" in line.text.lower() or "year of birth" in line.text.lower():
            search_order = list(range(idx - 1, max(-1, idx - 4), -1)) + list(range(idx + 1, min(len(lines), idx + 4)))
            for back in search_order:
                candidate = lines[back].text.strip()
                if ":" in candidate:
                    candidate = candidate.split(":", 1)[-1].strip()
                if not candidate or is_noise_line(candidate):
                    continue
                if candidate.upper() in _NAME_STOPWORDS:
                    continue
                if name_like(candidate):
                    return FieldValue(value=candidate, confidence=round(lines[back].confidence, 4))
    return None


_OTHER_LABELS = ("name", "dob", "year of birth", "gender", "male", "female", "s/o", "d/o", "c/o", "mobile")
_RELATIVE_PREFIX_RE = re.compile(r"^\s*(?:s/o|d/o|c/o)\s*[A-Za-z .'\-]*,?\s*", re.IGNORECASE)


def _find_address(lines: List[Line]) -> Optional[FieldValue]:
    """Aadhaar addresses are printed as a labelled multi-line block,
    typically ending at the 6-digit PIN code. We scan forward from an
    explicit "Address" label (if present) and stop at the PIN code line or
    the next unrelated field label; otherwise fall back to collecting the
    contiguous non-noise lines immediately preceding the PIN code."""
    label_idx = None
    for idx, line in enumerate(lines):
        if "address" in line.text.lower():
            label_idx = idx
            break

    collected: List[str] = []
    confs: List[float] = []

    if label_idx is not None:
        first = lines[label_idx].text
        _, _, after = first.partition(":") if ":" in first else ("", "", re.sub(r"(?i)address", "", first))
        after = _RELATIVE_PREFIX_RE.sub("", after).strip()
        if after:
            collected.append(after)
            confs.append(lines[label_idx].confidence)
        for idx in range(label_idx + 1, min(len(lines), label_idx + 6)):
            text = lines[idx].text.strip()
            if not text or is_noise_line(text):
                continue
            if any(lbl in text.lower() for lbl in _OTHER_LABELS):
                break
            collected.append(text)
            confs.append(lines[idx].confidence)
            if _PIN_RE.search(text):
                break
    else:
        pin_idx = next((i for i, l in enumerate(lines) if _PIN_RE.search(l.text)), None)
        if pin_idx is None:
            return None
        start = pin_idx
        for idx in range(pin_idx - 1, max(-1, pin_idx - 10), -1):
            text = lines[idx].text.strip()
            if not text:
                continue
            if any(lbl in text.lower() for lbl in _OTHER_LABELS):
                break
            if is_noise_line(text):
                break
            start = idx
        for idx in range(start, pin_idx + 1):
            text = lines[idx].text.strip()
            if not text or is_noise_line(text) or any(lbl in text.lower() for lbl in _OTHER_LABELS):
                continue
            collected.append(text)
            confs.append(lines[idx].confidence)

    if not collected or len(" ".join(collected)) < 6:
        return None
    body = re.sub(r"\s+", " ", " ".join(collected)).strip(", ")
    body = despace_merged_words(body)
    body = fix_trailing_pincode(body)
    avg_conf = sum(confs) / len(confs) if confs else 0.6
    return FieldValue(value=body, confidence=round(avg_conf, 4))


def extract(tokens: List[OCRToken]) -> TravellerData:
    data = TravellerData(document_type=DocumentType.AADHAAR)
    lines = group_lines(tokens)
    text = full_text(lines)

    uid = _find_uid(lines)
    if uid:
        data.document_number = uid

    dob = _find_dob(lines)
    if dob:
        data.date_of_birth = dob

    gender_match = _GENDER_RE.search(text)
    if gender_match:
        data.gender = FieldValue(value=gender_match.group(1).upper(), confidence=0.85)

    name = _find_name(lines)
    if name:
        data.full_name = name

    father_match = re.search(r"(?:s/o|d/o|c/o)\s*([A-Za-z .'\-]{3,})", text, re.IGNORECASE)
    if father_match:
        father_val = father_match.group(1).strip().rstrip(",")
        data.father_name = FieldValue(value=father_val, confidence=0.7)

    address = _find_address(lines)
    if address:
        data.address = address

    if data.full_name and data.father_name and data.full_name.value and data.father_name.value:
        data.full_name.value = respace_using_reference(data.full_name.value, data.father_name.value)

    data.nationality = FieldValue(value="INDIAN", confidence=0.99)
    data.country = FieldValue(value="INDIA", confidence=0.99)
    data.issuing_authority = FieldValue(value="UIDAI", confidence=0.99)
    return data
