# api.py
"""FastAPI HTTP routes for document processing."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import aiofiles
import cv2
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .config import get_settings
from .database import ProcessedDocument, db_dialect, get_session
from .schemas import (
    BatchProcessResponse,
    DocumentListResponse,
    DocumentResult,
    DocumentType,
    HealthResponse,
    MessageResponse,
    OCRMetadata,
    ProcessingStatus,
    TravellerData,
)
from .services.classifier import classify_tokens
from .services.duplicate_detector import (
    find_duplicates,
    normalize_doc_number,
    normalize_name,
)
from .services.ocr_service import get_ocr_service
from .services.parser import parse as parse_traveller
from .services.pdf_service import pdf_to_images
from .services.validator import validate as validate_traveller
from .utils.file_utils import (
    SUPPORTED_EXT,
    human_size,
    is_pdf,
    is_supported,
    unique_upload_path,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    ocr = get_ocr_service()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        ocr_engine=ocr.backend,
        ocr_ready=ocr.is_ready(),
        languages=ocr.languages,
        database=db_dialect(),
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Upload + process
# ---------------------------------------------------------------------------
async def _save_upload(file: UploadFile) -> Path:
    """Save uploaded file with size validation and structured logging."""
    start_time = time.time()
    settings = get_settings()
    
    if not file.filename or not is_supported(file.filename):
        logger.warning(
            "Unsupported file type rejected",
            extra={"original_filename": file.filename, "allowed_extensions": sorted(SUPPORTED_EXT)}
        )
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {sorted(SUPPORTED_EXT)}",
        )
    
    dest = unique_upload_path(Path(settings.upload_dir), file.filename)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    size = 0
    
    try:
        async with aiofiles.open(dest, "wb") as fp:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    await fp.close()
                    dest.unlink(missing_ok=True)
                    logger.warning(
                        "File too large rejected",
                        extra={
                            "original_filename": file.filename,
                            "size_mb": size / (1024 * 1024),
                            "max_mb": settings.max_upload_size_mb,
                        }
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large (>{settings.max_upload_size_mb} MB)",
                    )
                await fp.write(chunk)
        
        elapsed = time.time() - start_time
        logger.info(
            "Upload saved successfully",
            extra={
                "stored_filename": dest.name,
                "original_filename": file.filename,
                "size_bytes": size,
                "size_human": human_size(size),
                "elapsed_ms": round(elapsed * 1000, 2),
            }
        )
        return dest
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to save upload",
            extra={"original_filename": file.filename, "error": str(e)},
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to save uploaded file")


def _process_stored_file(session: Session, stored_path: Path, original_filename: str) -> DocumentResult:
    """Full OCR/parse/validate/dedupe pipeline for a single stored file."""
    total_start = time.time()
    doc_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    
    logger.info(
        "Starting document processing",
        extra={
            "doc_id": doc_id,
            "original_filename": original_filename,
            "stored_path": str(stored_path),
        }
    )
    
    record = ProcessedDocument(
        id=doc_id,
        filename=original_filename,
        stored_path=str(stored_path),
        status=ProcessingStatus.PROCESSING.value,
        created_at=created_at,
    )
    session.add(record)
    session.commit()

    try:
        # --- Stage 1: OCR ---
        ocr_stage_start = time.time()
        ocr = get_ocr_service()
        
        # Load pages -> images
        load_start = time.time()
        if is_pdf(stored_path):
            images = pdf_to_images(stored_path)
        else:
            img = cv2.imread(str(stored_path), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"Cannot decode image: {stored_path.name}")
            images = [img]
        
        load_elapsed = time.time() - load_start
        logger.debug(
            "Image loading complete",
            extra={
                "doc_id": doc_id,
                "page_count": len(images),
                "elapsed_ms": round(load_elapsed * 1000, 2),
            }
        )

        # Aggregate OCR across pages
        all_tokens = []
        agg_meta: Optional[OCRMetadata] = None
        y_offset = 0.0
        page_heights = []
        
        for page_idx, page in enumerate(images):
            page_start = time.time()
            tokens, meta = ocr.run_on_image(page)
            
            # Shift subsequent pages Y coordinates
            if y_offset:
                for tok in tokens:
                    tok.bbox = [[x, y + y_offset] for x, y in tok.bbox]
            all_tokens.extend(tokens)
            page_height = float(page.shape[0])
            page_heights.append(page_height)
            y_offset += page_height + 200.0
            
            if agg_meta is None:
                agg_meta = meta
            else:
                agg_meta = OCRMetadata(
                    engine=meta.engine,
                    languages=meta.languages,
                    average_confidence=(agg_meta.average_confidence + meta.average_confidence) / 2,
                    token_count=agg_meta.token_count + meta.token_count,
                    processing_ms=agg_meta.processing_ms + meta.processing_ms,
                    rotation_applied_degrees=agg_meta.rotation_applied_degrees or meta.rotation_applied_degrees,
                )
            
            page_elapsed = time.time() - page_start
            logger.debug(
                "Page OCR complete",
                extra={
                    "doc_id": doc_id,
                    "page_number": page_idx + 1,
                    "token_count": len(tokens),
                    "elapsed_ms": round(page_elapsed * 1000, 2),
                }
            )
            
        if agg_meta is None:
            agg_meta = OCRMetadata()
        
        ocr_elapsed = time.time() - ocr_stage_start
        logger.info(
            "OCR stage complete",
            extra={
                "doc_id": doc_id,
                "total_tokens": agg_meta.token_count,
                "avg_confidence": agg_meta.average_confidence,
                "rotation": agg_meta.rotation_applied_degrees,
                "elapsed_ms": round(ocr_elapsed * 1000, 2),
            }
        )

        # --- Stage 2: Classification ---
        class_start = time.time()
        classification = classify_tokens(all_tokens)
        class_elapsed = time.time() - class_start
        
        logger.debug(
            "Classification complete",
            extra={
                "doc_id": doc_id,
                "doc_type": classification.document_type.value,
                "confidence": round(classification.confidence, 4),
                "elapsed_ms": round(class_elapsed * 1000, 2),
            }
        )

        # --- Stage 3: Parsing ---
        parse_start = time.time()
        parsed_data = parse_traveller(all_tokens, classification.document_type)
        parse_elapsed = time.time() - parse_start
        
        # --- Stage 4: Validation ---
        validate_start = time.time()
        
        # Only validate traveller documents, not business cards
        if classification.document_type == DocumentType.BUSINESS_CARD:
            issues = []
            # Business cards don't have traveller validation
            traveller = TravellerData(document_type=classification.document_type)
            overall_conf = 0.0
        else:
            traveller = parsed_data
            traveller.document_type = classification.document_type
            issues = validate_traveller(traveller)
            
            # Overall confidence for traveller documents
            field_confidences = [
                fv.confidence for fv in (
                    traveller.document_number, traveller.full_name,
                    traveller.date_of_birth, traveller.date_of_expiry,
                    traveller.date_of_issue, traveller.gender, traveller.nationality,
                ) if fv and fv.value
            ]
            overall_conf = round(sum(field_confidences) / len(field_confidences), 4) if field_confidences else 0.0
        
        validate_elapsed = time.time() - validate_start
        
        logger.debug(
            "Parse & validate complete",
            extra={
                "doc_id": doc_id,
                "validation_issues": len(issues),
                "parse_elapsed_ms": round(parse_elapsed * 1000, 2),
                "validate_elapsed_ms": round(validate_elapsed * 1000, 2),
            }
        )

        # Persist normalised search columns for duplicate detection (only for traveller docs)
        if classification.document_type != DocumentType.BUSINESS_CARD:
            record.document_type = traveller.document_type.value
            record.document_number = normalize_doc_number(
                traveller.document_number.value if traveller.document_number else None
            )
            record.full_name = traveller.full_name.value if traveller.full_name else None
            record.normalized_name = normalize_name(record.full_name)
            record.date_of_birth = traveller.date_of_birth.value if traveller.date_of_birth else None
            record.classification_confidence = classification.confidence
            record.overall_confidence = overall_conf
        else:
            # For business cards, store minimal info
            record.document_type = DocumentType.BUSINESS_CARD.value
            record.classification_confidence = classification.confidence
            record.overall_confidence = 0.0
            # Try to extract name for search purposes
            if hasattr(parsed_data, 'full_name') and parsed_data.full_name:
                record.full_name = parsed_data.full_name.value
                record.normalized_name = normalize_name(record.full_name)

        record.ocr_metadata = json.loads(agg_meta.model_dump_json())
        record.processed_at = datetime.now(timezone.utc)
        record.status = ProcessingStatus.COMPLETED.value

        # --- Stage 5: Duplicate detection (only for traveller docs) ---
        dup_start = time.time()
        if classification.document_type != DocumentType.BUSINESS_CARD:
            duplicates = find_duplicates(session, traveller, exclude_id=doc_id)
        else:
            duplicates = []
        dup_elapsed = time.time() - dup_start
        
        if duplicates:
            logger.info(
                "Duplicates found",
                extra={
                    "doc_id": doc_id,
                    "duplicate_count": len(duplicates),
                    "elapsed_ms": round(dup_elapsed * 1000, 2),
                }
            )

        # --- Create result based on document type ---
        if classification.document_type == DocumentType.BUSINESS_CARD:
            result = DocumentResult(
                id=doc_id,
                filename=original_filename,
                status=ProcessingStatus.COMPLETED,
                document_type=classification.document_type,
                classification_confidence=classification.confidence,
                overall_confidence=0.0,
                traveller=TravellerData(),  # Empty traveller data
                business_card=parsed_data,  # Business card data
                ocr_metadata=agg_meta,
                validation=issues,
                duplicates=duplicates,
                created_at=record.created_at,
                processed_at=record.processed_at,
            )
        else:
            result = DocumentResult(
                id=doc_id,
                filename=original_filename,
                status=ProcessingStatus.COMPLETED,
                document_type=traveller.document_type,
                classification_confidence=classification.confidence,
                overall_confidence=overall_conf,
                traveller=traveller,
                ocr_metadata=agg_meta,
                validation=issues,
                duplicates=duplicates,
                created_at=record.created_at,
                processed_at=record.processed_at,
            )
            
        record.result_json = json.loads(result.model_dump_json())
        session.commit()

        # Persist JSON copy on disk
        settings = get_settings()
        json_path = settings.output_path / f"{doc_id}.json"
        json_path.write_text(result.model_dump_json(indent=2))
        
        total_elapsed = time.time() - total_start
        logger.info(
            "Document processing completed successfully",
            extra={
                "doc_id": doc_id,
                "original_filename": original_filename,
                "doc_type": result.document_type.value,
                "overall_confidence": result.overall_confidence,
                "validation_issues": len(issues),
                "duplicates": len(duplicates),
                "total_elapsed_ms": round(total_elapsed * 1000, 2),
            }
        )
        return result
        
    except Exception as exc:  # noqa: BLE001
        total_elapsed = time.time() - total_start
        error_traceback = traceback.format_exc()
        
        logger.error(
            "Document processing failed",
            extra={
                "doc_id": doc_id,
                "original_filename": original_filename,
                "stored_path": str(stored_path),
                "error": str(exc),
                "error_type": type(exc).__name__,
                "total_elapsed_ms": round(total_elapsed * 1000, 2),
            },
            exc_info=True
        )
        
        record.status = ProcessingStatus.FAILED.value
        record.error = f"{str(exc)}\n\n{error_traceback}"
        record.processed_at = datetime.now(timezone.utc)
        session.commit()
        
        return DocumentResult(
            id=doc_id,
            filename=original_filename,
            status=ProcessingStatus.FAILED,
            document_type=DocumentType.UNKNOWN,
            classification_confidence=0.0,
            overall_confidence=0.0,
            traveller=TravellerData(),
            ocr_metadata=OCRMetadata(),
            validation=[],
            duplicates=[],
            error=str(exc),
            created_at=record.created_at,
            processed_at=record.processed_at,
        )


@router.post("/documents/upload", response_model=DocumentResult)
async def upload_single(
    file: UploadFile = File(..., description="A single passport / Aadhaar / PDF"),
    session: Session = Depends(get_session),
):
    """Upload and synchronously process a single document."""
    stored = await _save_upload(file)
    return await asyncio.to_thread(_process_stored_file, session, stored, file.filename or stored.name)


@router.post("/documents/batch", response_model=BatchProcessResponse)
async def upload_batch(
    files: List[UploadFile] = File(..., description="Multiple travel documents to process"),
    session: Session = Depends(get_session),
):
    """Upload and process multiple documents. All results are returned inline."""
    batch_id = str(uuid.uuid4())
    batch_start = time.time()
    
    logger.info(
        "Batch processing started",
        extra={
            "batch_id": batch_id,
            "file_count": len(files),
        }
    )
    
    if not files:
        logger.warning("Batch upload with no files", extra={"batch_id": batch_id})
        raise HTTPException(status_code=400, detail="No files provided")

    results: list[DocumentResult] = []
    for idx, f in enumerate(files):
        file_start = time.time()
        try:
            stored = await _save_upload(f)
            result = await asyncio.to_thread(
                _process_stored_file, session, stored, f.filename or stored.name
            )
            file_elapsed = time.time() - file_start
            logger.debug(
                "Batch file processed",
                extra={
                    "batch_id": batch_id,
                    "index": idx + 1,
                    "original_filename": f.filename,
                    "status": result.status.value,
                    "elapsed_ms": round(file_elapsed * 1000, 2),
                }
            )
            results.append(result)
        except HTTPException as http_exc:
            file_elapsed = time.time() - file_start
            logger.warning(
                "Batch file upload failed",
                extra={
                    "batch_id": batch_id,
                    "index": idx + 1,
                    "original_filename": f.filename,
                    "error": http_exc.detail if isinstance(http_exc.detail, str) else str(http_exc.detail),
                    "elapsed_ms": round(file_elapsed * 1000, 2),
                }
            )
            results.append(
                DocumentResult(
                    id=str(uuid.uuid4()),
                    filename=f.filename or "unknown",
                    status=ProcessingStatus.FAILED,
                    document_type=DocumentType.UNKNOWN,
                    classification_confidence=0.0,
                    overall_confidence=0.0,
                    traveller=TravellerData(),
                    ocr_metadata=OCRMetadata(),
                    error=http_exc.detail if isinstance(http_exc.detail, str) else str(http_exc.detail),
                    created_at=datetime.now(timezone.utc),
                )
            )
            continue
        except Exception as e:
            file_elapsed = time.time() - file_start
            logger.error(
                "Batch file processing unexpected error",
                extra={
                    "batch_id": batch_id,
                    "index": idx + 1,
                    "original_filename": f.filename,
                    "error": str(e),
                    "elapsed_ms": round(file_elapsed * 1000, 2),
                },
                exc_info=True
            )
            results.append(
                DocumentResult(
                    id=str(uuid.uuid4()),
                    filename=f.filename or "unknown",
                    status=ProcessingStatus.FAILED,
                    document_type=DocumentType.UNKNOWN,
                    classification_confidence=0.0,
                    overall_confidence=0.0,
                    traveller=TravellerData(),
                    ocr_metadata=OCRMetadata(),
                    error=str(e),
                    created_at=datetime.now(timezone.utc),
                )
            )

    completed = sum(1 for r in results if r.status == ProcessingStatus.COMPLETED)
    failed = sum(1 for r in results if r.status == ProcessingStatus.FAILED)
    batch_elapsed = time.time() - batch_start
    
    logger.info(
        "Batch processing completed",
        extra={
            "batch_id": batch_id,
            "total": len(results),
            "completed": completed,
            "failed": failed,
            "total_elapsed_ms": round(batch_elapsed * 1000, 2),
        }
    )
    
    return BatchProcessResponse(
        batch_id=batch_id, total=len(results), completed=completed, failed=failed, results=results,
    )


# ---------------------------------------------------------------------------
# History / retrieval
# ---------------------------------------------------------------------------
@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    document_type: Optional[DocumentType] = Query(None),
    search: Optional[str] = Query(None, description="Search by name or document number"),
    session: Session = Depends(get_session),
):
    start_time = time.time()
    q = session.query(ProcessedDocument)
    
    if document_type:
        q = q.filter(ProcessedDocument.document_type == document_type.value)
    if search:
        like = f"%{search.upper()}%"
        q = q.filter(
            (ProcessedDocument.normalized_name.like(like))
            | (ProcessedDocument.document_number.like(like))
            | (ProcessedDocument.filename.like(like))
        )
    
    total = q.count()
    rows = q.order_by(desc(ProcessedDocument.created_at)).offset(offset).limit(limit).all()

    items: list[DocumentResult] = []
    for row in rows:
        items.append(_row_to_result(row))
    
    elapsed = time.time() - start_time
    logger.debug(
        "Document list query",
        extra={
            "total_count": total,
            "returned_count": len(items),
            "limit": limit,
            "offset": offset,
            "elapsed_ms": round(elapsed * 1000, 2),
        }
    )
    
    return DocumentListResponse(total=total, items=items)


@router.get("/documents/{doc_id}", response_model=DocumentResult)
async def get_document(doc_id: str, session: Session = Depends(get_session)):
    row = session.get(ProcessedDocument, doc_id)
    if not row:
        logger.warning("Document not found", extra={"doc_id": doc_id})
        raise HTTPException(status_code=404, detail="Document not found")
    return _row_to_result(row)


@router.get("/documents/{doc_id}/json")
async def download_json(doc_id: str, session: Session = Depends(get_session)):
    row = session.get(ProcessedDocument, doc_id)
    if not row:
        logger.warning("Document not found for JSON download", extra={"doc_id": doc_id})
        raise HTTPException(status_code=404, detail="Document not found")
    body = row.result_json or {}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="{doc_id}.json"'},
    )


@router.get("/documents/{doc_id}/file")
async def download_original(doc_id: str, session: Session = Depends(get_session)):
    row = session.get(ProcessedDocument, doc_id)
    if not row or not row.stored_path:
        logger.warning("Document or stored path not found", extra={"doc_id": doc_id})
        raise HTTPException(status_code=404, detail="File not found")
    
    path = Path(row.stored_path)
    if not path.exists():
        logger.warning("Stored file missing on disk", extra={"doc_id": doc_id, "stored_path": str(path)})
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    return FileResponse(str(path), filename=row.filename)


@router.delete("/documents/{doc_id}", response_model=MessageResponse)
async def delete_document(doc_id: str, session: Session = Depends(get_session)):
    row = session.get(ProcessedDocument, doc_id)
    if not row:
        logger.warning("Document not found for deletion", extra={"doc_id": doc_id})
        raise HTTPException(status_code=404, detail="Document not found")
    
    if row.stored_path:
        try:
            Path(row.stored_path).unlink(missing_ok=True)
            logger.debug("Stored file deleted", extra={"doc_id": doc_id, "stored_path": row.stored_path})
        except OSError as e:
            logger.warning(
                "Could not remove stored file",
                extra={"doc_id": doc_id, "stored_path": row.stored_path, "error": str(e)}
            )
    
    session.delete(row)
    session.commit()
    logger.info("Document deleted", extra={"doc_id": doc_id, "original_filename": row.filename})
    return MessageResponse(message="deleted", detail=doc_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _row_to_result(row: ProcessedDocument) -> DocumentResult:
    """Rebuild a DocumentResult from a persisted row (using stored result_json)."""
    if row.result_json:
        try:
            return DocumentResult.model_validate(row.result_json)
        except Exception:  # noqa: BLE001 - fall back to minimal shape if schema changed
            logger.warning(
                "Stored result_json invalid; returning minimal shape",
                extra={"doc_id": row.id}
            )

    # Fallback minimal result
    return DocumentResult(
        id=row.id,
        filename=row.filename,
        status=ProcessingStatus(row.status),
        document_type=DocumentType(row.document_type or "UNKNOWN"),
        classification_confidence=row.classification_confidence or 0.0,
        overall_confidence=row.overall_confidence or 0.0,
        traveller=TravellerData(document_type=DocumentType(row.document_type or "UNKNOWN")),
        ocr_metadata=OCRMetadata(**(row.ocr_metadata or {})),
        validation=[],
        duplicates=[],
        error=row.error,
        created_at=row.created_at,
        processed_at=row.processed_at,
    )