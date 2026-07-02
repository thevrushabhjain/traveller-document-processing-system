# pdf_service.py
"""PDF -> image conversion using pdf2image / Poppler."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from pdf2image import convert_from_path

from ..config import get_settings

logger = logging.getLogger(__name__)


def pdf_to_images(
    pdf_path: Path, 
    dpi: int = 250, 
    max_pages: int = 10,
    poppler_path: Optional[str] = None,
) -> List[np.ndarray]:
    """Convert a PDF file to a list of OpenCV (BGR) numpy images.

    Args:
        pdf_path: Path to input PDF.
        dpi: Rasterisation resolution (higher = better OCR, slower).
        max_pages: Safety cap.
        poppler_path: Optional path to Poppler bin directory (Windows support).
    """
    start_time = time.time()
    
    # Get Poppler path from settings if not provided
    if poppler_path is None:
        settings = get_settings()
        poppler_path = settings.poppler_path_resolved
    
    logger.info(
        "Converting PDF to images",
        extra={
            "pdf_path": str(pdf_path),
            "dpi": dpi,
            "max_pages": max_pages,
            "poppler_path": poppler_path,
        }
    )
    
    try:
        pil_pages = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            last_page=max_pages,
            poppler_path=poppler_path,
            thread_count=1,  # Avoid thread contention with OCR
        )
    except Exception as e:
        logger.error(
            "PDF conversion failed",
            extra={
                "pdf_path": str(pdf_path),
                "dpi": dpi,
                "error": str(e),
            },
            exc_info=True
        )
        raise
    
    out: List[np.ndarray] = []
    for idx, pil in enumerate(pil_pages):
        arr = np.array(pil)
        if arr.ndim == 3 and arr.shape[2] == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        out.append(arr)
    
    elapsed = time.time() - start_time
    logger.info(
        "PDF conversion complete",
        extra={
            "pdf_path": str(pdf_path),
            "page_count": len(out),
            "elapsed_ms": round(elapsed * 1000, 2),
        }
    )
    
    return out