"""Extended backend API tests for PAN, Driving Licence, Voter ID, Generic doc,
multi-page PDF upload, and unsupported file type error handling.
Complements test_traveller_docs_api.py (Passport/Aadhaar/batch/dup/history/delete).
"""
import io
import pytest
import requests
from PIL import Image

from .conftest import _blank, _put, encode_png, BASE_URL

CREATED_IDS = []


def make_pan_image():
    img = _blank(1400, 900)
    _put(img, "INCOME TAX DEPARTMENT", (60, 60), 1.1, 3)
    _put(img, "GOVT OF INDIA", (60, 110), 0.9, 2)
    _put(img, "Permanent Account Number Card", (60, 160), 0.8, 2)
    _put(img, "ABCDE1234F", (60, 260), 1.3, 3)
    _put(img, "Name", (60, 340), 0.6, 1)
    _put(img, "RAHUL KUMAR", (60, 380), 0.9, 2)
    _put(img, "Father's Name", (60, 440), 0.6, 1)
    _put(img, "SURESH KUMAR", (60, 480), 0.9, 2)
    _put(img, "Date of Birth", (60, 540), 0.6, 1)
    _put(img, "01/01/1990", (60, 580), 0.9, 2)
    return img


def make_dl_image():
    img = _blank(1400, 900)
    _put(img, "UNION OF INDIA", (60, 60), 1.1, 3)
    _put(img, "DRIVING LICENCE", (60, 110), 1.0, 2)
    _put(img, "DL No: MH0120210012345", (60, 200), 0.8, 2)
    _put(img, "Name: ANITA SHARMA", (60, 260), 0.8, 2)
    _put(img, "DOB: 05/06/1988", (60, 320), 0.8, 2)
    _put(img, "Date of Issue: 15/02/2018", (60, 380), 0.8, 2)
    _put(img, "Valid Till: 14/02/2038", (60, 440), 0.8, 2)
    _put(img, "Address: 45 Park Street, Pune, MH", (60, 500), 0.8, 2)
    return img


def make_voter_id_image():
    img = _blank(1400, 900)
    _put(img, "ELECTION COMMISSION OF INDIA", (60, 60), 1.0, 3)
    _put(img, "IDENTITY CARD", (60, 110), 0.9, 2)
    _put(img, "ABC1234567", (60, 190), 1.2, 3)
    _put(img, "Elector's Name: SUNIL VERMA", (60, 260), 0.8, 2)
    _put(img, "Father's Name: RAMESH VERMA", (60, 320), 0.8, 2)
    _put(img, "Sex: MALE", (60, 380), 0.8, 2)
    return img


def make_generic_id_image():
    img = _blank(1400, 900)
    _put(img, "MINISTRY OF DEFENCE", (60, 60), 1.0, 3)
    _put(img, "SERVICE IDENTITY CARD", (60, 110), 0.9, 2)
    _put(img, "ID No: SVC998877", (60, 200), 0.8, 2)
    _put(img, "Name: VIKRAM RAO", (60, 260), 0.8, 2)
    _put(img, "DOB: 20/11/1980", (60, 320), 0.8, 2)
    _put(img, "Address: Cantonment Road, Jodhpur, RJ", (60, 380), 0.8, 2)
    _put(img, "Country: INDIA", (60, 440), 0.8, 2)
    return img


@pytest.fixture(scope="session")
def api_client():
    return requests.Session()


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


class TestPanUpload:
    def test_pan_upload(self, api_client, base_url):
        files = {"file": ("pan_test.png", io.BytesIO(encode_png(make_pan_image())), "image/png")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "COMPLETED", data.get("error")
        assert data["document_type"] == "PAN", f"got {data['document_type']}"
        CREATED_IDS.append(data["id"])


class TestDrivingLicenceUpload:
    def test_dl_upload(self, api_client, base_url):
        files = {"file": ("dl_test.png", io.BytesIO(encode_png(make_dl_image())), "image/png")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "COMPLETED", data.get("error")
        assert data["document_type"] == "DRIVING_LICENSE", f"got {data['document_type']}"
        CREATED_IDS.append(data["id"])


class TestVoterIdUpload:
    def test_voter_id_upload(self, api_client, base_url):
        files = {"file": ("voter_test.png", io.BytesIO(encode_png(make_voter_id_image())), "image/png")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "COMPLETED", data.get("error")
        assert data["document_type"] == "VOTER_ID", f"got {data['document_type']}"
        CREATED_IDS.append(data["id"])


class TestGenericUpload:
    def test_generic_upload(self, api_client, base_url):
        files = {"file": ("generic_test.png", io.BytesIO(encode_png(make_generic_id_image())), "image/png")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "COMPLETED", data.get("error")
        assert data["document_type"] == "GENERIC", f"got {data['document_type']}"
        CREATED_IDS.append(data["id"])


class TestMultiPagePdfUpload:
    def test_pdf_upload(self, api_client, base_url):
        img1 = Image.fromarray(make_pan_image())
        img2 = Image.fromarray(make_dl_image())
        buf = io.BytesIO()
        img1.save(buf, format="PDF", save_all=True, append_images=[img2])
        buf.seek(0)
        files = {"file": ("multipage.pdf", buf, "application/pdf")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=180)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "COMPLETED", data.get("error")
        CREATED_IDS.append(data["id"])


class TestUnsupportedFileType:
    def test_txt_upload_rejected(self, api_client, base_url):
        files = {"file": ("notes.txt", io.BytesIO(b"just some plain text"), "text/plain")}
        r = api_client.post(f"{base_url}/api/documents/upload", files=files, timeout=30)
        assert r.status_code == 400, f"Expected 400 for unsupported file type, got {r.status_code}: {r.text}"
        body = r.json()
        assert "detail" in body


@pytest.fixture(scope="session", autouse=True)
def _cleanup(api_client, base_url):
    yield
    for did in set(CREATED_IDS):
        try:
            api_client.delete(f"{base_url}/api/documents/{did}", timeout=10)
        except Exception:
            pass
