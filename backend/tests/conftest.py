"""Shared fixtures for backend API tests."""
import io
import os
import pytest
import requests
import cv2
import numpy as np

# Use preview public URL (ingress routes /api/* to backend port 8001)
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://id-scanner-pro.preview.emergentagent.com").rstrip("/")


def _blank(w=1200, h=800):
    img = np.ones((h, w, 3), dtype=np.uint8) * 255
    return img


def _put(img, text, org, scale=0.9, thick=2):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick, cv2.LINE_AA)


def make_passport_image():
    """Synthetic passport-like image with MRZ."""
    img = _blank(1400, 900)
    _put(img, "REPUBLIC OF INDIA", (60, 60), 1.2, 3)
    _put(img, "PASSPORT", (60, 110), 1.0, 2)
    _put(img, "Type: P    Country Code: IND", (60, 170), 0.8, 2)
    _put(img, "Passport No.: A1234567", (60, 220), 0.8, 2)
    _put(img, "Surname: DOE", (60, 280), 0.8, 2)
    _put(img, "Given Names: JOHN MICHAEL", (60, 330), 0.8, 2)
    _put(img, "Nationality: INDIAN", (60, 380), 0.8, 2)
    _put(img, "Date of Birth: 15/03/1985", (60, 430), 0.8, 2)
    _put(img, "Sex: M", (60, 480), 0.8, 2)
    _put(img, "Place of Birth: DELHI", (60, 530), 0.8, 2)
    _put(img, "Date of Issue: 10/01/2020", (60, 580), 0.8, 2)
    _put(img, "Date of Expiry: 09/01/2030", (60, 630), 0.8, 2)
    # MRZ (2 lines of 44 chars)
    _put(img, "P<INDDOE<<JOHN<MICHAEL<<<<<<<<<<<<<<<<<<<<<<", (60, 780), 0.7, 2)
    _put(img, "A1234567<4IND8503155M3001091<<<<<<<<<<<<<<<0", (60, 830), 0.7, 2)
    return img


def make_aadhaar_image():
    img = _blank(1400, 900)
    _put(img, "GOVERNMENT OF INDIA", (60, 70), 1.2, 3)
    _put(img, "UIDAI", (60, 130), 1.0, 2)
    _put(img, "Name: PRIYA SINGH", (60, 220), 0.9, 2)
    _put(img, "DOB: 12/07/1992", (60, 280), 0.9, 2)
    _put(img, "Gender: FEMALE", (60, 340), 0.9, 2)
    _put(img, "1234 5678 9012", (60, 440), 1.3, 3)
    _put(img, "Address: 123 MG Road, Bangalore, KA", (60, 540), 0.8, 2)
    return img


def encode_png(img):
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api_client():
    s = requests.Session()
    return s


@pytest.fixture(scope="session")
def passport_bytes():
    return encode_png(make_passport_image())


@pytest.fixture(scope="session")
def aadhaar_bytes():
    return encode_png(make_aadhaar_image())
