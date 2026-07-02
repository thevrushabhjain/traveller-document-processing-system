"""Backend API tests for Traveller Docs Processing System."""
import io
import time
import pytest
import requests

# Cleanup registry of created IDs across tests
CREATED_IDS: list[str] = []


# --- Health -----------------------------------------------------------------
class TestHealth:
    def test_health_ok(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/health", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "ok"
        assert data["ocr_ready"] is True
        assert data["database"] in ("sqlite", "postgresql")
        assert "en" in data["languages"] and "hi" in data["languages"]
        assert data["version"]


# --- Single upload: passport ------------------------------------------------
class TestPassportUpload:
    def test_passport_upload(self, api_client, base_url, passport_bytes):
        files = {"file": ("passport_test.png", io.BytesIO(passport_bytes), "image/png")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "COMPLETED", data.get("error")
        assert data["document_type"] == "PASSPORT", f"got {data['document_type']}"
        # OCR quality is synthetic; require a reasonable floor
        assert data["overall_confidence"] > 0.5, data
        trav = data["traveller"]
        # Key fields should be populated (value present)
        for field in ("document_number", "full_name", "date_of_birth", "date_of_expiry", "gender", "nationality"):
            fv = trav.get(field)
            assert fv is not None, f"{field} missing"
            assert fv.get("value"), f"{field} has empty value: {fv}"
            assert isinstance(fv.get("confidence"), (int, float))
        CREATED_IDS.append(data["id"])
        # Store the id for the duplicate test
        pytest.passport_doc_id = data["id"]


# --- Single upload: aadhaar -------------------------------------------------
class TestAadhaarUpload:
    def test_aadhaar_upload(self, api_client, base_url, aadhaar_bytes):
        files = {"file": ("aadhaar_test.png", io.BytesIO(aadhaar_bytes), "image/png")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "COMPLETED", data.get("error")
        assert data["document_type"] == "AADHAAR", f"got {data['document_type']}"
        trav = data["traveller"]
        # Document number should be a 12-digit string
        assert trav.get("document_number") and trav["document_number"].get("value")
        docnum = trav["document_number"]["value"].replace(" ", "")
        assert len(docnum) == 12 and docnum.isdigit(), f"Aadhaar number malformed: {docnum}"
        # Name populated and not literal 'AADHAAR'
        assert trav.get("full_name") and trav["full_name"].get("value")
        assert trav["full_name"]["value"].upper() != "AADHAAR"
        # Issuing authority + nationality
        if trav.get("issuing_authority"):
            assert "UIDAI" in trav["issuing_authority"]["value"].upper()
        if trav.get("nationality"):
            assert "INDIAN" in trav["nationality"]["value"].upper()
        CREATED_IDS.append(data["id"])


# --- Batch ------------------------------------------------------------------
class TestBatchUpload:
    def test_batch_mixed(self, api_client, base_url, passport_bytes, aadhaar_bytes):
        files = [
            ("files", ("p1.png", io.BytesIO(passport_bytes), "image/png")),
            ("files", ("a1.png", io.BytesIO(aadhaar_bytes), "image/png")),
        ]
        r = api_client.post(f"{base_url}/api/documents/batch", files=files, timeout=180)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] == 2
        assert data["completed"] + data["failed"] == 2
        assert data["completed"] >= 1
        assert len(data["results"]) == 2
        # order preserved
        assert data["results"][0]["filename"] == "p1.png"
        assert data["results"][1]["filename"] == "a1.png"
        for r_item in data["results"]:
            CREATED_IDS.append(r_item["id"])


# --- Duplicate detection ----------------------------------------------------
class TestDuplicateDetection:
    def test_duplicate_exact(self, api_client, base_url, passport_bytes):
        # Upload once (baseline). Reuse passport_doc_id if available.
        first_id = getattr(pytest, "passport_doc_id", None)
        if not first_id:
            files = {"file": ("passport_dup1.png", io.BytesIO(passport_bytes), "image/png")}
            r1 = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
            assert r1.status_code == 200
            first_id = r1.json()["id"]
            CREATED_IDS.append(first_id)

        # Upload again -> expect duplicate
        files2 = {"file": ("passport_dup2.png", io.BytesIO(passport_bytes), "image/png")}
        r2 = api_client.post(f"{base_url}/api/documents/upload", files=files2, timeout=120)
        assert r2.status_code == 200, r2.text
        data = r2.json()
        CREATED_IDS.append(data["id"])
        dups = data.get("duplicates", [])
        assert len(dups) >= 1, f"Expected at least one duplicate. Got: {dups}"
        exact = [d for d in dups if d["match_type"] == "EXACT"]
        assert len(exact) >= 1, f"Expected an EXACT match. Got: {dups}"
        assert exact[0]["score"] == 1.0
        assert "document_number" in exact[0]["matched_on"]


# --- List / history ---------------------------------------------------------
class TestListDocuments:
    def test_list_paginated(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/documents?limit=5&offset=0", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "total" in data and "items" in data
        assert data["total"] >= 1
        assert len(data["items"]) <= 5

    def test_list_filter_by_type(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/documents?document_type=PASSPORT&limit=50", timeout=30)
        assert r.status_code == 200
        data = r.json()
        for item in data["items"]:
            assert item["document_type"] == "PASSPORT"

    def test_list_search(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/documents?search=DOE&limit=50", timeout=30)
        assert r.status_code == 200
        assert r.json()["total"] >= 0  # search does not error


# --- Retrieve + JSON download ----------------------------------------------
class TestDocumentRetrieval:
    def test_get_by_id(self, api_client, base_url):
        assert CREATED_IDS, "no docs created earlier"
        doc_id = CREATED_IDS[0]
        r = api_client.get(f"{base_url}/api/documents/{doc_id}", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == doc_id
        assert "traveller" in data

    def test_get_json_download(self, api_client, base_url):
        doc_id = CREATED_IDS[0]
        r = api_client.get(f"{base_url}/api/documents/{doc_id}/json", timeout=30)
        assert r.status_code == 200
        assert "attachment" in r.headers.get("content-disposition", "").lower()
        body = r.json()
        assert body.get("id") == doc_id

    def test_get_not_found(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/documents/does-not-exist-xyz", timeout=30)
        assert r.status_code == 404


# --- Delete -----------------------------------------------------------------
class TestDelete:
    def test_delete(self, api_client, base_url, passport_bytes):
        # Create a fresh document to delete
        files = {"file": ("todelete.png", io.BytesIO(passport_bytes), "image/png")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
        assert r.status_code == 200
        doc_id = r.json()["id"]

        r2 = api_client.delete(f"{base_url}/api/documents/{doc_id}", timeout=30)
        assert r2.status_code == 200
        assert r2.json().get("message") == "deleted"

        r3 = api_client.get(f"{base_url}/api/documents/{doc_id}", timeout=30)
        assert r3.status_code == 404


# --- Cleanup module-level (best-effort) ------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _cleanup(api_client, base_url):
    yield
    for did in set(CREATED_IDS):
        try:
            api_client.delete(f"{base_url}/api/documents/{did}", timeout=10)
        except Exception:
            pass
