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

# Business Card patterns
_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
_PHONE_RE = re.compile(
    r'(?:\+?\d{1,3}[-.\s]?)?'  # Country code (optional)
    r'(?:\(?\d{2,3}\)?[-.\s]?)'  # Area/STD code (optional)
    r'\d{3,4}[-.\s]?'  # Exchange
    r'\d{3,4}'  # Subscriber
    r'(?:[-.\s]?\d{3,4})?'  # Extension (optional)
    r'\b'
)
_WEBSITE_RE = re.compile(
    r'(?:https?://)?'  # Optional protocol
    r'(?:www\.)?'  # Optional www
    r'[A-Za-z0-9-]+'  # Domain name
    r'\.'  # Dot
    r'(?:com|in|org|net|io|ai|co|uk|us|ca|au|de|fr|jp|cn|br|ru|za|nl|se|no|fi|dk|ch|at|be|nz|sg|hk|my|id|ph|vn|th)'  # TLD
    r'(?:/[A-Za-z0-9-._~:/?#\[\]@!$&\'()*+,;=]*)?'  # Optional path
    r'\b'
)
_LINKEDIN_RE = re.compile(
    r'(?:linkedin\.com/in/|linkedin\.com/company/|linkedin\.com/pub/)'
    r'[A-Za-z0-9-]+'
    r'\b'
)
_QR_RE = re.compile(r'QR|二维码|qrcode', re.IGNORECASE)
_GST_RE = re.compile(r'\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}\d{1}[A-Z]{1}\d{1}\b')

# Social media patterns
_SOCIAL_PATTERNS = [
    r'(?:facebook\.com/|fb\.com/)',
    r'(?:instagram\.com/|ig\.me/)',
    r'(?:twitter\.com/|x\.com/)',
    r'(?:youtube\.com/|youtu\.be/)'
]

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


def _detect_business_card(tokens: List[OCRToken]) -> Tuple[bool, float]:
    """Detect business cards using weighted scoring.
    
    Feature weights:
    - Email: +3
    - Phone: +2
    - Website: +2
    - Designation: +2
    - Company Name: +2
    - LinkedIn: +2
    - QR Code: +5
    - GST Number: +1
    - Social Media: +1 each
    - Layout indicators: +1-2
    
    Threshold: >= 8 points = Business Card
    """
    import logging
    logger = logging.getLogger(__name__)
    
    text = "\n".join(t.text for t in tokens)
    lower = text.lower()
    text_lines = [t.text.strip() for t in tokens if t.text.strip()]
    
    score = 0
    indicators = []
    
    # Email detection (+3)
    if _EMAIL_RE.search(text):
        score += 3
        indicators.append('email')
    
    # Phone detection (+2)
    if _PHONE_RE.search(text):
        score += 2
        indicators.append('phone')
    
    # Website detection (+2)
    if _WEBSITE_RE.search(text):
        score += 2
        indicators.append('website')
    
    # LinkedIn detection (+2)
    if _LINKEDIN_RE.search(text):
        score += 2
        indicators.append('linkedin')
    
    # QR code detection (+5 - strong indicator)
    if _QR_RE.search(text):
        score += 5
        indicators.append('qr_code')
    
    # Designation detection (+2)
    designation_keywords = [
        'manager', 'director', 'ceo', 'founder', 'consultant', 'engineer',
        'developer', 'analyst', 'associate', 'lead', 'head', 'vp',
        'president', 'cto', 'cfo', 'coo', 'partner', 'principal'
    ]
    if any(kw in lower for kw in designation_keywords):
        score += 2
        indicators.append('designation')
    
    # Company name detection (+2)
    # Check for common company patterns
    company_indicators = ['inc', 'llc', 'ltd', 'corp', 'pvt', 'company', 'corporation']
    if any(kw in lower for kw in company_indicators):
        score += 2
        indicators.append('company')
    
    # Also check for ALL CAPS lines that might be company names
    for line in text_lines[:3]:
        words = line.split()
        if len(words) <= 4 and all(w.isupper() for w in words if len(w) > 1):
            if not any(kw in line.lower() for kw in ['email', 'phone', 'www']):
                score += 1
                indicators.append('company_allcaps')
    
    # GST number detection (+1)
    if _GST_RE.search(text):
        score += 1
        indicators.append('gst')
    
    # Social media detection (+1 each)
    social_patterns = [
        r'(?:facebook\.com/|fb\.com/)',
        r'(?:instagram\.com/|ig\.me/)',
        r'(?:twitter\.com/|x\.com/)',
        r'(?:youtube\.com/|youtu\.be/)'
    ]
    for pattern in social_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            score += 1
            indicators.append('social_media')
    
    # Designation with name pattern (+1)
    for line in text_lines[:5]:
        lower_line = line.lower()
        if any(kw in lower_line for kw in ['manager', 'director', 'engineer', 'developer']):
            words = line.split()
            if len(words) >= 2:
                score += 1
                indicators.append('designation_with_name')
                break
    
    # Check for business card layout indicators (+1)
    # Multiple short lines (typical of business cards)
    short_lines = sum(1 for line in text_lines if 5 < len(line.strip()) < 30)
    if short_lines >= 3:
        score += 1
        indicators.append('layout')
    
    # Check for contact section pattern (+1)
    contact_patterns = ['email', 'phone', 'mobile', 'www', 'linkedin']
    contact_hits = sum(1 for pattern in contact_patterns if pattern in lower)
    if contact_hits >= 2:
        score += 1
        indicators.append('contact_section')
    
    # Determine if it's a business card
    is_business_card = score >= 8
    
    # Calculate confidence (cap at 0.95)
    confidence = min(0.95, 0.3 + (score / 30))
    
    if is_business_card:
        logger.debug(
            "Business card detected",
            extra={
                "score": score,
                "indicators": indicators,
                "confidence": round(confidence, 3)
            }
        )
    
    return is_business_card, confidence if is_business_card else 0.0


def classify_tokens(tokens: List[OCRToken]) -> ClassificationResult:
    """Return the most likely document type based on OCR text content."""
    import logging
    logger = logging.getLogger(__name__)
    
    text = "\n".join(t.text for t in tokens)
    lower = text.lower()
    compact = text.replace(" ", "")

    scores = {
        "PASSPORT": 0.0,
        "AADHAAR": 0.0,
        "DRIVING_LICENSE": 0.0,
        "PAN": 0.0,
        "VOTER_ID": 0.0,
        "BUSINESS_CARD": 0.0,
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

    # --- Business Card detection (NEW) ---
    is_business_card, bc_confidence = _detect_business_card(tokens)
    if is_business_card:
        # Scale confidence to score (max 10)
        scores["BUSINESS_CARD"] += bc_confidence * 10

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
        scores["PASSPORT"] += 5.0
    if _PASSPORT_NUMBER_RE.search(text) and "passport" in lower:
        scores["PASSPORT"] += 2.0
    
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
    
    # --- Enhanced confidence calculation ---
    if best_type_str == "PASSPORT" and best_score > 0:
        # Boost confidence for passport if MRZ was detected or if multiple signals present
        if has_mrz:
            confidence = min(0.95, 0.6 + (best_score / 20))
        elif best_score >= 5:
            confidence = min(0.85, 0.4 + (best_score / 15))
        else:
            confidence = min(0.7, 0.3 + (best_score / 10))
        
        if best_score < 1.5:
            confidence = 0.0
            best_type_str = "GENERIC"
    
    elif best_type_str == "BUSINESS_CARD" and best_score > 0:
        # Confidence for business cards
        if best_score >= 8:
            confidence = min(0.95, 0.6 + (best_score / 20))
        elif best_score >= 5:
            confidence = min(0.85, 0.4 + (best_score / 15))
        else:
            confidence = min(0.7, 0.3 + (best_score / 10))
        
        if best_score < 3:
            confidence = 0.0
            best_type_str = "GENERIC"
    
    else:
        # For other document types, use original logic
        if best_score < 1.5 and not has_mrz:
            return ClassificationResult(DocumentType.GENERIC, 0.0, scores)
        confidence = best_score / total if total else 0.0
        confidence = round(min(confidence, 0.99), 4)
    
    # Override if we detected MRZ but classification is not passport
    if has_mrz and best_type_str != "PASSPORT":
        other_score = scores.get(best_type_str, 0)
        if other_score < scores["PASSPORT"] * 0.5:
            best_type_str = "PASSPORT"
            confidence = 0.90
    
    # Log the classification result for debugging
    logger.debug(
        "Classification result",
        extra={
            "document_type": best_type_str,
            "confidence": round(confidence, 4),
            "scores": {k: round(v, 2) for k, v in scores.items() if v > 0}
        }
    )
    
    doc_type = DocumentType(best_type_str)
    return ClassificationResult(doc_type, round(min(confidence, 0.99), 4), scores)


def detect_document_type(tokens: List[OCRToken]) -> Tuple[DocumentType, float]:
    res = classify_tokens(tokens)
    return res.document_type, res.confidence