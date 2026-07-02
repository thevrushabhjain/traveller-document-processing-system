"""Central parser that dispatches to the right extractor and normalises data."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from dateutil import parser as dateparser

from ..schemas import DocumentType, FieldValue, OCRToken, TravellerData
from ..extractors import aadhaar as aadhaar_extractor
from ..extractors import driving_license as driving_license_extractor
from ..extractors import generic as generic_extractor
from ..extractors import pan as pan_extractor
from ..extractors import passport as passport_extractor
from ..extractors import voter_id as voter_id_extractor

logger = logging.getLogger(__name__)

_EXTRACTORS = {
    DocumentType.PASSPORT: passport_extractor.extract,
    DocumentType.AADHAAR: aadhaar_extractor.extract,
    DocumentType.DRIVING_LICENSE: driving_license_extractor.extract,
    DocumentType.PAN: pan_extractor.extract,
    DocumentType.VOTER_ID: voter_id_extractor.extract,
}


def parse(tokens: List[OCRToken], document_type: DocumentType) -> TravellerData:
    """Return a normalised :class:`TravellerData` for the given OCR tokens."""
    extractor = _EXTRACTORS.get(document_type, generic_extractor.extract)
    data = extractor(tokens)

    _normalize_dates(data)
    _normalize_gender(data)
    _normalize_names(data)
    _normalize_document_number(data)
    return data


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------
_ISO_FORMAT = "%Y-%m-%d"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9A-Za-z/\-\. ]+", "", raw).strip()
    if not cleaned:
        return None
    if _ISO_DATE_RE.match(cleaned):
        # Already normalised (e.g. computed directly from an MRZ field) -
        # do not re-run it through the dayfirst heuristic below, which
        # would misinterpret the unambiguous YYYY-MM-DD order.
        return cleaned
    try:
        # dayfirst=True handles common Indian & European formats (dd/mm/yyyy)
        dt = dateparser.parse(cleaned, dayfirst=True, fuzzy=True)
        return dt.strftime(_ISO_FORMAT)
    except (ValueError, OverflowError, dateparser.ParserError):
        return None


def _normalize_dates(data: TravellerData) -> None:
    for attr in ("date_of_birth", "date_of_issue", "date_of_expiry"):
        fv: Optional[FieldValue] = getattr(data, attr)
        if fv and fv.value:
            iso = _normalize_date(fv.value)
            if iso:
                fv.value = iso


def _normalize_gender(data: TravellerData) -> None:
    if not data.gender or not data.gender.value:
        return
    val = data.gender.value.strip().upper()
    mapping = {"M": "M", "MALE": "M", "पुरुष": "M", "F": "F", "FEMALE": "F", "महिला": "F"}
    data.gender.value = mapping.get(val, val[:1] if val[:1] in {"M", "F"} else val)


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _normalize_names(data: TravellerData) -> None:
    for attr in ("full_name", "given_names", "surname", "father_name", "nationality", "country", "issuing_authority"):
        fv = getattr(data, attr, None)
        if fv and fv.value:
            # Insert a space at genuine lower->upper case transitions before
            # uppercasing - OCR on mixed-case documents (e.g. Aadhaar
            # e-letters) sometimes drops the space between two words but
            # keeps each word's own capitalisation (e.g. "VrushabhKamal"),
            # which is a reliable signal for where the words split.
            spaced = _CAMEL_BOUNDARY_RE.sub(" ", fv.value)
            fv.value = re.sub(r"\s+", " ", spaced).strip().upper()


def _normalize_document_number(data: TravellerData) -> None:
    if data.document_number and data.document_number.value:
        val = re.sub(r"\s+", "", data.document_number.value).upper()
        data.document_number.value = val
