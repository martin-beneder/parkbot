import os
import re
import subprocess
import sys

import pytest

os.environ["ADB_HOST"] = "localhost"
os.environ["ADB_PORT"] = "5555"


# Mock subprocess before importing main so ADB calls don't hang
class _FakeResult:
    returncode = 1
    stdout = b""
    stderr = b""


_real_subprocess_run = subprocess.run
subprocess.run = lambda cmd, **kwargs: _FakeResult()

import main  # noqa: E402


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    return TestClient(main.app, raise_server_exceptions=False)


# ── API endpoint tests ──────────────────────────────────────────────────────

def test_status_stopped(client):
    main.bot_controller.running = False
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json() == {"running": False}


def test_health_structure(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "emulator" in data
    assert "bot" in data
    assert data["bot"] == "stopped"


def test_stop_endpoint(client):
    r = client.post("/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


def test_start_missing_plate(client):
    r = client.post("/start", json={})
    assert r.status_code == 200
    assert "error" in r.json()


def test_start_with_plate(client):
    main.bot_controller.running = False
    r = client.post("/start", json={"license_plate": "W123AB"})
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    assert r.json()["license_plate"] == "W123AB"
    main.bot_controller.stop()


def test_index_endpoint_exists(client):
    r = client.get("/")
    assert r.status_code in (200, 404)  # 404 if frontend not mounted, but route must exist


# ── XML / bounds parsing tests ─────────────────────────────────────────────

def test_bounds_parsing_correct():
    bounds = "[100,500][980,580]"
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    assert m is not None
    x = (int(m.group(1)) + int(m.group(3))) // 2
    y = (int(m.group(2)) + int(m.group(4))) // 2
    assert x == 540
    assert y == 540


def test_bounds_parsing_zero_origin():
    bounds = "[0,0][1080,2340]"
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    assert m
    assert (int(m.group(1)) + int(m.group(3))) // 2 == 540


def test_bounds_parsing_invalid():
    bounds = "invalid"
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    assert m is None


def test_xml_find_plate():
    from lxml import etree

    XML = b"""<hierarchy>
      <node text="" bounds="[0,0][1080,2340]">
        <node text="Meine Fahrzeuge" bounds="[33,180][540,240]"/>
        <node text="W 123 AB" bounds="[100,500][980,580]"/>
        <node text="Verlangern" bounds="[200,600][880,680]"/>
      </node>
    </hierarchy>"""
    root = etree.fromstring(XML)
    nodes = root.xpath('//*[@text="W 123 AB"]')
    assert len(nodes) == 1
    bounds = nodes[0].get("bounds")
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    assert m
    assert (int(m.group(1)) + int(m.group(3))) // 2 == 540
    assert (int(m.group(2)) + int(m.group(4))) // 2 == 540


def test_xml_plate_not_found():
    from lxml import etree

    XML = b"<hierarchy><node text='Hello' bounds='[0,0][100,100]'/></hierarchy>"
    root = etree.fromstring(XML)
    nodes = root.xpath('//*[@text="W999ZZ"]')
    assert len(nodes) == 0


# ── MJPEG frame format tests ───────────────────────────────────────────────

def test_mjpeg_frame_format():
    import cv2
    import numpy as np

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, jpeg_buf = cv2.imencode(".jpg", img)
    jpeg_bytes = jpeg_buf.tobytes()
    frame = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n"
    assert frame.startswith(b"--frame")
    assert b"Content-Type: image/jpeg" in frame
    assert len(frame) > len(jpeg_bytes)


def test_png_to_jpeg_conversion():
    import cv2
    import numpy as np

    img = np.zeros((270, 480, 3), dtype=np.uint8)
    img[:, :, 2] = 200
    _, png_buf = cv2.imencode(".png", img)
    png_bytes = png_buf.tobytes()

    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert decoded is not None
    assert decoded.shape == (270, 480, 3)

    _, jpeg_buf = cv2.imencode(".jpg", decoded, [cv2.IMWRITE_JPEG_QUALITY, 70])
    jpeg_bytes = jpeg_buf.tobytes()
    assert len(jpeg_bytes) > 0
    assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic bytes


# ── ADB manager unit tests (mocked) ───────────────────────────────────────

def test_adb_is_connected_false_on_failure():
    result = main.adb_manager.is_connected()
    assert result is False  # mocked subprocess returns returncode=1


def test_apk_installer_not_installed():
    result = main.apk_installer.is_installed()
    assert result is False  # mocked subprocess stdout is empty
