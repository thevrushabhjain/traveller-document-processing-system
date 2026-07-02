"""OpenCV image preprocessing: grayscale, denoise, deskew, contrast boost."""
from __future__ import annotations

import logging
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _to_grayscale(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _deskew(gray: np.ndarray) -> Tuple[np.ndarray, int]:
    """Estimate skew angle from text lines and rotate.

    Returns the deskewed grayscale image and the applied rotation (rounded degrees).
    """
    inverted = cv2.bitwise_not(gray)
    thresh = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return gray, 0
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    # Limit correction to sensible skew (< 30 degrees)
    if abs(angle) < 0.5 or abs(angle) > 30:
        return gray, 0
    (h, w) = gray.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated, int(round(angle))


def preprocess_image(image: np.ndarray) -> Tuple[np.ndarray, int]:
    """Full preprocessing pipeline.

    Args:
        image: BGR or grayscale numpy image loaded with OpenCV / Pillow.

    Returns:
        Preprocessed BGR image (ready for OCR) and the fine-skew rotation applied
        in degrees. Coarse 90/180/270 orientation is corrected separately by the
        OCR service (see ``ocr_service._detect_best_rotation``), since telling a
        right-side-up page from an upside-down one requires actually reading the
        text rather than a row-variance heuristic (which cannot distinguish the
        two - both produce identical projection profiles).
    """
    if image is None or image.size == 0:
        raise ValueError("Empty image received for preprocessing")

    gray = _to_grayscale(image)
    gray, skew_rot = _deskew(gray)

    # Adaptive denoise + contrast
    denoised = cv2.fastNlMeansDenoising(gray, h=10)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)

    # PaddleOCR expects BGR
    bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
    return bgr, skew_rot


def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Cannot read image: {path}")
    return img
