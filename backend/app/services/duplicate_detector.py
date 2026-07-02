"""Duplicate traveller detection.

Combines two strategies:
1. Exact match on normalized document number (passport/Aadhaar) - fast & reliable.
2. Fuzzy match on `normalized_name + date_of_birth` when the document
   number is missing or slightly OCR-mangled.
"""
from __future__ import annotations

import re
from typing import List

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import ProcessedDocument
from ..schemas import DocumentType, DuplicateMatch, TravellerData

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip().upper())


def normalize_doc_number(num: str | None) -> str | None:
    if not num:
        return None
    return _NON_ALNUM.sub("", num.upper())


def find_duplicates(
    session: Session,
    traveller: TravellerData,
    exclude_id: str | None = None,
) -> List[DuplicateMatch]:
    """Search the database for potential duplicates of the given traveller."""
    settings = get_settings()
    matches: List[DuplicateMatch] = []
    seen_ids: set[str] = set()

    doc_number = normalize_doc_number(
        traveller.document_number.value if traveller.document_number else None
    )
    full_name = normalize_name(
        traveller.full_name.value if traveller.full_name else None
    )
    dob = traveller.date_of_birth.value if traveller.date_of_birth else None

    # --- 1) Exact document number match ---
    if doc_number:
        q = session.query(ProcessedDocument).filter(
            ProcessedDocument.document_number == doc_number,
            ProcessedDocument.status == "COMPLETED",
        )
        if exclude_id:
            q = q.filter(ProcessedDocument.id != exclude_id)
        for row in q.limit(20).all():
            if row.id in seen_ids:
                continue
            seen_ids.add(row.id)
            matches.append(
                DuplicateMatch(
                    document_id=row.id,
                    match_type="EXACT",
                    score=1.0,
                    matched_on=["document_number"],
                    full_name=row.full_name,
                    document_number=row.document_number,
                    document_type=_safe_doc_type(row.document_type),
                )
            )

    # --- 2) Fuzzy match on name + DOB ---
    if full_name and dob:
        q = session.query(ProcessedDocument).filter(
            ProcessedDocument.date_of_birth == dob,
            ProcessedDocument.status == "COMPLETED",
        )
        if exclude_id:
            q = q.filter(ProcessedDocument.id != exclude_id)
        candidates = q.limit(200).all()
        threshold = settings.fuzzy_match_threshold
        for row in candidates:
            if row.id in seen_ids or not row.normalized_name:
                continue
            score = fuzz.token_sort_ratio(full_name, row.normalized_name)
            if score >= threshold:
                seen_ids.add(row.id)
                matches.append(
                    DuplicateMatch(
                        document_id=row.id,
                        match_type="FUZZY",
                        score=round(score / 100.0, 4),
                        matched_on=["full_name", "date_of_birth"],
                        full_name=row.full_name,
                        document_number=row.document_number,
                        document_type=_safe_doc_type(row.document_type),
                    )
                )

    matches.sort(key=lambda m: (m.match_type != "EXACT", -m.score))
    return matches


def _safe_doc_type(value: str | None) -> DocumentType:
    try:
        return DocumentType(value or "UNKNOWN")
    except ValueError:
        return DocumentType.UNKNOWN
