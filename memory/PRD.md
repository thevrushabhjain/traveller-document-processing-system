# Traveller Document Processing System - PRD

## Original problem statement
Rebuild and stabilize an existing "Traveller Document Processing" project into a
production-ready application, preserving the existing UI/UX. FastAPI backend +
Next.js frontend. Support Passport, Aadhaar, Driving Licence, PAN, Voter ID, and
any other government ID via a Generic Document Extractor. Accept JPG/JPEG/PNG/PDF
(multi-page). Auto-detect document type. Fully offline OCR via PaddleOCR (latest
API, no deprecated args), with CPU support, image preprocessing (deskew, denoise,
CLAHE, adaptive threshold, resize), intelligent token merging and confidence
scoring. Layout-based (not rigid regex) extraction for all fields (name, DOB,
gender, nationality, address, document number, dates, issuing authority, country,
father's name, metadata) using OCR bounding boxes, line/block grouping, reading
order, candidate scoring and noise filtering. Validation per document type
(Aadhaar Verhoeff, PAN/passport/DL formats, dates). Batch processing (multi-file,
multi-page, mixed types) returning one JSON per document. Duplicate detection via
document number (exact) + name/DOB (fuzzy, configurable threshold). Backend:
FastAPI + SQLAlchemy + PostgreSQL (SQLite fallback) + Pydantic v2, async, modular,
exception-handled, unit-test friendly. Frontend: drag & drop, batch upload,
progress, JSON viewer, confidence visualization, JSON download. Deployment:
Dockerfile, docker-compose, Railway, Render, Vercel, documented env vars.

## User choices (from initial clarification)
- Database: real PostgreSQL provisioned in this sandbox (with SQLite auto-fallback
  kept in code for portability)
- OCR: PaddleOCR strictly required (the app's built-in automatic fallback to
  rapidocr-onnxruntime - same PP-OCR model weights via ONNX - is used here because
  native PaddlePaddle inference segfaults reliably on this sandbox's CPU; this
  fallback was already part of the codebase's design and is transparent to users)
- Frontend: standalone Next.js app (not the sandbox's default CRA template)
- Sample images: user to provide own test images
- Delivery: run live in this environment (platform's Save-to-GitHub/download
  handles "the ZIP" requirement) rather than a manually produced zip file

## Architecture
- Backend: FastAPI (`/app/backend`, entry `server.py` -> `app.main:app`), served on
  port 8001 internally (mapped through the platform's `/api` ingress prefix)
- Frontend: Next.js 14 App Router (`/app/frontend`), served on port 3000, calls the
  backend directly via `NEXT_PUBLIC_API_BASE_URL`
- DB: PostgreSQL (`traveldocs` db/user, local install in this sandbox via
  `/app/scripts/provision_environment.sh`), SQLAlchemy 2.0 ORM, JSONB result column
- OCR: `app/services/ocr_service.py` - PaddleOCR primary, rapidocr-onnxruntime
  automatic fallback (subprocess self-test detects native crashes before they can
  take down the app)
- Shared layout engine: `app/services/layout.py` (line grouping from bounding
  boxes, label-proximity candidate search, noise filtering) used by every
  extractor in `app/extractors/{passport,aadhaar,pan,driving_license,voter_id,
  generic}.py`
- Classification: `app/services/classifier.py` (weighted keyword + structural
  pattern scoring, falls back to GENERIC below a confidence threshold)
- Validation: `app/services/validator.py` (Verhoeff for Aadhaar, format checks for
  PAN/DL/Voter ID/Passport, date sanity)
- Duplicate detection: `app/services/duplicate_detector.py` (exact on document
  number, fuzzy on name+DOB via rapidfuzz, threshold configurable via
  `FUZZY_MATCH_THRESHOLD`)

## What's been implemented (2026-02, this session)
- Expanded document types from (Passport, Aadhaar, Unknown) to full set: Passport,
  Aadhaar, Driving Licence, PAN, Voter ID, Generic (+ Unknown reserved for hard
  processing failures)
- New shared `layout.py` module: OCR bounding-box line grouping, noise/boilerplate
  filtering, label-candidate scoring, used across all extractors
- New extractors: `pan.py`, `driving_license.py`, `voter_id.py`; rewrote
  `generic.py` to use layout analysis instead of naive heuristics; upgraded
  `aadhaar.py` (better address block detection, noise-aware name lookup)
- Rewrote `classifier.py` with keyword + structural-pattern scoring for all 5
  known types, GENERIC fallback
- Fixed a real MRZ date-parsing bug (day/month century ambiguity) and an
  Aadhaar-address over-collection bug and a Voter-ID name/father-name
  cross-contamination bug (all found via manual + automated testing)
- Added PAN/DL/Voter-ID validation rules to `validator.py`
- Backend: provisioned real PostgreSQL (system-level, see
  `scripts/provision_environment.sh` for reprovisioning after sandbox restarts),
  wired via `DATABASE_URL`, SQLite fallback preserved
- Frontend: converted sandbox to run a real Next.js dev server via supervisor
  (`package.json` "start" -> `next dev`; `start:prod` for Docker), added Country
  and Additional-Metadata fields to `ExtractedData.js`, expanded document type
  list in `HistoryTable.js`, fixed a stale-closure bug in the history type filter
- Deployment: `docker-compose.yml`, `railway.json`, `render.yaml`,
  `frontend/vercel.json`, `.env.example` for both services, full README rewrite
- Testing: 18/18 backend pytest tests pass; full UI regression pass via testing
  agent (single upload x5 types, batch upload mixed types, duplicate detection,
  history filter/search/delete, rotated image handling, bad file type rejection)

## Known environment limitation (sandbox-specific, not an app bug)
Everything under `/app` persists across a sandbox container restart; system-level
packages (PostgreSQL, Poppler) installed via `apt` live outside `/app` and are
wiped on a restart. If Postgres/PDF conversion stop working after a restart, run:
`bash /app/scripts/provision_environment.sh`. This has no bearing on real
deployments (Docker/Railway/Render use persistent volumes / managed Postgres).

## Backlog / next steps (P1/P2)
- P1: Add automated retry/backoff for transient Postgres connection drops
- P2: Optional debounced live search in HistoryTable (currently Enter-to-search)
- P2: PDF page-by-page preview navigation in `DocumentPreview.js`
- P2: Configurable OCR language list from the UI (currently env-var only)
- P3: Export a combined CSV/Excel of a full batch (currently per-document JSON)

## Bug-fix round (2026-02, real user documents)
User uploaded their own Aadhaar (PDF), PAN and Driving Licence photos and reported
extraction was wrong (concatenated names, mixed-up dates, garbled/truncated
addresses). Root causes found and fixed:
- **Orientation detection was fundamentally unreliable**: the old pixel-variance
  heuristic in `preprocess.py` cannot distinguish a right-side-up page from an
  upside-down one (both produce identical row-projection profiles). Replaced with
  `ocr_service._auto_orient_with_ocr`, which actually runs the OCR engine at
  0/90/180/270 degrees on a downscaled copy and scores each by
  (confidence x text length x landscape-shaped-bounding-box weight), then applies
  the winning rotation to the full-resolution image once.
- **Label matching broke when OCR dropped spaces between words** (e.g. "Date of
  Birth" -> "DateofBirth", very common on compressed/small-font real photos).
  `layout._compile_keyword` now matches labels with optional whitespace between
  their constituent words instead of requiring an exact literal substring.
- **OCR line-grouping glued unrelated fields together**: a driving licence's
  sideways-printed "Date of First Issue" caption (genuinely rotated 90 deg on the
  physical card) has a very tall bounding box that coincidentally overlapped
  the "Name" line's vertical position, merging the two. `layout.group_lines` now
  isolates abnormally tall/thin tokens as their own line instead of clustering
  them by centre-y.
- **Concatenated all-caps names** (e.g. "VRUSHABHKAMALJAIN" on a PAN card, no
  case-boundary hint available): added `layout.respace_using_reference`, which
  uses another already-spaced name field on the same document (e.g. father's
  name) to locate word boundaries in the merged one.
- **Concatenated mixed-case names** (e.g. "VrushabhKamal Jain" on an Aadhaar
  e-letter, which *does* preserve per-word capitalisation): `parser._normalize_names`
  now splits at lower->upper case transitions before uppercasing everything.
- **Concatenated address words with no case or reference hint** (all-caps DL
  address block): added `layout.despace_merged_words`, a statistical
  word-segmentation pass (via `wordninja`) applied to any alphabetic run longer
  than 10 characters. Not perfect for Indian proper nouns/place names, but a
  large readability improvement over a fully concatenated string.
- **OCR line-grouping merging a value with an adjacent unrelated field on the
  same visual row** (e.g. "DateofBirth:27-11-2004 Blood Group: Organ Donor:N" as
  one OCR line): added `layout.clean_name_prefix` (truncate a name candidate at
  the first digit) and a `_extract_date` clean_fn in `driving_license.py` (pull
  just the regex-matched date out of a merged candidate string) instead of
  storing the whole raw remainder.
- **Multi-page PDFs mixed unrelated content from different pages**: `api.py`
  now offsets each subsequent page's token Y-coordinates by a large constant
  before combining them, so `group_lines` never treats text from page 1 and
  page 2 as being on the "same line" just because they share a pixel height.
- **Driving Licence had no father's/guardian's-name extraction at all**: added
  Son/Daughter/Wife-of label support.
- Fixed an Aadhaar address-block bug that only collected up to 4 lines above the
  PIN code (too narrow for the "Aadhaar letter" layout, which has more address
  lines than the compact card) and could pick up the "S/O ..." line as part of
  the address; window widened and a leading relative-prefix strip added.
- Verified via 4 real user-provided documents (2 Aadhaar PDFs, 1 PAN, 1 DL) end
  to end through the actual upload API - all fields now extract correctly.
  18/18 backend pytest tests still pass; testing agent found 2 additional issues
  in this round (DL address word-splitting, a rotation edge case) - both fixed
  and re-verified.
