import asyncio
import logging
import os
import re
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from lxml import etree

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PACKAGE = "at.mobilkom.android.handyparken"
APK_URL = "https://d.apkpure.com/b/APK/at.mobilkom.android.handyparken?version=latest"
APK_PATH = "/apk/handyparken.apk"
FRONTEND_PATH = "/frontend/index.html"
ADB_HOST = os.environ.get("ADB_HOST", "emulator")
ADB_PORT = os.environ.get("ADB_PORT", "5555")
DEVICE = f"{ADB_HOST}:{ADB_PORT}"

connected_clients: set[WebSocket] = set()
_main_loop: Optional[asyncio.AbstractEventLoop] = None


async def _broadcast(message: str) -> None:
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


def log_and_broadcast(message: str) -> None:
    logger.info(message)
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast(message), _main_loop)


class ADBManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def connect(self) -> bool:
        try:
            result = subprocess.run(
                ["adb", "connect", DEVICE],
                capture_output=True, text=True, timeout=15
            )
            success = "connected" in result.stdout.lower() or "already connected" in result.stdout.lower()
            if success:
                log_and_broadcast(f"ADB connected to {DEVICE}")
            else:
                log_and_broadcast(f"ADB connect failed: {result.stdout.strip()} {result.stderr.strip()}")
            return success
        except Exception as e:
            log_and_broadcast(f"ADB connect error: {e}")
            return False

    def run(self, cmd: list[str], timeout: int = 30, capture: bool = True) -> Optional[subprocess.CompletedProcess]:
        full_cmd = ["adb", "-s", DEVICE] + cmd
        with self._lock:
            try:
                result = subprocess.run(
                    full_cmd,
                    capture_output=capture,
                    timeout=timeout
                )
                return result
            except subprocess.TimeoutExpired:
                log_and_broadcast(f"ADB timeout: {' '.join(cmd)}")
                return None
            except Exception as e:
                log_and_broadcast(f"ADB error: {e} — attempting reconnect")
                self.connect()
                try:
                    return subprocess.run(full_cmd, capture_output=capture, timeout=timeout)
                except Exception as e2:
                    log_and_broadcast(f"ADB reconnect failed: {e2}")
                    return None

    def is_connected(self) -> bool:
        result = self.run(["shell", "echo", "ok"], timeout=5)
        if result is None:
            return False
        return result.returncode == 0

    def screencap(self) -> Optional[bytes]:
        full_cmd = ["adb", "-s", DEVICE, "exec-out", "screencap", "-p"]
        with self._lock:
            try:
                result = subprocess.run(full_cmd, capture_output=True, timeout=10)
                if result.returncode == 0 and result.stdout:
                    return result.stdout
            except Exception as e:
                log_and_broadcast(f"Screencap error: {e}")
        return None


class APKInstaller:
    def __init__(self, adb: ADBManager) -> None:
        self.adb = adb

    def is_installed(self) -> bool:
        result = self.adb.run(["shell", "pm", "list", "packages", PACKAGE])
        if result is None:
            return False
        return PACKAGE in result.stdout.decode(errors="replace")

    def download_apk(self) -> bool:
        apk_path = Path(APK_PATH)
        apk_path.parent.mkdir(parents=True, exist_ok=True)
        if apk_path.exists() and apk_path.stat().st_size > 1_000_000:
            log_and_broadcast("APK already present locally.")
            return True
        log_and_broadcast(f"Downloading APK from {APK_URL} ...")
        try:
            resp = requests.get(APK_URL, timeout=120, stream=True,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            with open(APK_PATH, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            size = Path(APK_PATH).stat().st_size
            log_and_broadcast(f"APK downloaded: {size // 1024} KB")
            return size > 1_000_000
        except Exception as e:
            log_and_broadcast(f"APK download failed: {e}")
            return False

    def install(self) -> bool:
        if not Path(APK_PATH).exists():
            log_and_broadcast(f"APK not found at {APK_PATH}")
            return False
        log_and_broadcast("Installing APK ...")
        result = self.adb.run(["install", "-r", APK_PATH], timeout=120)
        if result and result.returncode == 0:
            log_and_broadcast("APK installed successfully.")
            return True
        err = result.stderr.decode(errors="replace") if result else "unknown"
        log_and_broadcast(f"APK install failed: {err}")
        return False

    def ensure_installed(self) -> None:
        log_and_broadcast("Checking if HANDYPARKEN is installed ...")
        if self.is_installed():
            log_and_broadcast("HANDYPARKEN already installed.")
            return
        log_and_broadcast("HANDYPARKEN not found — downloading ...")
        if not self.download_apk():
            log_and_broadcast("Using fallback: no APK available. Place APK in /apk/handyparken.apk and restart.")
            return
        for attempt in range(3):
            if self.install():
                return
            log_and_broadcast(f"Install attempt {attempt + 1} failed. Retrying ...")
            time.sleep(3)
        log_and_broadcast("All install attempts failed.")


class BotController:
    def __init__(self, adb: ADBManager) -> None:
        self.adb = adb
        self.running = False
        self.license_plate: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self, license_plate: str) -> None:
        if self.running:
            self.stop()
        self.license_plate = license_plate.upper().strip()
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log_and_broadcast(f"Bot started for Kennzeichen: {self.license_plate}")

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log_and_broadcast("Bot stopped.")

    def _loop(self) -> None:
        while self.running and not self._stop_event.is_set():
            try:
                self._start_app()
                time.sleep(3)
                self._dump_and_tap()
            except Exception as e:
                log_and_broadcast(f"Bot loop error: {e}")
            self._stop_event.wait(timeout=60)

    def _start_app(self) -> None:
        log_and_broadcast("Starte HANDYPARKEN App ...")
        result = self.adb.run(["shell", "monkey", "-p", PACKAGE, "1"])
        if result and result.returncode == 0:
            log_and_broadcast("App gestartet.")
        else:
            log_and_broadcast("App-Start fehlgeschlagen.")

    def _dump_ui(self) -> Optional[str]:
        for attempt in range(3):
            log_and_broadcast(f"UI-Dump erstellen (Versuch {attempt + 1}) ...")
            result = self.adb.run(["shell", "uiautomator", "dump", "/sdcard/view.xml"])
            if result is None or result.returncode != 0:
                log_and_broadcast("uiautomator dump fehlgeschlagen, retry ...")
                time.sleep(2)
                continue
            pull = self.adb.run(["pull", "/sdcard/view.xml", "/tmp/view.xml"])
            if pull is None or pull.returncode != 0:
                log_and_broadcast("adb pull fehlgeschlagen, retry ...")
                time.sleep(2)
                continue
            try:
                return Path("/tmp/view.xml").read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                log_and_broadcast(f"XML lesen fehlgeschlagen: {e}")
                time.sleep(2)
        return None

    def _parse_bounds(self, bounds_str: str) -> Optional[tuple[int, int]]:
        m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
        if not m:
            return None
        x = (int(m.group(1)) + int(m.group(3))) // 2
        y = (int(m.group(2)) + int(m.group(4))) // 2
        return x, y

    def _dump_and_tap(self) -> None:
        log_and_broadcast(f"Suche Kennzeichen: {self.license_plate} ...")
        deadline = time.time() + 30
        while time.time() < deadline and self.running:
            xml_content = self._dump_ui()
            if xml_content is None:
                log_and_broadcast("Timeout: XML konnte nicht geladen werden.")
                return
            try:
                root = etree.fromstring(xml_content.encode())
            except etree.XMLSyntaxError as e:
                log_and_broadcast(f"XML Parsefehler: {e}")
                time.sleep(2)
                continue
            nodes = root.xpath(f'//*[@text="{self.license_plate}"]')
            if not nodes:
                log_and_broadcast(f"Kennzeichen {self.license_plate} nicht gefunden. Warte ...")
                time.sleep(3)
                continue
            node = nodes[0]
            bounds = node.get("bounds", "")
            coords = self._parse_bounds(bounds)
            if coords is None:
                log_and_broadcast(f"Ungültige Bounds: {bounds}")
                return
            x, y = coords
            log_and_broadcast(f"Kennzeichen gefunden bei ({x},{y}). Klicke ...")
            tap_result = self.adb.run(["shell", "input", "tap", str(x), str(y)])
            if tap_result and tap_result.returncode == 0:
                log_and_broadcast("Klick erfolgreich.")
            else:
                log_and_broadcast("Klick fehlgeschlagen.")
            return
        log_and_broadcast(f"Timeout: Kennzeichen {self.license_plate} nicht gefunden.")


adb_manager = ADBManager()
apk_installer = APKInstaller(adb_manager)
bot_controller = BotController(adb_manager)


def _wait_for_emulator_and_install() -> None:
    """Retry ADB connection until the emulator is ready, then install APK."""
    log_and_broadcast("Warte auf Android-Emulator (kann 2-5 Minuten dauern) ...")
    for attempt in range(60):
        if adb_manager.connect():
            apk_installer.ensure_installed()
            return
        log_and_broadcast(f"Emulator noch nicht bereit, warte ... ({attempt + 1}/60)")
        time.sleep(10)
    log_and_broadcast("Emulator nicht erreichbar nach 10 Minuten. Bitte manuell prüfen.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_event_loop()
    log_and_broadcast("ParkBot startet ...")
    threading.Thread(target=_wait_for_emulator_and_install, daemon=True).start()
    yield
    bot_controller.stop()


app = FastAPI(title="ParkBot", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    try:
        return HTMLResponse(content=Path(FRONTEND_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Frontend not found. Mount frontend/ volume.</h1>", status_code=404)


@app.post("/start")
async def start_bot(body: dict):
    plate = body.get("license_plate", "").strip()
    if not plate:
        return {"error": "license_plate required"}
    bot_controller.start(plate)
    return {"status": "started", "license_plate": plate}


@app.post("/stop")
async def stop_bot():
    bot_controller.stop()
    return {"status": "stopped"}


@app.get("/status")
async def status():
    return {"running": bot_controller.running}


@app.get("/health")
async def health():
    emulator_status = "connected" if adb_manager.is_connected() else "disconnected"
    bot_status = "running" if bot_controller.running else "stopped"
    return {"emulator": emulator_status, "bot": bot_status}


async def _mjpeg_generator():
    while True:
        png_data = await asyncio.get_event_loop().run_in_executor(None, adb_manager.screencap)
        if png_data:
            arr = np.frombuffer(png_data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                _, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
        await asyncio.sleep(0.5)


@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    await websocket.send_text("WebSocket verbunden — Logs erscheinen hier.")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(websocket)
