# passport.py
"""Passport extractor - parses OCR tokens into a structured TravellerData.

Uses TD3 MRZ (Machine Readable Zone) when detectable and falls back to
label-based extraction from the visual inspection zone (VIZ).
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional, Tuple

from ..schemas import DocumentType, FieldValue, OCRToken, TravellerData

# Initialize logger at top
logger = logging.getLogger(__name__)

# ICAO 9303 TD3 MRZ patterns - tolerant to OCR variations
_MRZ_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")

# Country code to country name mapping
_COUNTRY_NAMES = {
    "IND": "INDIA", "USA": "UNITED STATES", "GBR": "UNITED KINGDOM",
    "CAN": "CANADA", "AUS": "AUSTRALIA", "SGP": "SINGAPORE", "DEU": "GERMANY",
    "FRA": "FRANCE", "JPN": "JAPAN", "ARE": "UNITED ARAB EMIRATES",
    "CHN": "CHINA", "PAK": "PAKISTAN", "BGD": "BANGLADESH", "NPL": "NEPAL",
    "LKA": "SRI LANKA",
}

# OCR confusion pairs - kept for reference but not used globally
# We apply corrections field-by-field instead
_OCR_CONFUSIONS = {
    'O': '0',
    'o': '0',
    'I': '1',
    'i': '1',
    'l': '1',
    'B': '8',
    'b': '8',
    'S': '5',
    's': '5',
    'G': '6',
    'g': '6',
}


# ---------------------------------------------------------------------------
# MRZ Check Digit Validation (ICAO 9303)
# ---------------------------------------------------------------------------
def _mrz_check_digit(value: str) -> Optional[bool]:
    """Validate MRZ check digit using ICAO 9303 algorithm.
    
    Args:
        value: String containing the field and check digit
    
    Returns:
        True if valid, False if invalid, None if can't validate
    """
    if not value or len(value) < 2:
        return None
    
    # Extract check digit (last character)
    check_digit = value[-1]
    if check_digit == '<':
        check_digit = '0'
    elif not check_digit.isdigit():
        return None
    
    # Calculate expected check digit
    weights = [7, 3, 1]
    total = 0
    
    for i, char in enumerate(value[:-1]):
        if char.isdigit():
            val = int(char)
        elif char.isalpha():
            val = ord(char.upper()) - 55  # A=10, B=11, ..., Z=35
        elif char == '<':
            val = 0
        else:
            return None
        
        total += val * weights[i % 3]
    
    expected = total % 10
    return expected == int(check_digit)


def _parse_mrz_date(date_str: str, kind: str) -> Optional[str]:
    """Convert MRZ YYMMDD to ISO date string.
    
    Args:
        date_str: 6-character date string (YYMMDD)
        kind: 'dob' or 'expiry' to determine century handling
    
    Returns:
        ISO date string (YYYY-MM-DD) or None if invalid
    """
    if not date_str or len(date_str) != 6:
        return None
    
    # Replace any non-digit characters
    date_str = ''.join(c for c in date_str if c.isdigit())
    if len(date_str) != 6:
        return None
    
    try:
        yy, mm, dd = int(date_str[0:2]), int(date_str[2:4]), int(date_str[4:6])
        
        # Validate month and day
        if not (1 <= mm <= 12) or not (1 <= dd <= 31):
            return None
        
        # Determine century
        if kind == 'dob':
            # Date of Birth: no one is > 100 years old and not born in future
            current_year = date.today().year
            current_century = (current_year // 100) * 100
            
            # If year is > current year + 1, assume previous century
            if yy > (current_year % 100) + 1:
                year = current_century - 100 + yy
            else:
                year = current_century + yy
        else:
            # Expiry: always in the future within same century
            year = 2000 + yy
        
        # Validate the date exists
        parsed = date(year, mm, dd)
        return parsed.isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Field-Aware MRZ Normalization
# ---------------------------------------------------------------------------
def _normalize_mrz_character(char: str, field_type: str) -> str:
    """Normalize a single MRZ character based on field type.
    
    Args:
        char: The character to normalize
        field_type: 'alpha' for alphabetic fields, 'numeric' for numeric fields,
                   'alphanumeric' for mixed fields, 'gender' for gender field
    
    Returns:
        Normalized character
    """
    char = char.upper()
    
    if field_type == 'alpha':
        # Alphabetic fields: only letters and '<' are valid
        if char.isalpha():
            return char
        # Convert digit-like characters to their letter equivalents
        if char == '0':
            return 'O'
        if char == '1':
            return 'I'
        if char == '8':
            return 'B'
        if char == '5':
            return 'S'
        if char == '6':
            return 'G'
        return '<'
    
    elif field_type == 'numeric':
        # Numeric fields: only digits and '<' are valid
        if char.isdigit():
            return char
        # Convert letter-like characters to their digit equivalents
        if char in ('O', 'o'):
            return '0'
        if char in ('I', 'l'):
            return '1'
        if char in ('B', 'b'):
            return '8'
        if char in ('S', 's'):
            return '5'
        if char in ('G', 'g'):
            return '6'
        return '<'
    
    elif field_type == 'alphanumeric':
        # Alphanumeric fields: letters, digits, and '<' are valid
        if char.isalpha() or char.isdigit():
            return char
        return '<'
    
    elif field_type == 'gender':
        # Gender field: only M, F, or '<'
        if char in ('M', 'F'):
            return char
        return '<'
    
    else:
        # Default: keep only valid MRZ characters
        return char if char in _MRZ_CHARS else '<'


def _normalize_mrz_line(text: str, field_types: Optional[List[str]] = None) -> Optional[str]:
    """Clean and normalize an MRZ line, correcting common OCR errors.
    
    Args:
        text: The raw OCR text
        field_types: List of field types for each character position
    
    Returns:
        Normalized MRZ line or None if invalid
    """
    if not text:
        return None
    
    # Remove whitespace
    cleaned = ''.join(text.split())
    
    # Remove invalid characters
    if field_types:
        # Apply field-aware normalization
        normalized = []
        for i, c in enumerate(cleaned):
            if i < len(field_types):
                normalized.append(_normalize_mrz_character(c, field_types[i]))
            else:
                # For characters beyond field definitions, keep only valid MRZ chars
                if c.upper() in _MRZ_CHARS:
                    normalized.append(c.upper())
                else:
                    normalized.append('<')
        cleaned = ''.join(normalized)
    else:
        # Legacy mode: simple cleanup without field awareness
        # Only keep characters that are valid in MRZ
        cleaned = ''.join(c for c in cleaned.upper() if c in _MRZ_CHARS)
    
    # If line is too short, it's not a valid MRZ line
    if len(cleaned) < 30:
        return None
    
    # Pad or truncate to 44 characters for TD3
    if len(cleaned) < 44:
        # If it's close to 44, try to reconstruct by adding '<' where missing
        # But only if we have the first character (document type) and country code
        if len(cleaned) >= 30 and cleaned.startswith('P'):
            # Try to pad with '<' to reach 44
            cleaned = cleaned.ljust(44, '<')
    
    # Return only if we have at least 30 characters (minimum for a valid MRZ)
    return cleaned[:44] if len(cleaned) >= 30 else None


def _get_line1_field_types() -> List[str]:
    """Return field types for MRZ Line 1 positions."""
    # Positions 0-44
    # 0: Document Type (alpha)
    # 1: Document Subtype (alpha)
    # 2-4: Issuing Country (alpha)
    # 5-44: Name field (alpha)
    types = ['alpha', 'alpha']  # positions 0-1
    for _ in range(3):  # positions 2-4
        types.append('alpha')
    for _ in range(40):  # positions 5-44
        types.append('alpha')
    return types


def _get_line2_field_types() -> List[str]:
    """Return field types for MRZ Line 2 positions."""
    types = []
    # 0-8: Document Number (alphanumeric)
    for _ in range(9):
        types.append('alphanumeric')
    # 9: Document Number Check Digit (numeric)
    types.append('numeric')
    # 10-12: Nationality (alpha)
    for _ in range(3):
        types.append('alpha')
    # 13-18: Date of Birth (numeric)
    for _ in range(6):
        types.append('numeric')
    # 19: DOB Check Digit (numeric)
    types.append('numeric')
    # 20: Gender (gender)
    types.append('gender')
    # 21-26: Expiry Date (numeric)
    for _ in range(6):
        types.append('numeric')
    # 27: Expiry Check Digit (numeric)
    types.append('numeric')
    # 28-41: Optional Data (alphanumeric)
    for _ in range(14):
        types.append('alphanumeric')
    # 42: Optional Data Check Digit (numeric)
    types.append('numeric')
    # 43: Composite Check Digit (numeric)
    types.append('numeric')
    return types


def _find_mrz_lines(tokens: List[OCRToken]) -> Tuple[Optional[str], Optional[str]]:
    """Find the two 44-char lines of a TD3 MRZ (ICAO 9303).
    
    Uses tolerant matching to handle OCR errors.
    
    Returns:
        Tuple of (line1, line2) or (None, None) if not found
    """
    candidates = []
    
    # First pass: collect potential MRZ lines with field-aware normalization
    for tok in tokens:
        # Try line 1 normalization first
        line1_types = _get_line1_field_types()
        normalized1 = _normalize_mrz_line(tok.text, line1_types)
        if normalized1 and normalized1.startswith('P'):
            candidates.append(('line1', normalized1, tok.confidence))
            continue
        
        # Try line 2 normalization
        line2_types = _get_line2_field_types()
        normalized2 = _normalize_mrz_line(tok.text, line2_types)
        if normalized2 and not normalized2.startswith('P'):
            candidates.append(('line2', normalized2, tok.confidence))
            continue
        
        # Try legacy normalization as fallback
        normalized_legacy = _normalize_mrz_line(tok.text)
        if normalized_legacy and len(normalized_legacy) >= 30:
            if normalized_legacy.startswith('P'):
                candidates.append(('line1', normalized_legacy, tok.confidence))
            else:
                candidates.append(('line2', normalized_legacy, tok.confidence))
    
    # Find line 1: must start with 'P'
    line1 = None
    line2 = None
    line1_confidence = 0.0
    line2_confidence = 0.0
    
    # Sort by confidence (highest first)
    candidates.sort(key=lambda x: x[2], reverse=True)
    
    for line_type, content, conf in candidates:
        if line_type == 'line1' and not line1:
            # Verify line1 has reasonable structure
            if content.startswith('P') and len(content) >= 30:
                line1 = content
                line1_confidence = conf
        elif line_type == 'line2' and not line2:
            # Verify line2 has reasonable structure
            if not content.startswith('P') and len(content) >= 30:
                line2 = content
                line2_confidence = conf
    
    # If we found line1 but no line2, try to find any other line as line2
    if line1 and not line2:
        for line_type, content, conf in candidates:
            if content != line1 and not content.startswith('P') and len(content) >= 30:
                line2 = content
                line2_confidence = conf
                break
    
    # Re-normalize found lines with field-aware corrections
    if line1:
        line1_types = _get_line1_field_types()
        line1 = _normalize_mrz_line(line1, line1_types)
        if not line1 or not line1.startswith('P'):
            line1 = None
    
    if line2:
        line2_types = _get_line2_field_types()
        line2 = _normalize_mrz_line(line2, line2_types)
        if not line2 or line2.startswith('P'):
            line2 = None
    
    # Ensure we have both lines before returning
    if line1 and line2:
        logger.debug("MRZ lines found with field-aware normalization")
        return line1, line2
    
    return None, None


# ---------------------------------------------------------------------------
# MRZ Parsing (ICAO 9303 TD3)
# ---------------------------------------------------------------------------
def _parse_mrz_line1(line1: str) -> dict:
    """Parse TD3 MRZ Line 1.
    
    ICAO 9303 TD3 positions (0-indexed):
    0: Document Type (P)
    1: Document Subtype (usually <)
    2-4: Issuing Country (3 letters)
    5-44: Name field (surname << given names)
    """
    result = {}
    
    try:
        # Issuing country (positions 2-4)
        country = line1[2:5].replace('<', '')
        if country and len(country) == 3 and country.isalpha():
            result['issuing_country'] = country
        
        # Name field (positions 5-44)
        name_field = line1[5:] if len(line1) > 5 else ''
        if name_field:
            # Split on '<<' (separator between surname and given names)
            if '<<' in name_field:
                surname_part, given_part = name_field.split('<<', 1)
            else:
                # Try to find where surname ends (first word or first '<')
                parts = name_field.split('<')
                surname_part = parts[0] if parts else ''
                given_part = ''.join(parts[1:]) if len(parts) > 1 else ''
            
            # Clean surname (replace '<' with spaces)
            surname = surname_part.replace('<', ' ').strip()
            # Keep only alphabetic characters and spaces for names
            surname = ''.join(c for c in surname if c.isalpha() or c.isspace())
            if surname:
                result['surname'] = surname
            
            # Clean given names (replace '<' with spaces)
            given = given_part.replace('<', ' ').strip()
            # Keep only alphabetic characters and spaces for names
            given = ''.join(c for c in given if c.isalpha() or c.isspace())
            if given:
                result['given_names'] = given
            
            # Full name (surname first, then given names, as per ICAO)
            if surname and given:
                result['full_name'] = f"{surname} {given}"
            elif surname:
                result['full_name'] = surname
            elif given:
                result['full_name'] = given
            
    except (IndexError, ValueError) as e:
        logger.debug("MRZ line 1 parsing error: %s", e)
    
    return result


def _parse_mrz_line2(line2: str) -> dict:
    """Parse TD3 MRZ Line 2.
    
    ICAO 9303 TD3 positions (0-indexed):
    0-8: Document Number (9 digits/letters)
    9: Document Number Check Digit
    10-12: Nationality (3 letters)
    13-18: Date of Birth (YYMMDD)
    19: DOB Check Digit
    20: Gender (M/F)
    21-26: Date of Expiry (YYMMDD)
    27: Expiry Check Digit
    28-41: Optional Data (14 chars)
    42: Optional Data Check Digit
    43: Composite Check Digit
    """
    result = {}
    
    try:
        # Ensure line2 is long enough
        if len(line2) < 44:
            line2 = line2.ljust(44, '<')
        
        # Document Number (positions 0-8) + Check Digit (position 9)
        doc_number_raw = line2[0:10]
        doc_number = doc_number_raw[:-1].replace('<', '')
        # Keep only alphanumeric characters for document number
        doc_number = ''.join(c for c in doc_number if c.isalnum())
        if doc_number:
            result['document_number_raw'] = doc_number_raw
            result['document_number'] = doc_number
        
        # Nationality (positions 10-12)
        nationality = line2[10:13].replace('<', '')
        if nationality and len(nationality) == 3 and nationality.isalpha():
            result['nationality'] = nationality
        
        # Date of Birth (positions 13-18) + Check Digit (position 19)
        dob_raw = line2[13:20]
        dob_value = dob_raw[:-1]  # Remove check digit
        if dob_value and dob_value != '<<<<<<':
            # Extract only digits for date
            dob_str = ''.join(c for c in dob_value if c.isdigit())
            if len(dob_str) == 6:
                result['date_of_birth'] = _parse_mrz_date(dob_str, 'dob')
                result['dob_raw'] = dob_raw
        
        # Gender (position 20)
        gender = line2[20]
        if gender in ('M', 'F'):
            result['gender'] = gender
        
        # Date of Expiry (positions 21-26) + Check Digit (position 27)
        expiry_raw = line2[21:28]
        expiry_value = expiry_raw[:-1]  # Remove check digit
        if expiry_value and expiry_value != '<<<<<<':
            expiry_str = ''.join(c for c in expiry_value if c.isdigit())
            if len(expiry_str) == 6:
                result['date_of_expiry'] = _parse_mrz_date(expiry_str, 'expiry')
                result['expiry_raw'] = expiry_raw
        
        # Optional Data (positions 28-41) + Check Digit (position 42)
        optional_raw = line2[28:43]
        if optional_raw:
            result['optional_data_raw'] = optional_raw
        
        # Composite Check Digit (position 43)
        if len(line2) > 43:
            result['composite_raw'] = line2[43]
        
        # Validate check digits where possible
        if 'document_number_raw' in result:
            result['document_number_valid'] = _mrz_check_digit(result['document_number_raw'])
        if 'dob_raw' in result:
            result['dob_valid'] = _mrz_check_digit(result['dob_raw'])
        if 'expiry_raw' in result:
            result['expiry_valid'] = _mrz_check_digit(result['expiry_raw'])
        
    except (IndexError, ValueError) as e:
        logger.debug("MRZ line 2 parsing error: %s", e)
    
    return result


# ---------------------------------------------------------------------------
# Full MRZ Extraction (ICAO 9303 compliant)
# ---------------------------------------------------------------------------
def _extract_from_mrz(line1: str, line2: str) -> dict:
    """Extract all data from MRZ lines according to ICAO 9303."""
    mrz_data = {}
    
    # Parse Line 1
    line1_data = _parse_mrz_line1(line1)
    mrz_data.update(line1_data)
    
    # Parse Line 2
    line2_data = _parse_mrz_line2(line2)
    mrz_data.update(line2_data)
    
    # Add full MRZ lines for reference
    mrz_data['mrz_line1'] = line1
    mrz_data['mrz_line2'] = line2
    
    return mrz_data


# ---------------------------------------------------------------------------
# OCR/Viz Fallback for fields not in MRZ
# ---------------------------------------------------------------------------
def _find_label_value(tokens: List[OCRToken], labels: List[str], max_distance: int = 3) -> Optional[Tuple[str, float]]:
    """Find value following a label in OCR text."""
    for i, tok in enumerate(tokens):
        text_lower = tok.text.lower()
        for label in labels:
            if label in text_lower:
                # Check if value is on same line after colon
                if ':' in tok.text:
                    parts = tok.text.split(':', 1)
                    if len(parts) > 1:
                        val = parts[1].strip()
                        if val and len(val) > 2:
                            return val, tok.confidence
                
                # Look ahead for the value
                for j in range(1, max_distance + 1):
                    if i + j < len(tokens):
                        nxt = tokens[i + j]
                        # Skip noise tokens (short, single chars, etc.)
                        text = nxt.text.strip()
                        if len(text) > 2 and not text.isdigit():
                            return text, min(tok.confidence, nxt.confidence)
    
    return None


def _extract_viz_fields(tokens: List[OCRToken]) -> dict:
    """Extract fields not present in MRZ from VIZ.
    
    Only extracts fields that exist in TravellerData schema.
    Other fields are stored in additional_fields.
    """
    viz_data = {}
    
    # Address - exists in TravellerData
    address_result = _find_label_value(tokens, ['address', 'residing at', 'permanent address'])
    if address_result:
        viz_data['address'] = address_result[0]
    
    # Father/Guardian - exists in TravellerData
    father_result = _find_label_value(tokens, ['father', "father's name", 's/o', 'd/o', 'guardian'])
    if father_result:
        viz_data['father_name'] = father_result[0]
    
    # Date of Issue - exists in TravellerData
    issue_result = _find_label_value(tokens, ['date of issue', 'issued on', 'issue date'])
    if issue_result:
        viz_data['date_of_issue'] = issue_result[0]
    
    # Place of Birth - exists in TravellerData
    pob_result = _find_label_value(tokens, ['place of birth', 'born at'])
    if pob_result:
        viz_data['place_of_birth'] = pob_result[0]
    
    # Issuing Authority - exists in TravellerData
    authority_result = _find_label_value(tokens, ['issuing authority', 'issued by', 'authority'])
    if authority_result:
        viz_data['issuing_authority'] = authority_result[0]
    
    # These fields don't exist in TravellerData, store them in additional_fields
    # Mother
    mother_result = _find_label_value(tokens, ['mother', "mother's name"])
    if mother_result:
        viz_data['_extra_mother_name'] = mother_result[0]
    
    # Spouse
    spouse_result = _find_label_value(tokens, ['spouse', "spouse's name"])
    if spouse_result:
        viz_data['_extra_spouse_name'] = spouse_result[0]
    
    # Place of Issue (not in TravellerData schema)
    place_result = _find_label_value(tokens, ['place of issue', 'issued at'])
    if place_result:
        viz_data['_extra_place_of_issue'] = place_result[0]
    
    return viz_data


# ---------------------------------------------------------------------------
# VIZ-Only Fallback (when no MRZ found)
# ---------------------------------------------------------------------------
def _extract_viz_only(tokens: List[OCRToken]) -> dict:
    """Fallback extraction from VIZ when MRZ is not detected."""
    viz_data = {}
    
    # Try to extract passport number from VIZ
    doc_result = _find_label_value(tokens, ['passport no', 'passport number', 'no.', 'number'])
    if doc_result:
        viz_data['document_number'] = doc_result[0]
    
    # Try to extract name from VIZ
    name_result = _find_label_value(tokens, ['name', 'full name', 'surname', 'given name'])
    if name_result:
        viz_data['full_name'] = name_result[0]
    
    # Try to extract date of birth
    dob_result = _find_label_value(tokens, ['date of birth', 'dob', 'birth'])
    if dob_result:
        viz_data['date_of_birth'] = dob_result[0]
    
    # Try to extract nationality
    nat_result = _find_label_value(tokens, ['nationality', 'citizen of'])
    if nat_result:
        viz_data['nationality'] = nat_result[0]
    
    # Try to extract gender
    gender_result = _find_label_value(tokens, ['sex', 'gender'])
    if gender_result:
        viz_data['gender'] = gender_result[0]
    
    # Additional VIZ fields that exist in TravellerData
    # Address
    address_result = _find_label_value(tokens, ['address', 'residing at', 'permanent address'])
    if address_result:
        viz_data['address'] = address_result[0]
    
    # Father/Guardian
    father_result = _find_label_value(tokens, ['father', "father's name", 's/o', 'd/o', 'guardian'])
    if father_result:
        viz_data['father_name'] = father_result[0]
    
    # Date of Issue
    issue_result = _find_label_value(tokens, ['date of issue', 'issued on', 'issue date'])
    if issue_result:
        viz_data['date_of_issue'] = issue_result[0]
    
    # Place of Birth
    pob_result = _find_label_value(tokens, ['place of birth', 'born at'])
    if pob_result:
        viz_data['place_of_birth'] = pob_result[0]
    
    # Issuing Authority
    authority_result = _find_label_value(tokens, ['issuing authority', 'issued by', 'authority'])
    if authority_result:
        viz_data['issuing_authority'] = authority_result[0]
    
    # Extra fields (not in TravellerData schema)
    mother_result = _find_label_value(tokens, ['mother', "mother's name"])
    if mother_result:
        viz_data['_extra_mother_name'] = mother_result[0]
    
    spouse_result = _find_label_value(tokens, ['spouse', "spouse's name"])
    if spouse_result:
        viz_data['_extra_spouse_name'] = spouse_result[0]
    
    place_result = _find_label_value(tokens, ['place of issue', 'issued at'])
    if place_result:
        viz_data['_extra_place_of_issue'] = place_result[0]
    
    return viz_data


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------
def extract(tokens: List[OCRToken]) -> TravellerData:
    """Extract passport data following ICAO 9303 standards.
    
    Priority:
    1. MRZ data (primary source for document number, name, nationality, etc.)
    2. VIZ data for fields not in MRZ (address, father, mother, spouse)
    3. VIZ-only fallback if no MRZ found
    """
    data = TravellerData(document_type=DocumentType.PASSPORT)
    
    # Step 1: Detect and extract MRZ with field-aware normalization
    line1, line2 = _find_mrz_lines(tokens)
    
    if line1 and line2:
        logger.info("MRZ detected successfully with field-aware normalization")
        
        # Parse MRZ according to ICAO 9303
        mrz_data = _extract_from_mrz(line1, line2)
        
        # Store MRZ lines for reference
        data.mrz_line1 = FieldValue(value=line1, confidence=0.95)
        data.mrz_line2 = FieldValue(value=line2, confidence=0.95)
        
        # Map MRZ fields to TravellerData - only fields that exist in the schema
        field_mappings = [
            ('document_number', 'document_number'),
            ('issuing_country', 'country_code'),
            ('nationality', 'nationality'),
            ('surname', 'surname'),
            ('given_names', 'given_names'),
            ('full_name', 'full_name'),
            ('date_of_birth', 'date_of_birth'),
            ('date_of_expiry', 'date_of_expiry'),
            ('gender', 'gender'),
        ]
        
        # Populate data from MRZ
        for mrz_field, data_field in field_mappings:
            value = mrz_data.get(mrz_field)
            if value and value != '<' and value != '<<<<<<':
                # Calculate confidence based on check digit validation
                confidence = 0.95
                
                # Reduce confidence if check digit validation fails
                valid_key = f'{mrz_field}_valid'
                if mrz_data.get(valid_key) is False:
                    confidence = 0.80
                    logger.debug("Check digit validation failed for %s", mrz_field)
                
                # Special handling for country codes
                if mrz_field == 'issuing_country' and value:
                    # Store country code
                    setattr(data, data_field, FieldValue(value=value, confidence=confidence))
                    # Derive country name from code
                    country_name = _COUNTRY_NAMES.get(value.upper())
                    if country_name:
                        data.country = FieldValue(value=country_name, confidence=confidence)
                else:
                    setattr(data, data_field, FieldValue(value=value, confidence=confidence))
        
        # Ensure full_name is set from surname + given names if not available
        if not data.full_name and data.surname and data.given_names:
            full = f"{data.surname.value} {data.given_names.value}".strip()
            if full:
                data.full_name = FieldValue(value=full, confidence=0.90)
        
        # Step 2: Extract VIZ fields not in MRZ
        viz_data = _extract_viz_fields(tokens)
        
        # Add VIZ fields to data (only if not already set by MRZ and field exists in schema)
        if 'address' in viz_data and not data.address:
            data.address = FieldValue(value=viz_data['address'], confidence=0.75)
        if 'father_name' in viz_data and not data.father_name:
            data.father_name = FieldValue(value=viz_data['father_name'], confidence=0.70)
        if 'date_of_issue' in viz_data and not data.date_of_issue:
            data.date_of_issue = FieldValue(value=viz_data['date_of_issue'], confidence=0.75)
        if 'place_of_birth' in viz_data and not data.place_of_birth:
            data.place_of_birth = FieldValue(value=viz_data['place_of_birth'], confidence=0.75)
        if 'issuing_authority' in viz_data and not data.issuing_authority:
            data.issuing_authority = FieldValue(value=viz_data['issuing_authority'], confidence=0.75)
        
        # Store extra fields (not in TravellerData schema) in additional_fields
        extra_fields = {}
        if '_extra_mother_name' in viz_data:
            extra_fields['mother_name'] = FieldValue(value=viz_data['_extra_mother_name'], confidence=0.70)
        if '_extra_spouse_name' in viz_data:
            extra_fields['spouse_name'] = FieldValue(value=viz_data['_extra_spouse_name'], confidence=0.70)
        if '_extra_place_of_issue' in viz_data:
            extra_fields['place_of_issue'] = FieldValue(value=viz_data['_extra_place_of_issue'], confidence=0.75)
        
        if extra_fields:
            data.additional_fields.update(extra_fields)
    
    else:
        # No MRZ found, fall back to VIZ extraction
        logger.info("No valid MRZ found, using VIZ fallback extraction")
        viz_data = _extract_viz_only(tokens)
        
        # Map VIZ fields to TravellerData (only fields that exist in schema)
        if 'document_number' in viz_data:
            data.document_number = FieldValue(value=viz_data['document_number'], confidence=0.70)
        if 'full_name' in viz_data:
            data.full_name = FieldValue(value=viz_data['full_name'], confidence=0.70)
        if 'date_of_birth' in viz_data:
            data.date_of_birth = FieldValue(value=viz_data['date_of_birth'], confidence=0.70)
        if 'nationality' in viz_data:
            data.nationality = FieldValue(value=viz_data['nationality'], confidence=0.70)
        if 'gender' in viz_data:
            data.gender = FieldValue(value=viz_data['gender'], confidence=0.70)
        if 'address' in viz_data:
            data.address = FieldValue(value=viz_data['address'], confidence=0.70)
        if 'father_name' in viz_data:
            data.father_name = FieldValue(value=viz_data['father_name'], confidence=0.65)
        if 'date_of_issue' in viz_data:
            data.date_of_issue = FieldValue(value=viz_data['date_of_issue'], confidence=0.70)
        if 'place_of_birth' in viz_data:
            data.place_of_birth = FieldValue(value=viz_data['place_of_birth'], confidence=0.70)
        if 'issuing_authority' in viz_data:
            data.issuing_authority = FieldValue(value=viz_data['issuing_authority'], confidence=0.70)
        
        # Store extra fields (not in TravellerData schema) in additional_fields
        extra_fields = {}
        if '_extra_mother_name' in viz_data:
            extra_fields['mother_name'] = FieldValue(value=viz_data['_extra_mother_name'], confidence=0.65)
        if '_extra_spouse_name' in viz_data:
            extra_fields['spouse_name'] = FieldValue(value=viz_data['_extra_spouse_name'], confidence=0.65)
        if '_extra_place_of_issue' in viz_data:
            extra_fields['place_of_issue'] = FieldValue(value=viz_data['_extra_place_of_issue'], confidence=0.70)
        
        if extra_fields:
            data.additional_fields.update(extra_fields)
    
    return data