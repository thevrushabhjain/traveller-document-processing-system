# classifier.py
"""Document classifier: keyword + regex + MRZ pattern based scoring.

Every supported document type contributes a weighted score from two
independent signals - distinctive keywords/phrases that only appear on
that document, and structural patterns (MRZ line, Aadhaar UID, PAN
number, driving-licence number, EPIC number). The type with the highest
total score wins; when no type clears a minimum confidence bar the
document falls back to :class:`DocumentType.GENERIC` so the generic,
layout-driven extractor can still pull out best-effort fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

from ..schemas import DocumentType, OCRToken

# --- Structural patterns -----------------------------------------------
_MRZ_PASSPORT_RE = re.compile(r"P[<K][A-Z<]{3}[A-Z0-9<]{20,}")
_AADHAAR_NUMBER_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_PASSPORT_NUMBER_RE = re.compile(r"\b[A-PR-WY][0-9]{7}\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_VOTER_ID_RE = re.compile(r"\b[A-Z]{3}[0-9]{7}\b")
_DL_NUMBER_RE = re.compile(r"\b[A-Z]{2}[-\s]?\d{1,2}[-\s]?\d{4}[-\s]?\d{6,9}\b")

# MRZ line detection - tolerant to OCR errors
_MRZ_LINE1_RE = re.compile(r"P[<K][A-Z<]{2}[A-Z0-9<]{20,}")
_MRZ_LINE2_RE = re.compile(r"^[A-Z0-9<]{20,}$")

# --- Keyword banks -------------------------------------------------------
_PASSPORT_KEYWORDS = [
    "passport", "republic of", "nationality", "surname", "given name",
    "given names", "date of issue", "date of expiry",
    "place of birth", "type", "code",
]

# Expanded passport keywords for better detection
_PASSPORT_KEYWORDS_EXPANDED = [
    "passport", "republic of india", "republic of", "nationality", 
    "surname", "given name", "given names", "date of issue", 
    "date of expiry", "expiry date", "place of birth", 
    "type", "code", "passport no", "passport number",
    "date of birth", "dob", "issuing authority",
]

_AADHAAR_KEYWORDS = [
    "aadhaar", "aadhar", "unique identification", "government of india",
    "\u092d\u093e\u0930\u0924 \u0938\u0930\u0915\u093e\u0930", "\u0906\u0927\u093e\u0930", "uidai", "vid", "enrolment",
]
_DL_KEYWORDS = [
    "driving licence", "driving license", "dl no", "transport department",
    "motor vehicle", "form 6", "non-transport", "licence to drive",
    "regional transport", "cov", "rto",
]
_PAN_KEYWORDS = [
    "income tax department", "permanent account number", "pan card",
    "govt. of india income tax", "signature",
]
_VOTER_KEYWORDS = [
    "election commission", "electors photo identity card", "epic",
    "elector's photo identity card", "identity card", "assembly constituency",
]


@dataclass
class ClassificationResult:
    document_type: DocumentType
    confidence: float
    scores: dict[str, float]


def _detect_mrz(tokens: List[OCRToken]) -> Tuple[bool, int]:
    """Detect if MRZ lines are present in OCR tokens.
    
    Returns:
        Tuple of (has_mrz, mrz_confidence_score)
    """
    text = "\n".join(t.text for t in tokens)
    compact = text.replace(" ", "")
    
    # Check for MRZ pattern
    if _MRZ_PASSPORT_RE.search(compact):
        return True, 8  # High confidence, even higher than individual keywords
    
    # Check for MRZ line 1 and line 2 patterns
    mrz_line1_found = False
    mrz_line2_found = False
    
    for tok in tokens:
        cleaned = re.sub(r"\s+", "", tok.text.upper())
        # Check for line 1 pattern (starts with P)
        if _MRZ_LINE1_RE.match(cleaned):
            mrz_line1_found = True
        # Check for line 2 pattern (all alphanumeric + <, no letters-only)
        elif _MRZ_LINE2_RE.match(cleaned) and len(cleaned) >= 30:
            # Make sure it's not just a random string of letters
            letter_count = sum(1 for c in cleaned if c.isalpha())
            digit_count = sum(1 for c in cleaned if c.isdigit())
            # MRZ line 2 should have a mix of letters and digits or lots of '<'
            if digit_count > 5 or cleaned.count('<') > 10:
                mrz_line2_found = True
    
    if mrz_line1_found and mrz_line2_found:
        return True, 7  # High confidence for two MRZ lines
    
    if mrz_line1_found or mrz_line2_found:
        return True, 5  # Medium-high confidence for a single MRZ line
    
    return False, 0


def _detect_passport_keywords(text: str, lower: str) -> float:
    """Detect passport-specific keywords with scoring."""
    score = 0.0
    
    # Primary keywords - higher weight
    primary_keywords = ["passport", "republic of india"]
    for kw in primary_keywords:
        if kw in lower:
            score += 3.0
    
    # Secondary keywords - medium weight
    secondary_keywords = [
        "nationality", "surname", "given name", "given names",
        "date of issue", "date of expiry", "place of birth"
    ]
    for kw in secondary_keywords:
        if kw in lower:
            score += 1.5
    
    # Tertiary keywords - low weight
    tertiary_keywords = ["type", "code", "passport no", "passport number"]
    for kw in tertiary_keywords:
        if kw in lower:
            score += 0.8
    
    return score


def _detect_passport_patterns(text: str, compact: str) -> float:
    """Detect passport-specific patterns with scoring."""
    score = 0.0
    
    # Passport number pattern
    if _PASSPORT_NUMBER_RE.search(text):
        score += 2.0
    
    # Date patterns common in passports
    date_patterns = [
        r"\b\d{2}[\/\-.]\d{2}[\/\-.]\d{4}\b",  # DD/MM/YYYY
        r"\b\d{2}[\/\-.]\d{2}[\/\-.]\d{2}\b",  # DD/MM/YY
    ]
    for pattern in date_patterns:
        if re.search(pattern, text):
            score += 0.5
    
    return score


def classify_tokens(tokens: List[OCRToken]) -> ClassificationResult:
    """Return the most likely document type based on OCR text content."""
    text = "\n".join(t.text for t in tokens)
    lower = text.lower()
    compact = text.replace(" ", "")

    scores = {
        "PASSPORT": 0.0,
        "AADHAAR": 0.0,
        "DRIVING_LICENSE": 0.0,
        "PAN": 0.0,
        "VOTER_ID": 0.0,
    }

    # --- Passport detection with multiple signals ---
    
    # Signal 1: MRZ detection (highest weight)
    has_mrz, mrz_score = _detect_mrz(tokens)
    if has_mrz:
        scores["PASSPORT"] += mrz_score
    
    # Signal 2: Passport keywords
    scores["PASSPORT"] += _detect_passport_keywords(text, lower)
    
    # Signal 3: Passport patterns
    scores["PASSPORT"] += _detect_passport_patterns(text, compact)
    
    # Signal 4: Legacy passport keywords (for backward compatibility)
    for kw in _PASSPORT_KEYWORDS:
        if kw in lower:
            scores["PASSPORT"] += 1.0

    # --- Other document types (unchanged) ---
    for kw in _AADHAAR_KEYWORDS:
        if kw in lower:
            scores["AADHAAR"] += 1.0
    for kw in _DL_KEYWORDS:
        if kw in lower:
            scores["DRIVING_LICENSE"] += 1.0
    for kw in _PAN_KEYWORDS:
        if kw in lower:
            scores["PAN"] += 1.0
    for kw in _VOTER_KEYWORDS:
        if kw in lower:
            scores["VOTER_ID"] += 1.0

    # Structural / pattern signals - weighted higher than keywords
    if _MRZ_PASSPORT_RE.search(compact):
        scores["PASSPORT"] += 5.0  # This is now redundant with _detect_mrz but kept for safety
    if _PASSPORT_NUMBER_RE.search(text) and "passport" in lower:
        scores["PASSPORT"] += 2.0  # Redundant with _detect_passport_patterns but kept for safety
    
    # Other document patterns (unchanged)
    if _AADHAAR_NUMBER_RE.search(text):
        scores["AADHAAR"] += 4.0
    if _DL_NUMBER_RE.search(compact):
        scores["DRIVING_LICENSE"] += 4.0
    if _PAN_RE.search(compact):
        scores["PAN"] += 5.0
    if _VOTER_ID_RE.search(compact) and scores["PAN"] < 4.0:
        scores["VOTER_ID"] += 4.0

    # Determine best type
    best_type_str, best_score = max(scores.items(), key=lambda kv: kv[1])
    total = sum(scores.values())
    
    # --- Enhanced confidence calculation for passports ---
    if best_type_str == "PASSPORT" and best_score > 0:
        # Boost confidence for passport if MRZ was detected or if multiple signals present
        if has_mrz:
            # MRZ is a very strong signal
            confidence = min(0.95, 0.6 + (best_score / 20))
        elif best_score >= 5:
            # Multiple strong signals (keywords + patterns)
            confidence = min(0.85, 0.4 + (best_score / 15))
        else:
            # Weak signals
            confidence = min(0.7, 0.3 + (best_score / 10))
        
        # Ensure we don't classify random text as passport
        if best_score < 1.5:
            confidence = 0.0
            best_type_str = "GENERIC"
    else:
        # For other document types, use original logic
        if best_score < 1.5 and not has_mrz:
            return ClassificationResult(DocumentType.GENERIC, 0.0, scores)
        confidence = best_score / total if total else 0.0
        confidence = round(min(confidence, 0.99), 4)
    
    # Override if we detected MRZ but classification is not passport
    # (This handles edge cases where MRZ is detected but other document types have higher scores)
    if has_mrz and best_type_str != "PASSPORT":
        # If MRZ is present, it's almost certainly a passport
        # Only override if the other document doesn't have an extremely high score
        other_score = scores.get(best_type_str, 0)
        if other_score < scores["PASSPORT"] * 0.5:  # If passport score is at least half of other
            best_type_str = "PASSPORT"
            # Recalculate confidence for passport
            confidence = 0.90
    
    doc_type = DocumentType(best_type_str)
    return ClassificationResult(doc_type, round(min(confidence, 0.99), 4), scores)


def detect_document_type(tokens: List[OCRToken]) -> Tuple[DocumentType, float]:
    res = classify_tokens(tokens)
    return res.document_type, res.confidence