# schemas.py
"""Pydantic v2 schemas exposed by the API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentType(str, Enum):
    PASSPORT = "PASSPORT"
    AADHAAR = "AADHAAR"
    DRIVING_LICENSE = "DRIVING_LICENSE"
    PAN = "PAN"
    VOTER_ID = "VOTER_ID"
    BUSINESS_CARD = "BUSINESS_CARD"
    GENERIC = "GENERIC"
    UNKNOWN = "UNKNOWN"


class ProcessingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# OCR / extracted field
# ---------------------------------------------------------------------------
class FieldValue(BaseModel):
    """A single extracted field with its confidence score."""

    value: Optional[str] = None
    confidence: float = 0.0

    model_config = ConfigDict(from_attributes=True)


class OCRToken(BaseModel):
    """Raw OCR token as returned by PaddleOCR."""

    text: str
    confidence: float
    bbox: List[List[float]] = Field(default_factory=list)


class OCRMetadata(BaseModel):
    engine: str = "PaddleOCR"
    languages: List[str] = Field(default_factory=list)
    average_confidence: float = 0.0
    token_count: int = 0
    processing_ms: int = 0
    rotation_applied_degrees: int = 0


# ---------------------------------------------------------------------------
# Traveller data (unified structure for passport/aadhaar/generic)
# ---------------------------------------------------------------------------
class TravellerData(BaseModel):
    document_type: DocumentType = DocumentType.UNKNOWN
    document_number: Optional[FieldValue] = None
    full_name: Optional[FieldValue] = None
    given_names: Optional[FieldValue] = None
    surname: Optional[FieldValue] = None
    date_of_birth: Optional[FieldValue] = None
    gender: Optional[FieldValue] = None
    nationality: Optional[FieldValue] = None
    country_code: Optional[FieldValue] = None
    place_of_birth: Optional[FieldValue] = None
    date_of_issue: Optional[FieldValue] = None
    date_of_expiry: Optional[FieldValue] = None
    issuing_authority: Optional[FieldValue] = None
    country: Optional[FieldValue] = None
    address: Optional[FieldValue] = None
    mrz_line1: Optional[FieldValue] = None
    mrz_line2: Optional[FieldValue] = None
    father_name: Optional[FieldValue] = None
    additional_fields: Dict[str, FieldValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Business Card Data (NEW)
# ---------------------------------------------------------------------------
class BusinessCardData(BaseModel):
    """Structured data extracted from a business card."""
    
    full_name: Optional[FieldValue] = None
    company_name: Optional[FieldValue] = None
    designation: Optional[FieldValue] = None
    email: Optional[FieldValue] = None
    mobile_number: Optional[FieldValue] = None
    office_number: Optional[FieldValue] = None
    website: Optional[FieldValue] = None
    address: Optional[FieldValue] = None
    city: Optional[FieldValue] = None
    state: Optional[FieldValue] = None
    country: Optional[FieldValue] = None
    pin_code: Optional[FieldValue] = None
    linkedin: Optional[FieldValue] = None
    qr_code_content: Optional[FieldValue] = None
    additional_fields: Dict[str, FieldValue] = Field(default_factory=dict)
    
    model_config = ConfigDict(from_attributes=True)


class ValidationIssue(BaseModel):
    field: str
    severity: str  # ERROR | WARNING | INFO
    message: str


class DuplicateMatch(BaseModel):
    document_id: str
    match_type: str  # EXACT | FUZZY
    score: float
    matched_on: List[str]
    full_name: Optional[str] = None
    document_number: Optional[str] = None
    document_type: DocumentType = DocumentType.UNKNOWN


# ---------------------------------------------------------------------------
# API responses
# ---------------------------------------------------------------------------
class DocumentResult(BaseModel):
    id: str
    filename: str
    status: ProcessingStatus
    document_type: DocumentType
    classification_confidence: float = 0.0
    overall_confidence: float = 0.0
    traveller: TravellerData
    business_card: Optional[BusinessCardData] = None
    ocr_metadata: OCRMetadata
    validation: List[ValidationIssue] = Field(default_factory=list)
    duplicates: List[DuplicateMatch] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BatchProcessResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    results: List[DocumentResult]


class DocumentListResponse(BaseModel):
    total: int
    items: List[DocumentResult]


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    ocr_engine: str
    ocr_ready: bool
    languages: List[str]
    database: str
    timestamp: datetime


class MessageResponse(BaseModel):
    message: str
    detail: Optional[Any] = None