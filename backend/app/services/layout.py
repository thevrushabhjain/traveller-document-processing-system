"""Layout analysis helpers shared by every extractor.

OCR engines return a flat list of text tokens, each with a bounding box.
Reading a document reliably requires reconstructing the *visual* structure
of the page first: which tokens sit on the same physical line, and in what
left-to-right / top-to-bottom order those lines should be read. Every
extractor (generic, PAN, driving licence, voter ID, ...) builds on top of
these primitives instead of scanning the raw token list with regular
expressions.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable, List, Optional, Sequence

from ..schemas import FieldValue, OCRToken


@dataclass
class Line:
    """A row of OCR tokens that live on (roughly) the same horizontal band."""

    tokens: List[OCRToken] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(t.text.strip() for t in self.tokens if t.text.strip())

    @property
    def confidence(self) -> float:
        confs = [t.confidence for t in self.tokens if t.text.strip()]
        return sum(confs) / len(confs) if confs else 0.0

    @property
    def center_y(self) -> float:
        ys = [_bbox_center(t.bbox)[1] for t in self.tokens]
        return sum(ys) / len(ys) if ys else 0.0


def _bbox_center(bbox) -> tuple[float, float]:
    if not bbox:
        return (0.0, 0.0)
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _bbox_height(bbox) -> float:
    if not bbox:
        return 20.0
    ys = [p[1] for p in bbox]
    return max(ys) - min(ys) or 20.0


def _bbox_left(bbox) -> float:
    if not bbox:
        return 0.0
    return min(p[0] for p in bbox)


def _bbox_width(bbox) -> float:
    if not bbox:
        return 20.0
    xs = [p[0] for p in bbox]
    return max(xs) - min(xs) or 20.0


def group_lines(tokens: Sequence[OCRToken]) -> List[Line]:
    """Cluster OCR tokens into reading-order lines using their bounding boxes.

    Tokens are first sorted by vertical position. Any token whose centre
    falls within half the median token height of the current line's centre
    is appended to that line; otherwise a new line is started. Each line's
    tokens are then sorted left-to-right so multi-column layouts (e.g. a
    label and its value emitted as two separate OCR boxes on one physical
    row) come out in natural reading order.

    Tokens whose bounding box is dramatically taller than the page's
    typical text line (e.g. a caption printed sideways along a card's
    margin, common on driving licences) are kept as their own standalone
    line instead of being clustered by centre-y - their vertical extent
    covers many unrelated horizontal lines' worth of height, so including
    them in the normal pass would silently glue unrelated fields together.
    """
    if not tokens:
        return []

    heights = [_bbox_height(t.bbox) for t in tokens if t.bbox]
    median_height = statistics.median(heights) if heights else 20.0
    threshold = max(median_height * 0.6, 8.0)
    outlier_cutoff = max(median_height * 2.5, 60.0)

    normal_tokens = [t for t in tokens if _bbox_height(t.bbox) <= outlier_cutoff]
    outlier_tokens = [t for t in tokens if _bbox_height(t.bbox) > outlier_cutoff]

    ordered = sorted(normal_tokens, key=lambda t: _bbox_center(t.bbox)[1])
    lines: List[Line] = []
    for tok in ordered:
        cy = _bbox_center(tok.bbox)[1]
        placed = False
        for line in lines:
            if abs(line.center_y - cy) <= threshold:
                line.tokens.append(tok)
                placed = True
                break
        if not placed:
            lines.append(Line(tokens=[tok]))

    for tok in outlier_tokens:
        lines.append(Line(tokens=[tok]))

    for line in lines:
        line.tokens.sort(key=lambda t: _bbox_left(t.bbox))
    lines.sort(key=lambda l: l.center_y)
    return lines


def full_text(lines: Iterable[Line]) -> str:
    return "\n".join(l.text for l in lines)


# ---------------------------------------------------------------------------
# Noise filtering - boilerplate that should never be picked as a name/address
# ---------------------------------------------------------------------------
NOISE_PHRASES = {
    "government of india", "govt of india", "govt. of india",
    "unique identification authority of india", "uidai",
    "online authentication", "digitally signed", "download date",
    "issue date", "not a proof of", "address as per", "enrolment no",
    "election commission of india", "income tax department",
    "ministry of road transport", "transport department",
    "republic of india", "signature", "specimen", "sample",
}


def is_noise_line(text: str) -> bool:
    lower = text.strip().lower()
    if not lower:
        return True
    return any(phrase in lower for phrase in NOISE_PHRASES)


# ---------------------------------------------------------------------------
# Label -> value candidate search
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    value: str
    confidence: float
    line_index: int
    source: str  # "same_line" | "next_line" | "pattern"


@lru_cache(maxsize=256)
def _compile_keyword(keyword: str) -> re.Pattern:
    """Build a regex for a label keyword that tolerates OCR dropping the
    spaces between words (very common: "Date of Birth" -> "DateofBirth").
    Each word in the keyword is joined with an optional-whitespace gap, and
    the whole pattern is word-bounded so short keywords (e.g. "son") don't
    match inside unrelated words (e.g. "Person").
    """
    words = [re.escape(w) for w in keyword.split() if w]
    if not words:
        words = [re.escape(keyword)]
    return re.compile(r"\b" + r"\s*".join(words) + r"\b", re.IGNORECASE)


def clean_name_prefix(text: str) -> str:
    """Trim a candidate value at the first run of 3+ digits, keeping only
    the leading name-shaped phrase. Handles OCR line-grouping accidentally
    merging a name with an adjacent unrelated field, e.g.
    "KAMAL CHIRANJILAL JAIN 05122022" -> "KAMAL CHIRANJILAL JAIN".
    """
    cleaned = text.strip()
    m = re.search(r"\d{3,}", cleaned)
    if m:
        cleaned = cleaned[: m.start()]
    return cleaned.strip(" ,.-:")


def find_label_candidates(
    lines: Sequence[Line],
    label_keywords: Sequence[str],
    value_validator: Optional[Callable[[str], bool]] = None,
    max_lines_below: int = 1,
    clean_fn: Optional[Callable[[str], str]] = None,
) -> List[Candidate]:
    """Return every plausible value found near one of ``label_keywords``.

    For each line containing a label keyword we look:
    1. After a ``:`` on the same line, or after the keyword itself.
    2. On the following line(s), when the label and value are stacked
       (common on Aadhaar / voter ID / driving licence layouts).

    ``clean_fn`` (when given) is applied to a candidate's raw text before
    validation/storage - used to strip garbage that OCR line-grouping
    occasionally merges onto the real value (see :func:`clean_name_prefix`).
    """
    candidates: List[Candidate] = []
    for idx, line in enumerate(lines):
        lower = line.text.lower()
        matched_kw = None
        match_end = None
        for kw in label_keywords:
            m = _compile_keyword(kw.lower()).search(lower)
            if m:
                matched_kw = kw
                match_end = m.end()
                break
        if matched_kw is None:
            continue

        # 1) value after ':' on the same line
        if ":" in line.text:
            _, _, after = line.text.partition(":")
            after = after.strip()
            candidate_val = clean_fn(after) if clean_fn else after
            if candidate_val and not is_noise_line(candidate_val):
                if value_validator is None or value_validator(candidate_val):
                    candidates.append(Candidate(candidate_val, line.confidence, idx, "same_line"))
        # 2) value on the same line, immediately after the matched keyword
        remainder = line.text[match_end:].strip(" :-\u2013")
        candidate_val = clean_fn(remainder) if clean_fn else remainder
        if candidate_val and not is_noise_line(candidate_val):
            if value_validator is None or value_validator(candidate_val):
                candidates.append(Candidate(candidate_val, line.confidence, idx, "same_line"))
        # 3) value on subsequent line(s)
        for offset in range(1, max_lines_below + 1):
            nxt = idx + offset
            if nxt >= len(lines):
                break
            raw_text = lines[nxt].text.strip()
            if not raw_text or is_noise_line(raw_text):
                continue
            if any(_compile_keyword(k.lower()).search(raw_text.lower()) for k in label_keywords):
                continue  # that's another label, not a value
            cand_text = clean_fn(raw_text) if clean_fn else raw_text
            if cand_text and (value_validator is None or value_validator(cand_text)):
                candidates.append(Candidate(cand_text, lines[nxt].confidence, nxt, "next_line"))
    return candidates


def best_candidate(candidates: Sequence[Candidate]) -> Optional[Candidate]:
    """Pick the highest-confidence candidate, preferring same-line matches."""
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c.source == "same_line", c.confidence, len(c.value)))


def field_from_candidates(candidates: Sequence[Candidate]) -> Optional[FieldValue]:
    best = best_candidate(candidates)
    if not best:
        return None
    return FieldValue(value=best.value.strip(), confidence=round(best.confidence, 4))


# ---------------------------------------------------------------------------
# Generic pattern scanning across the whole document (used as a fallback
# when label-based search does not find anything, e.g. a cropped document
# missing its header labels).
# ---------------------------------------------------------------------------
def scan_pattern(lines: Sequence[Line], pattern: re.Pattern) -> List[Candidate]:
    out: List[Candidate] = []
    for idx, line in enumerate(lines):
        for m in pattern.finditer(line.text):
            out.append(Candidate(m.group(0), line.confidence, idx, "pattern"))
    return out


def name_like(text: str) -> bool:
    """True if ``text`` looks like a human name (letters, spaces, dots only)."""
    cleaned = text.strip()
    if len(cleaned) < 3 or len(cleaned) > 60:
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", cleaned):
        return False
    if is_noise_line(cleaned):
        return False
    return True


def respace_using_reference(name: str, reference: str) -> str:
    """Re-insert spaces into a concatenated OCR name (e.g. "VRUSHABHKAMALJAIN")
    using another already-spaced name field on the same document as a hint
    (e.g. the father's name "KAMAL CHIRANJILAL JAIN" - Indian ID cards often
    share a first/surname, and print the two names in the same run-on font,
    so words that are readable in one field can locate word boundaries in
    the other).

    Only abnormally long space-separated tokens (>10 chars, i.e. likely two
    or more words OCR'd as one) are touched, so already correctly spaced
    names are never mangled by an incidental substring match.
    """
    if not name or not reference:
        return name
    ref_words = sorted({w for w in reference.split() if len(w) >= 3}, key=len, reverse=True)
    if not ref_words:
        return name
    out_tokens = []
    for token in name.split(" "):
        if len(token) <= 10:
            out_tokens.append(token)
            continue
        fixed = token
        for word in ref_words:
            idx = fixed.upper().find(word.upper())
            if idx == -1:
                continue
            fixed = f"{fixed[:idx]} {fixed[idx:idx + len(word)]} {fixed[idx + len(word):]}"
        out_tokens.append(fixed)
    return re.sub(r"\s+", " ", " ".join(out_tokens)).strip()



_TRAILING_PIN_RE = re.compile(r"[0-9oO]{6}$")


def fix_trailing_pincode(text: str) -> str:
    """Fix the common OCR letter/digit confusion ('O'/'o' read instead of
    '0') in a 6-digit Indian PIN code sitting at the very end of an address
    string (e.g. "...MH4ooo91" -> "...MH400091"). Only touches the last 6
    characters, and only when at least one of them is already a genuine
    digit, so ordinary trailing words are left untouched.
    """
    if not text:
        return text
    m = _TRAILING_PIN_RE.search(text)
    if not m or not any(ch.isdigit() for ch in m.group(0)):
        return text
    fixed = m.group(0).replace("o", "0").replace("O", "0")
    return text[: m.start()] + fixed


def despace_merged_words(text: str) -> str:
    """Best-effort re-insertion of spaces into address/name text where OCR
    glued several words together with none at all (common on small,
    all-caps printed fields such as a driving licence's address block,
    which - unlike mixed-case Aadhaar text - carries no case-boundary hint
    to lean on). Uses a statistical English word-segmentation dictionary
    (wordninja) on every alphabetic run longer than 10 characters,
    regardless of surrounding punctuation/digits, so commas and PIN codes
    glued onto the end of a run don't block the split. Normal words and
    place names that are already correctly spaced/short are left untouched.
    """
    if not text:
        return text
    try:
        import wordninja
    except ImportError:  # pragma: no cover - optional dependency guard
        return text

    def _replace(match: re.Match) -> str:
        word = match.group(0)
        pieces = wordninja.split(word)
        if len(pieces) <= 1:
            return word
        return " ".join(pieces)

    result = re.sub(r"[A-Za-z]{11,}", _replace, text)
    return re.sub(r"\s+", " ", result).strip()
