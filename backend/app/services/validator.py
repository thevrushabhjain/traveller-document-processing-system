"""Post-extraction validation: format checks, expiry, Verhoeff for Aadhaar."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import List

from ..schemas import DocumentType, TravellerData, ValidationIssue

_PASSPORT_NUMBER_RE = re.compile(r"^[A-PR-WY][0-9]{7}$")
_AADHAAR_NUMBER_RE = re.compile(r"^\d{12}$")
_PAN_NUMBER_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_VOTER_ID_RE = re.compile(r"^[A-Z]{3}[0-9]{7}$")
_DL_NUMBER_RE = re.compile(r"^[A-Z]{2}\d{1,2}\d{4}\d{6,9}$")

# Verhoeff tables (used for Aadhaar checksum validation)
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _verhoeff_valid(number: str) -> bool:
    try:
        c = 0
        for i, digit in enumerate(reversed(number)):
            c = _D[c][_P[i % 8][int(digit)]]
        return c == 0
    except (ValueError, IndexError):
        return False


def validate(data: TravellerData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    if data.document_type == DocumentType.PASSPORT:
        _validate_passport(data, issues)
    elif data.document_type == DocumentType.AADHAAR:
        _validate_aadhaar(data, issues)
    elif data.document_type == DocumentType.PAN:
        _validate_pan(data, issues)
    elif data.document_type == DocumentType.VOTER_ID:
        _validate_voter_id(data, issues)
    elif data.document_type == DocumentType.DRIVING_LICENSE:
        _validate_driving_license(data, issues)

    _validate_dates(data, issues)
    return issues


def _validate_passport(data: TravellerData, issues: List[ValidationIssue]) -> None:
    num = data.document_number.value if data.document_number else None
    if not num:
        issues.append(ValidationIssue(field="document_number", severity="ERROR",
                                      message="Passport number could not be extracted"))
    elif not _PASSPORT_NUMBER_RE.match(num):
        issues.append(ValidationIssue(field="document_number", severity="WARNING",
                                      message="Passport number format is unusual"))
    if not data.full_name or not data.full_name.value:
        issues.append(ValidationIssue(field="full_name", severity="WARNING",
                                      message="Full name is missing"))


def _validate_aadhaar(data: TravellerData, issues: List[ValidationIssue]) -> None:
    num = data.document_number.value if data.document_number else None
    if not num:
        issues.append(ValidationIssue(field="document_number", severity="ERROR",
                                      message="Aadhaar number could not be extracted"))
        return
    digits = "".join(ch for ch in num if ch.isdigit())
    if not _AADHAAR_NUMBER_RE.match(digits):
        issues.append(ValidationIssue(field="document_number", severity="ERROR",
                                      message="Aadhaar number must be 12 digits"))
    elif not _verhoeff_valid(digits):
        issues.append(ValidationIssue(field="document_number", severity="WARNING",
                                      message="Aadhaar checksum (Verhoeff) failed"))


def _validate_pan(data: TravellerData, issues: List[ValidationIssue]) -> None:
    num = data.document_number.value if data.document_number else None
    if not num:
        issues.append(ValidationIssue(field="document_number", severity="ERROR",
                                      message="PAN could not be extracted"))
        return
    if not _PAN_NUMBER_RE.match(num.upper()):
        issues.append(ValidationIssue(field="document_number", severity="WARNING",
                                      message="PAN format is unusual (expected AAAAA9999A)"))
    if not data.full_name or not data.full_name.value:
        issues.append(ValidationIssue(field="full_name", severity="WARNING",
                                      message="Full name is missing"))


def _validate_voter_id(data: TravellerData, issues: List[ValidationIssue]) -> None:
    num = data.document_number.value if data.document_number else None
    if not num:
        issues.append(ValidationIssue(field="document_number", severity="ERROR",
                                      message="EPIC (voter ID) number could not be extracted"))
        return
    if not _VOTER_ID_RE.match(num.upper()):
        issues.append(ValidationIssue(field="document_number", severity="WARNING",
                                      message="EPIC number format is unusual (expected AAA9999999)"))


def _validate_driving_license(data: TravellerData, issues: List[ValidationIssue]) -> None:
    num = data.document_number.value if data.document_number else None
    if not num:
        issues.append(ValidationIssue(field="document_number", severity="ERROR",
                                      message="Driving licence number could not be extracted"))
        return
    compact = re.sub(r"[\s\-]", "", num.upper())
    if not _DL_NUMBER_RE.match(compact):
        issues.append(ValidationIssue(field="document_number", severity="WARNING",
                                      message="Driving licence number format is unusual"))


def _validate_dates(data: TravellerData, issues: List[ValidationIssue]) -> None:
    today = date.today()
    dob = _parse_iso(data.date_of_birth.value) if data.date_of_birth else None
    doi = _parse_iso(data.date_of_issue.value) if data.date_of_issue else None
    doe = _parse_iso(data.date_of_expiry.value) if data.date_of_expiry else None

    if dob and dob >= today:
        issues.append(ValidationIssue(field="date_of_birth", severity="ERROR",
                                      message="Date of birth is in the future"))
    if doe and doe < today:
        issues.append(ValidationIssue(field="date_of_expiry", severity="WARNING",
                                      message="Document is expired"))
    if doi and doe and doi > doe:
        issues.append(ValidationIssue(field="date_of_issue", severity="ERROR",
                                      message="Issue date is after expiry date"))


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
