# ocr_service.py
"""PaddleOCR wrapper. Lazy-loaded, thread-safe, multi-language.

Primary backend: **PaddleOCR** (fully offline, no API keys).

On platforms where the PaddlePaddle wheel is unstable (some ARM64 CPUs,
older CPUs without AVX, etc.) the same PaddleOCR PP-OCRv4 model weights
can be served through **rapidocr-onnxruntime** which loads them via ONNX
Runtime. That fallback is engaged automatically only when the Paddle
engine fails to initialise or crashes on the first call. The output
schema is identical either way.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import cv2
import numpy as np

from ..config import get_settings
from ..schemas import OCRMetadata, OCRToken
from .preprocess import preprocess_image

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-process based Paddle self-test
# ---------------------------------------------------------------------------
def _paddle_selftest() -> bool:
    """Run a real PaddleOCR init + inference in a child process to detect
    segfaults / native crashes before the main process ever touches Paddle.

    The child process builds the engine with the exact same (non-deprecated)
    keyword arguments used by :class:`_PaddleEngine` and performs the same
    ``predict`` call the application relies on, so a crash here reliably
    predicts a crash in real usage.
    """
    code = (
        "import cv2, numpy as np;"
        "img=np.full((80,400,3),255,dtype=np.uint8);"
        "cv2.putText(img,'PING TEST',(10,50),cv2.FONT_HERSHEY_SIMPLEX,1.1,(0,0,0),2);"
        "from paddleocr import PaddleOCR;"
        "o=PaddleOCR(lang='en', use_doc_orientation_classify=False, "
        "use_doc_unwarping=False, use_textline_orientation=True);"
        "r=o.predict(img);"
        "print('OK', len(r or []))"
    )
    try:
        res = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=180,
            env={**os.environ, "GLOG_minloglevel": "2"},
        )
        ok = res.returncode == 0 and b"OK" in res.stdout
        if not ok:
            stderr = (res.stderr or b"")[-500:].decode(errors="ignore")
            logger.warning(
                "PaddleOCR self-test failed (rc=%s): %s",
                res.returncode,
                stderr,
            )
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning("PaddleOCR self-test raised: %s", exc)
        return False


def _check_paddle_import() -> bool:
    """Check if PaddleOCR can be imported without errors."""
    try:
        import paddleocr
        return True
    except ImportError:
        return False
    except Exception as e:
        logger.warning("PaddleOCR import failed: %s", e)
        return False


class _PaddleEngine:
    """Thin adapter around PaddleOCR 3.x (falls back to the 2.x call shape
    only if the installed package predates the ``predict`` API)."""

    def __init__(self, lang: str, use_gpu: bool, settings):
        from paddleocr import PaddleOCR

        try:
            # PaddleOCR 3.x - modern, non-deprecated keyword arguments.
            self._engine = PaddleOCR(
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
            )
            self._api = "predict"
            logger.debug("PaddleOCR 3.x initialized", extra={"lang": lang, "use_gpu": use_gpu})
        except TypeError as e:
            # PaddleOCR 2.x fallback for older pinned installs.
            logger.debug("Falling back to PaddleOCR 2.x API: %s", e)
            self._engine = PaddleOCR(lang=lang, use_angle_cls=True, use_gpu=use_gpu)
            self._api = "ocr"

    def run(self, image: np.ndarray) -> List[OCRToken]:
        start_time = time.time()
        if self._api == "predict":
            raw = self._engine.predict(image)
            tokens = _parse_predict_output(raw)
        else:
            raw = self._engine.ocr(image, cls=True)
            tokens = _parse_legacy_output(raw)
        
        elapsed = time.time() - start_time
        logger.debug(
            "PaddleOCR inference",
            extra={
                "token_count": len(tokens),
                "elapsed_ms": round(elapsed * 1000, 2),
                "api": self._api,
            }
        )
        return tokens


class _RapidEngine:
    """Fallback engine: rapidocr-onnxruntime (uses PaddleOCR models via ONNX)."""

    def __init__(self, lang: str):
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
            self._lang = lang
            logger.debug("RapidOCR initialized", extra={"lang": lang})
        except ImportError as e:
            logger.error("Failed to import rapidocr_onnxruntime: %s", e)
            raise

    def run(self, image: np.ndarray) -> List[OCRToken]:
        start_time = time.time()
        raw, _ = self._engine(image)
        tokens: List[OCRToken] = []
        if not raw:
            return tokens
        
        for line in raw:
            try:
                bbox, text, score = line[0], line[1], float(line[2])
                text = (text or "").strip()
                if not text:
                    continue
                tokens.append(OCRToken(text=text, confidence=score, bbox=bbox or []))
            except Exception as e:  # noqa: BLE001
                logger.debug("Skipping malformed OCR token: %s", e)
                continue
        
        elapsed = time.time() - start_time
        logger.debug(
            "RapidOCR inference",
            extra={
                "token_count": len(tokens),
                "elapsed_ms": round(elapsed * 1000, 2),
            }
        )
        return tokens


def _parse_predict_output(raw) -> List[OCRToken]:
    tokens: List[OCRToken] = []
    if not raw:
        return tokens
    for page in raw:
        texts = page.get("rec_texts", []) or []
        scores = page.get("rec_scores", []) or []
        polys = page.get("rec_polys", []) or page.get("rec_boxes", []) or []
        for i, text in enumerate(texts):
            if not text:
                continue
            bbox_raw = polys[i] if i < len(polys) else None
            bbox: List[List[float]] = []
            if bbox_raw is not None:
                arr = np.asarray(bbox_raw).tolist()
                if arr and isinstance(arr[0], (int, float)):
                    x1, y1, x2, y2 = arr[0], arr[1], arr[2], arr[3]
                    bbox = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
                else:
                    bbox = arr
            tokens.append(
                OCRToken(
                    text=text.strip(),
                    confidence=float(scores[i]) if i < len(scores) else 0.0,
                    bbox=bbox,
                )
            )
    return tokens


def _parse_legacy_output(raw) -> List[OCRToken]:
    tokens: List[OCRToken] = []
    if not raw:
        return tokens
    for page in raw:
        if page is None:
            continue
        for item in page:
            try:
                bbox = item[0]
                text_score = item[1]
                text, score = (text_score[0], float(text_score[1])) if isinstance(
                    text_score, (list, tuple)
                ) else (str(text_score), 0.0)
                text = (text or "").strip()
                if not text:
                    continue
                tokens.append(OCRToken(text=text, confidence=score, bbox=bbox or []))
            except Exception:  # noqa: BLE001
                continue
    return tokens


def _orientation_score(tokens: List[OCRToken]) -> float:
    """Score a set of OCR tokens for how likely they represent a *correctly
    oriented* page. Correctly oriented horizontal text lines produce wide,
    short bounding boxes; a page rotated 90 degrees produces tall, thin
    ones even when the per-textline angle classifier still manages to read
    the characters correctly (recognition and box geometry are separate
    concerns in PaddleOCR/rapidocr). Boxes that look "vertical" are heavily
    down-weighted so the overall page rotation - not just text legibility -
    drives the decision.
    """
    score = 0.0
    for tok in tokens:
        if not tok.bbox or len(tok.bbox) < 3:
            continue
        xs = [p[0] for p in tok.bbox]
        ys = [p[1] for p in tok.bbox]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        if height <= 0:
            continue
        weight = 1.0 if width >= height else 0.15
        score += len(tok.text) * tok.confidence * weight
    return score


def _heuristic_orientation(image: np.ndarray) -> tuple[int, np.ndarray]:
    """Simple heuristic orientation detection using image statistics."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    
    # Compute gradient orientations
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    
    # Compute magnitude and angle
    mag, angle = cv2.cartToPolar(gx, gy)
    
    # Weighted histogram of angles
    hist = cv2.calcHist([angle.astype(np.float32)], [0], mag.astype(np.float32), [36], [0, 2 * np.pi])
    peak_idx = np.argmax(hist.flatten())
    peak_angle = (peak_idx / 36) * 180  # degrees
    
    # Determine closest rotation: 0, 90, 180, 270
    rotations = [0, 90, 180, 270]
    best_rot = min(rotations, key=lambda r: abs((peak_angle - r) % 180))
    
    if best_rot == 0:
        return 0, image
    
    rotate_code = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }[best_rot]
    
    logger.debug("Heuristic orientation detected", extra={"angle": round(peak_angle, 1), "rotation": best_rot})
    return best_rot, cv2.rotate(image, rotate_code)


def _enhance_image(image: np.ndarray) -> np.ndarray:
    """Apply CLAHE and adaptive thresholding for contrast enhancement.
    
    This improves OCR accuracy on low-quality or poorly lit documents.
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    
    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    
    # Apply adaptive thresholding to improve text contrast
    # Use a larger block size for document images
    adaptive = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=5
    )
    
    # Convert back to 3-channel if original was color
    if len(image.shape) == 3:
        enhanced_color = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        adaptive_color = cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR)
        # Blend original with enhanced versions
        blended = cv2.addWeighted(image, 0.4, enhanced_color, 0.3, 0)
        blended = cv2.addWeighted(blended, 0.7, adaptive_color, 0.3, 0)
        return blended
    else:
        # For grayscale, blend original with enhanced
        blended = cv2.addWeighted(gray, 0.4, enhanced, 0.3, 0)
        blended = cv2.addWeighted(blended, 0.7, adaptive, 0.3, 0)
        return blended


def _deskew_image(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Detect and correct skew in the image.
    
    Returns:
        Tuple of (deskewed_image, rotation_angle)
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    
    # Threshold the image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Find all non-zero points
    points = cv2.findNonZero(binary)
    if points is None:
        return image, 0.0
    
    # Get the minimum area rectangle
    rect = cv2.minAreaRect(points)
    angle = rect[-1]
    
    # Determine the rotation angle
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90
    
    # Only apply if skew is significant (> 0.5 degrees)
    if abs(angle) < 0.5:
        return image, 0.0
    
    # Rotate the image
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    
    # Calculate the new bounding dimensions
    cos = abs(rotation_matrix[0, 0])
    sin = abs(rotation_matrix[0, 1])
    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))
    
    # Adjust rotation matrix for new dimensions
    rotation_matrix[0, 2] += (new_w / 2) - center[0]
    rotation_matrix[1, 2] += (new_h / 2) - center[1]
    
    deskewed = cv2.warpAffine(image, rotation_matrix, (new_w, new_h), 
                              borderMode=cv2.BORDER_CONSTANT, 
                              borderValue=(255, 255, 255))
    
    logger.debug("Deskew applied", extra={"angle": round(angle, 2)})
    return deskewed, angle


def _detect_orientation(image: np.ndarray) -> tuple[int, np.ndarray]:
    """Detect and correct 0/90/180/270 degree orientation.
    
    Uses a combination of heuristic and OCR-based orientation detection.
    """
    # First try heuristic method (faster)
    try:
        rot_angle, oriented = _heuristic_orientation(image)
        if rot_angle != 0:
            logger.debug("Heuristic orientation correction applied", extra={"rotation": rot_angle})
            return rot_angle, oriented
    except Exception as e:
        logger.debug("Heuristic orientation failed: %s", e)
    
    # Fallback to OCR-based orientation detection
    # This will be called from run_on_image with actual engine
    return 0, image


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------
class OCRService:
    """Thread-safe OCR service supporting multiple languages."""

    _lock = threading.Lock()

    _LANG_ALIASES = {
        "en": "en", "eng": "en", "english": "en",
        "hi": "hi", "hin": "hi", "hindi": "hi",
        "ar": "arabic", "ru": "ru", "fr": "fr",
        "de": "german", "ja": "japan", "ko": "korean",
        "ch": "ch", "chinese": "ch",
    }

    def __init__(
        self, 
        languages: Optional[List[str]] = None, 
        use_gpu: bool = False,
        backend: Literal["auto", "paddleocr", "rapidocr"] = "auto",
        orientation: Literal["auto", "off", "heuristic"] = "auto",
    ):
        settings = get_settings()
        self._settings = settings
        self._languages: List[str] = [
            self._LANG_ALIASES.get(lang.lower(), lang.lower())
            for lang in (languages or settings.ocr_language_list)
        ]
        self._use_gpu = use_gpu
        self._requested_backend = backend
        self._orientation_mode = orientation
        self._engines: Dict[str, Any] = {}
        self._ready = False
        self._backend_name = "Unknown"
        self._paddle_ok: Optional[bool] = None  # cached self-test result
        self._initialized_backend = None
        
        # Validate orientation mode
        if self._orientation_mode not in ["auto", "off", "heuristic"]:
            logger.warning(
                "Invalid orientation mode '%s', falling back to 'auto'",
                self._orientation_mode
            )
            self._orientation_mode = "auto"

    # -----------------------------------------------------------------
    def _paddle_available(self) -> bool:
        if self._paddle_ok is not None:
            return self._paddle_ok
        
        # Check environment override
        env_override = os.environ.get("FORCE_OCR_BACKEND", "").lower()
        if env_override == "rapidocr":
            self._paddle_ok = False
            self._backend_name = "PaddleOCR-via-ONNX (rapidocr)"
            return False
        if env_override == "paddleocr":
            self._paddle_ok = True
            return True
        
        # Check requested backend
        if self._requested_backend == "rapidocr":
            self._paddle_ok = False
            self._backend_name = "PaddleOCR-via-ONNX (rapidocr)"
            logger.info("Using rapidocr backend per configuration")
            return False
        if self._requested_backend == "paddleocr":
            # Force paddle, don't fall back
            if _check_paddle_import():
                self._paddle_ok = True
                return True
            else:
                logger.error("PaddleOCR requested but not available")
                self._paddle_ok = False
                self._backend_name = "PaddleOCR-via-ONNX (rapidocr)"
                return False
        
        # Auto: test Paddle
        if _check_paddle_import():
            logger.info("Running PaddleOCR self-test in a child process...")
            self._paddle_ok = _paddle_selftest()
            if self._paddle_ok:
                self._backend_name = "PaddleOCR"
                return True
            else:
                logger.warning(
                    "PaddleOCR is not stable on this host; using rapidocr-onnxruntime "
                    "(same PP-OCR model weights, ONNX runtime)."
                )
                self._backend_name = "PaddleOCR-via-ONNX (rapidocr)"
                return False
        else:
            logger.info("PaddleOCR not installed; using rapidocr-onnxruntime")
            self._paddle_ok = False
            self._backend_name = "PaddleOCR-via-ONNX (rapidocr)"
            return False

    def _build_engine(self, lang: str):
        if self._paddle_available():
            try:
                engine = _PaddleEngine(lang=lang, use_gpu=self._use_gpu, settings=self._settings)
                if self._initialized_backend is None:
                    self._initialized_backend = "paddleocr"
                return engine
            except Exception as e:
                logger.exception("Failed to init PaddleOCR for lang=%s; falling back to rapidocr", lang)
                if self._requested_backend == "paddleocr":
                    # Don't fall back if paddle was explicitly requested
                    logger.error("PaddleOCR initialization failed and it was explicitly requested")
                    raise
                self._paddle_ok = False
                self._backend_name = "PaddleOCR-via-ONNX (rapidocr)"
        
        # Fallback to rapidocr
        if self._initialized_backend is None:
            self._initialized_backend = "rapidocr"
        return _RapidEngine(lang=lang)

    def warmup(self) -> None:
        start_time = time.time()
        with self._lock:
            for lang in self._languages:
                if lang not in self._engines:
                    try:
                        self._engines[lang] = self._build_engine(lang)
                    except Exception as e:
                        logger.exception("Cannot load OCR engine for lang=%s", lang)
                        if self._requested_backend == "paddleocr":
                            raise
            self._ready = bool(self._engines)
            elapsed = time.time() - start_time
            logger.info(
                "OCR warmup complete",
                extra={
                    "backend": self._backend_name,
                    "languages": self._languages,
                    "ready": self._ready,
                    "elapsed_ms": round(elapsed * 1000, 2),
                }
            )

    def is_ready(self) -> bool:
        return self._ready

    @property
    def languages(self) -> List[str]:
        return list(self._languages)

    @property
    def backend(self) -> str:
        return self._backend_name

    def _get_engine(self, lang: str):
        with self._lock:
            if lang not in self._engines:
                self._engines[lang] = self._build_engine(lang)
                self._ready = True
            return self._engines[lang]

    def run_on_image(self, image: np.ndarray) -> tuple[List[OCRToken], OCRMetadata]:
        started = time.time()
        
        # Step 1: Enhance image (CLAHE + adaptive thresholding)
        enhanced = _enhance_image(image)
        
        # Step 2: Deskew the image
        deskewed, skew_angle = _deskew_image(enhanced)
        
        # Step 3: Preprocess (existing preprocessing)
        preprocessed, _ = preprocess_image(deskewed)
        skew_rotation = skew_angle  # Use the deskew angle

        # Step 4: Orientation correction (0, 90, 180, 270)
        orientation_rotation = 0
        if self._orientation_mode != "off":
            primary_engine = self._get_engine(self._languages[0] if self._languages else "en")
            
            if self._orientation_mode == "heuristic":
                orientation_rotation, preprocessed = _heuristic_orientation(preprocessed)
            else:  # "auto"
                orientation_rotation, preprocessed = self._auto_orient_with_ocr(
                    primary_engine, preprocessed
                )

        # Step 5: OCR with all languages
        combined: List[OCRToken] = []
        for lang in self._languages:
            engine = self._get_engine(lang)
            try:
                with self._lock:
                    tokens_lang = engine.run(preprocessed)
            except Exception as exc:
                logger.exception("OCR failed for lang=%s: %s", lang, exc)
                continue
            combined.extend(tokens_lang)

        # Deduplicate tokens
        deduped: Dict[tuple, OCRToken] = {}
        for tok in combined:
            key = (tok.text.strip().lower(), self._bbox_key(tok.bbox))
            if key not in deduped or tok.confidence > deduped[key].confidence:
                deduped[key] = tok
        tokens = list(deduped.values())

        avg_conf = float(np.mean([t.confidence for t in tokens])) if tokens else 0.0
        total_rotation = (skew_rotation + orientation_rotation) % 360
        
        elapsed = time.time() - started
        logger.debug(
            "OCR run complete",
            extra={
                "token_count": len(tokens),
                "avg_confidence": round(avg_conf, 4),
                "rotation_total": total_rotation,
                "rotation_skew": skew_rotation,
                "rotation_orientation": orientation_rotation,
                "elapsed_ms": round(elapsed * 1000, 2),
            }
        )
        
        meta = OCRMetadata(
            engine=self._backend_name,
            languages=self._languages,
            average_confidence=round(avg_conf, 4),
            token_count=len(tokens),
            processing_ms=int((time.time() - started) * 1000),
            rotation_applied_degrees=total_rotation,
        )
        return tokens, meta

    @staticmethod
    def _auto_orient_with_ocr(engine, image: np.ndarray) -> tuple[int, np.ndarray]:
        """Pick whichever of 0/90/180/270 degrees the OCR engine reads best.

        Runs a cheap OCR pass on a downscaled copy at each candidate rotation
        and scores it by total (text length x confidence). The winning
        rotation is then applied to the full-resolution image once.
        """
        start_time = time.time()
        h, w = image.shape[:2]
        scale = min(1.0, 640 / max(h, w)) if max(h, w) > 0 else 1.0
        small = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale)))) if scale < 1.0 else image

        candidates = {
            0: small,
            90: cv2.rotate(small, cv2.ROTATE_90_CLOCKWISE),
            180: cv2.rotate(small, cv2.ROTATE_180),
            270: cv2.rotate(small, cv2.ROTATE_90_COUNTERCLOCKWISE),
        }
        best_rot, best_score = 0, -1.0
        for rot, candidate in candidates.items():
            try:
                with OCRService._lock:
                    tokens = engine.run(candidate)
            except Exception as e:
                logger.debug("OCR orientation candidate %d failed: %s", rot, e)
                continue
            score = _orientation_score(tokens)
            if score > best_score:
                best_score, best_rot = score, rot

        if best_rot == 0:
            elapsed = time.time() - start_time
            logger.debug(
                "Orientation detection complete",
                extra={"rotation": 0, "score": round(best_score, 2), "elapsed_ms": round(elapsed * 1000, 2)}
            )
            return 0, image
        
        rotate_code = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }[best_rot]
        
        elapsed = time.time() - start_time
        logger.debug(
            "Orientation detection complete",
            extra={"rotation": best_rot, "score": round(best_score, 2), "elapsed_ms": round(elapsed * 1000, 2)}
        )
        return best_rot, cv2.rotate(image, rotate_code)

    def run_on_file(self, path: Path) -> tuple[List[OCRToken], OCRMetadata]:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Cannot read image file: {path}")
        return self.run_on_image(img)

    @staticmethod
    def _bbox_key(bbox) -> tuple:
        if bbox is None or len(bbox) == 0:
            return (0, 0)
        try:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            return (int(sum(xs) / len(xs)) // 8, int(sum(ys) / len(ys)) // 8)
        except Exception:
            return (0, 0)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_ocr_singleton: Optional[OCRService] = None
_singleton_lock = threading.Lock()


def get_ocr_service() -> OCRService:
    global _ocr_singleton
    with _singleton_lock:
        if _ocr_singleton is None:
            settings = get_settings()
            _ocr_singleton = OCRService(
                languages=settings.ocr_language_list,
                use_gpu=settings.ocr_use_gpu,
                backend=settings.ocr_backend,
                orientation=settings.orientation_detection,
            )
        return _ocr_singleton