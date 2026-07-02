# backend/app/extractors/business_card.py
"""Business Card extractor - extracts contact information from business/visiting cards."""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

from ..schemas import BusinessCardData, FieldValue, OCRToken

logger = logging.getLogger(__name__)

# --- Regular expressions for field extraction ---

# Email - strict but handles common formats
_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

# Phone - supports Indian and international formats
_PHONE_RE = re.compile(
    r'(?:\+?\d{1,3}[-.\s]?)?'  # Country code (optional)
    r'(?:\(?\d{2,3}\)?[-.\s]?)'  # Area/STD code (optional)
    r'\d{3,4}[-.\s]?'  # Exchange
    r'\d{3,4}'  # Subscriber
    r'(?:[-.\s]?\d{3,4})?'  # Extension (optional)
    r'\b'
)

# Website - only match proper domain patterns
_WEBSITE_RE = re.compile(
    r'(?:https?://)?'  # Optional protocol
    r'(?:www\.)?'  # Optional www
    r'[A-Za-z0-9-]+'  # Domain name
    r'\.'  # Dot
    r'(?:com|in|org|net|io|ai|co|uk|us|ca|au|de|fr|jp|cn|br|ru|za|nl|se|no|fi|dk|ch|at|be|nz|sg|hk|my|id|ph|vn|th)'  # TLD
    r'(?:/[A-Za-z0-9-._~:/?#\[\]@!$&\'()*+,;=]*)?'  # Optional path
    r'\b'
)

# LinkedIn
_LINKEDIN_RE = re.compile(
    r'(?:linkedin\.com/in/|linkedin\.com/company/|linkedin\.com/pub/)'
    r'[A-Za-z0-9-]+'
    r'\b'
)

# Social media
_FACEBOOK_RE = re.compile(r'(?:facebook\.com/|fb\.com/)[A-Za-z0-9.]+')
_INSTAGRAM_RE = re.compile(r'(?:instagram\.com/|ig\.me/)[A-Za-z0-9_.]+')
_TWITTER_RE = re.compile(r'(?:twitter\.com/|x\.com/)[A-Za-z0-9_]+')
_YOUTUBE_RE = re.compile(r'(?:youtube\.com/|youtu\.be/)[A-Za-z0-9_-]+')

# PIN code (Indian)
_PINCODE_RE = re.compile(r'\b\d{6}\b')

# GST Number (Indian)
_GST_RE = re.compile(r'\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}\d{1}[A-Z]{1}\d{1}\b')

# WhatsApp/Skype/Fax
_WHATSAPP_RE = re.compile(r'whatsapp[-.\s]*(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}')
_SKYPE_RE = re.compile(r'skype[-.\s]*[A-Za-z0-9_.-]+')
_FAX_RE = re.compile(r'fax[-.\s]*(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}')

# Designation keywords (expanded)
_DESIGNATION_KEYWORDS = [
    'manager', 'director', 'ceo', 'founder', 'consultant', 'engineer',
    'developer', 'analyst', 'associate', 'lead', 'head', 'vp',
    'vice president', 'president', 'cto', 'cfo', 'coo', 'hr',
    'sales', 'marketing', 'operations', 'finance', 'architect',
    'specialist', 'coordinator', 'administrator', 'executive',
    'assistant', 'supervisor', 'designer', 'writer', 'partner',
    'principal', 'staff', 'senior', 'junior', 'intern', 'trainee',
    'officer', 'clerk', 'representative', 'agent', 'advisor',
    'counsel', 'attorney', 'lawyer', 'physician', 'doctor', 'nurse',
    'professor', 'teacher', 'instructor', 'trainer', 'coach'
]

# Name prefixes (expanded)
_NAME_PREFIXES = ['mr.', 'ms.', 'mrs.', 'dr.', 'prof.', 'er.', 'adv.', 'ca.', 'cs.', 'cma.']

# Company suffixes (indicators)
_COMPANY_SUFFIXES = [
    'inc', 'llc', 'ltd', 'corp', 'pvt', 'co.', 'company', 'corporation',
    'limited', 'incorporated', 'llp', 'plc', 'gmbh', 'sa', 'bv', 'nv',
    'holdings', 'group', 'ventures', 'technologies', 'solutions'
]

# Address indicators
_ADDRESS_INDICATORS = ['street', 'road', 'avenue', 'lane', 'drive', 'suite', 'floor', 'building']


def _extract_email(text: str) -> Optional[Tuple[str, float]]:
    """Extract email address from text."""
    matches = _EMAIL_RE.findall(text)
    if matches:
        return matches[0], 0.95
    return None


def _extract_phone_numbers(text: str) -> List[Tuple[str, float]]:
    """Extract phone numbers from text supporting Indian and international formats."""
    phones = []
    matches = _PHONE_RE.findall(text)
    
    for match in matches:
        cleaned = re.sub(r'[^0-9+]', '', match)
        
        # Indian format: 10 digits, starts with 6-9
        if len(cleaned) == 10 and cleaned[0] in '6789':
            phones.append((match, 0.90))
        # Indian format: +91 followed by 10 digits
        elif len(cleaned) == 12 and cleaned.startswith('91') and len(cleaned[2:]) == 10:
            phones.append((match, 0.92))
        # International format: 11-15 digits
        elif 11 <= len(cleaned) <= 15 and cleaned.startswith('+'):
            phones.append((match, 0.88))
        # Indian landline: STD code + number (8-10 digits total)
        elif 8 <= len(cleaned) <= 10 and cleaned[0] in '02':
            phones.append((match, 0.75))
        # Generic phone number
        elif 10 <= len(cleaned) <= 15:
            phones.append((match, 0.70))
    
    # Sort by confidence and deduplicate
    phones.sort(key=lambda x: x[1], reverse=True)
    unique_phones = []
    seen = set()
    for phone, conf in phones:
        if phone not in seen:
            seen.add(phone)
            unique_phones.append((phone, conf))
    
    return unique_phones[:3]  # Max 3 phone numbers


def _extract_website(text: str) -> Optional[Tuple[str, float]]:
    """Extract website URL from text."""
    matches = _WEBSITE_RE.findall(text)
    if matches:
        url = matches[0]
        if not url.startswith(('http://', 'https://')):
            url = f'https://{url}'
        return url, 0.90
    return None


def _extract_social_media(text: str) -> dict:
    """Extract social media profiles from text."""
    result = {}
    
    linkedin = _LINKEDIN_RE.search(text)
    if linkedin:
        result['linkedin'] = linkedin.group(0)
    
    facebook = _FACEBOOK_RE.search(text)
    if facebook:
        result['facebook'] = facebook.group(0)
    
    instagram = _INSTAGRAM_RE.search(text)
    if instagram:
        result['instagram'] = instagram.group(0)
    
    twitter = _TWITTER_RE.search(text)
    if twitter:
        result['twitter'] = twitter.group(0)
    
    youtube = _YOUTUBE_RE.search(text)
    if youtube:
        result['youtube'] = youtube.group(0)
    
    return result


def _extract_gst(text: str) -> Optional[Tuple[str, float]]:
    """Extract GST number from text."""
    match = _GST_RE.search(text)
    if match:
        return match.group(0), 0.90
    return None


def _extract_whatsapp(text: str) -> Optional[Tuple[str, float]]:
    """Extract WhatsApp number from text."""
    match = _WHATSAPP_RE.search(text)
    if match:
        return match.group(0), 0.85
    return None


def _extract_skype(text: str) -> Optional[Tuple[str, float]]:
    """Extract Skype ID from text."""
    match = _SKYPE_RE.search(text)
    if match:
        return match.group(0), 0.85
    return None


def _extract_fax(text: str) -> Optional[Tuple[str, float]]:
    """Extract fax number from text."""
    match = _FAX_RE.search(text)
    if match:
        return match.group(0), 0.80
    return None


def _extract_pincode(text: str) -> Optional[Tuple[str, float]]:
    """Extract PIN code from text."""
    match = _PINCODE_RE.search(text)
    if match:
        return match.group(0), 0.90
    return None


def _extract_name(text_lines: List[str]) -> Optional[Tuple[str, float]]:
    """Extract person's name from business card using layout heuristics.
    
    Heuristics:
    1. Look for name prefixes (Mr., Ms., Dr., etc.)
    2. Check for lines with 2-3 words that are properly capitalized
    3. If a line is followed by a designation, it's likely the name
    4. Name usually appears before contact information
    """
    candidates = []
    
    # Find designation lines to help identify names
    designation_indices = []
    for i, line in enumerate(text_lines):
        lower_line = line.lower()
        if any(kw in lower_line for kw in _DESIGNATION_KEYWORDS[:20]):  # Check common ones first
            designation_indices.append(i)
    
    for idx, line in enumerate(text_lines):
        line = line.strip()
        if not line or len(line) > 50 or len(line.split()) < 2:
            continue
        
        lower_line = line.lower()
        
        # Skip lines that are clearly not names
        if any(kw in lower_line for kw in ['email', 'phone', 'www', 'linkedin', 'facebook', 'twitter', 'instagram']):
            continue
        if '@' in line or 'http' in lower_line:
            continue
        
        has_prefix = any(line.lower().startswith(prefix) for prefix in _NAME_PREFIXES)
        words = line.split()
        
        # Check if it's a proper name
        if len(words) <= 4:
            proper_caps = all(w[0].isupper() for w in words if len(w) > 2)
            all_caps = all(w.isupper() for w in words)
            
            # Check if this line is immediately followed by a designation
            followed_by_designation = (idx + 1) in designation_indices
            
            if has_prefix or proper_caps or all_caps:
                confidence = 0.70
                
                # Boost confidence if followed by designation
                if followed_by_designation:
                    confidence += 0.20
                
                if has_prefix:
                    confidence += 0.10
                if len(words) == 2:
                    confidence += 0.05
                if proper_caps and not all_caps:
                    confidence += 0.05
                
                # Higher confidence if it appears before contact info
                if idx < 3:  # Name usually in first few lines
                    confidence += 0.05
                
                candidates.append((line, min(confidence, 0.95)))
    
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0]
    
    return None


def _extract_designation(text_lines: List[str]) -> Optional[Tuple[str, float]]:
    """Extract job title/designation from text."""
    candidates = []
    
    for idx, line in enumerate(text_lines):
        line = line.strip()
        if not line:
            continue
        
        lower_line = line.lower()
        
        # Skip if it looks like a name or contact info
        if any(kw in lower_line for kw in ['email', 'phone', 'www', 'linkedin', '@']):
            continue
        
        for kw in _DESIGNATION_KEYWORDS:
            if kw in lower_line:
                confidence = 0.80
                
                # Boost confidence for senior/executive roles
                if re.search(r'\b(?:senior|junior|principal|staff|lead|associate|executive) .+ (?:engineer|manager|director)\b', lower_line):
                    confidence += 0.10
                elif re.search(r'\b(?:manager|director|vp|vice president|cto|cfo|coo|ceo|founder|partner|principal)\b', lower_line):
                    confidence += 0.10
                
                # Lower confidence if it's just a single word
                if len(line.split()) == 1:
                    confidence -= 0.10
                
                candidates.append((line, min(confidence, 0.95)))
                break
    
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0]
    
    return None


def _extract_company(text_lines: List[str]) -> Optional[Tuple[str, float]]:
    """Extract company name from text.
    
    Heuristics:
    1. Largest text near top (usually company name)
    2. Above designation (if designation is found)
    3. Below logo (implied by position)
    4. Before contact information
    """
    # First, try to find designation line
    designation_idx = None
    for i, line in enumerate(text_lines):
        lower_line = line.lower()
        if any(kw in lower_line for kw in _DESIGNATION_KEYWORDS[:20]):
            designation_idx = i
            break
    
    candidates = []
    
    # If we found a designation, look for company name above it
    if designation_idx is not None:
        for i in range(max(0, designation_idx - 3), designation_idx):
            if i < len(text_lines):
                line = text_lines[i].strip()
                if line and len(line) < 60 and len(line.split()) <= 4:
                    # Check if it looks like a company name
                    lower_line = line.lower()
                    if not any(kw in lower_line for kw in ['email', 'phone', 'www', 'linkedin', '@']):
                        candidates.append((line, 0.85))
    
    # Also check first few lines for company name
    for i, line in enumerate(text_lines[:4]):
        line = line.strip()
        if not line or len(line) > 60:
            continue
        
        lower_line = line.lower()
        if any(kw in lower_line for kw in ['email', 'phone', 'www', 'linkedin']):
            continue
        
        words = line.split()
        if 1 <= len(words) <= 5:
            is_company_style = False
            
            # ALL CAPS (often company names)
            if all(w.isupper() for w in words if len(w) > 1):
                is_company_style = True
            # Proper Case
            elif all(w[0].isupper() for w in words if len(w) > 2):
                is_company_style = True
            # Contains company suffixes
            elif any(kw in lower_line for kw in _COMPANY_SUFFIXES):
                is_company_style = True
            
            # Check if it has a common company prefix
            elif any(kw in lower_line for kw in ['google', 'microsoft', 'openai', 'infosys', 'tcs', 'zoho', 'nvidia']):
                is_company_style = True
            
            if is_company_style:
                confidence = 0.80 - (i * 0.05)
                if any(kw in lower_line for kw in _COMPANY_SUFFIXES):
                    confidence += 0.10
                # Boost if it appears before designation
                if designation_idx is not None and i < designation_idx:
                    confidence += 0.10
                candidates.append((line, min(confidence, 0.95)))
    
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0]
    
    return None


def _extract_address_components(text_lines: List[str]) -> dict:
    """Extract address, city, state, country from text."""
    result = {}
    
    address_candidates = []
    for idx, line in enumerate(text_lines):
        line = line.strip()
        if not line or len(line) > 100:
            continue
        
        # Skip lines that are clearly not address
        if any(kw in line.lower() for kw in ['email', 'phone', 'www', 'linkedin', 'mobile', 'cell']):
            continue
        if '@' in line or 'http' in line.lower():
            continue
        
        lower_line = line.lower()
        # Check for address indicators
        if any(kw in lower_line for kw in _ADDRESS_INDICATORS):
            address_candidates.append(line)
        elif ',' in line and len(line.split(',')) >= 2:
            address_candidates.append(line)
        # Check for PIN code
        elif _PINCODE_RE.search(line):
            address_candidates.append(line)
    
    if address_candidates:
        # Join address candidates
        address = ' '.join(address_candidates)
        
        # Try to extract city and state
        city_match = re.search(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*,\s*([A-Z]{2})\b', address)
        if city_match:
            result['city'] = city_match.group(1)
            result['state'] = city_match.group(2)
        
        # Try to extract country
        country_keywords = ['india', 'united states', 'usa', 'uk', 'canada', 'australia', 'singapore', 'germany', 'france']
        for country in country_keywords:
            if country in address.lower():
                result['country'] = country.title()
                break
        
        # Extract PIN if present
        pin_match = _PINCODE_RE.search(address)
        if pin_match:
            result['pin_code'] = pin_match.group(0)
        
        result['address'] = address
    
    return result


def extract(tokens: List[OCRToken]) -> BusinessCardData:
    """Extract business card data from OCR tokens."""
    text = " ".join(t.text for t in tokens)
    text_lines = [t.text.strip() for t in tokens if t.text.strip()]
    
    data = BusinessCardData()
    
    # Extract email
    email_result = _extract_email(text)
    if email_result:
        data.email = FieldValue(value=email_result[0], confidence=email_result[1])
    
    # Extract phone numbers
    phones = _extract_phone_numbers(text)
    if phones:
        data.mobile_number = FieldValue(value=phones[0][0], confidence=phones[0][1])
        if len(phones) > 1:
            data.office_number = FieldValue(value=phones[1][0], confidence=phones[1][1])
    
    # Extract website
    website_result = _extract_website(text)
    if website_result:
        data.website = FieldValue(value=website_result[0], confidence=website_result[1])
    
    # Extract social media
    social = _extract_social_media(text)
    for key, value in social.items():
        if key == 'linkedin':
            data.linkedin = FieldValue(value=value, confidence=0.95)
        else:
            data.additional_fields[key] = FieldValue(value=value, confidence=0.85)
    
    # Extract GST
    gst_result = _extract_gst(text)
    if gst_result:
        data.additional_fields['gst_number'] = FieldValue(value=gst_result[0], confidence=gst_result[1])
    
    # Extract WhatsApp
    whatsapp_result = _extract_whatsapp(text)
    if whatsapp_result:
        data.additional_fields['whatsapp'] = FieldValue(value=whatsapp_result[0], confidence=whatsapp_result[1])
    
    # Extract Skype
    skype_result = _extract_skype(text)
    if skype_result:
        data.additional_fields['skype'] = FieldValue(value=skype_result[0], confidence=skype_result[1])
    
    # Extract Fax
    fax_result = _extract_fax(text)
    if fax_result:
        data.additional_fields['fax'] = FieldValue(value=fax_result[0], confidence=fax_result[1])
    
    # Extract PIN code
    pincode_result = _extract_pincode(text)
    if pincode_result:
        data.pin_code = FieldValue(value=pincode_result[0], confidence=pincode_result[1])
    
    # Extract name
    name_result = _extract_name(text_lines)
    if name_result:
        data.full_name = FieldValue(value=name_result[0], confidence=name_result[1])
    
    # Extract designation
    designation_result = _extract_designation(text_lines)
    if designation_result:
        data.designation = FieldValue(value=designation_result[0], confidence=designation_result[1])
    
    # Extract company
    company_result = _extract_company(text_lines)
    if company_result:
        data.company_name = FieldValue(value=company_result[0], confidence=company_result[1])
    
    # Extract address components
    address_data = _extract_address_components(text_lines)
    if 'address' in address_data:
        data.address = FieldValue(value=address_data['address'], confidence=0.75)
    if 'city' in address_data:
        data.city = FieldValue(value=address_data['city'], confidence=0.70)
    if 'state' in address_data:
        data.state = FieldValue(value=address_data['state'], confidence=0.70)
    if 'country' in address_data:
        data.country = FieldValue(value=address_data['country'], confidence=0.75)
    if 'pin_code' in address_data and not data.pin_code:
        data.pin_code = FieldValue(value=address_data['pin_code'], confidence=0.85)
    
    return data