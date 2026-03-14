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
from lxml import html as lxhtml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PACKAGE = "at.mobilkom.android.handyparken"
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


# ── ADB ──────────────────────────────────────────────────────────────────────

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
                log_and_broadcast(f"ADB verbunden mit {DEVICE}")
            else:
                log_and_broadcast(f"ADB connect fehlgeschlagen: {result.stdout.strip()} {result.stderr.strip()}")
            return success
        except Exception as e:
            log_and_broadcast(f"ADB connect Fehler: {e}")
            return False

    def run(self, cmd: list[str], timeout: int = 30, capture: bool = True) -> Optional[subprocess.CompletedProcess]:
        full_cmd = ["adb", "-s", DEVICE] + cmd
        with self._lock:
            try:
                result = subprocess.run(full_cmd, capture_output=capture, timeout=timeout)
                return result
            except subprocess.TimeoutExpired:
                log_and_broadcast(f"ADB Timeout: {' '.join(cmd)}")
                return None
            except Exception as e:
                log_and_broadcast(f"ADB Fehler: {e} — versuche Reconnect")
                self.connect()
                try:
                    return subprocess.run(full_cmd, capture_output=capture, timeout=timeout)
                except Exception as e2:
                    log_and_broadcast(f"ADB Reconnect fehlgeschlagen: {e2}")
                    return None

    def is_connected(self) -> bool:
        result = self.run(["shell", "echo", "ok"], timeout=5)
        return result is not None and result.returncode == 0

    def screencap(self) -> Optional[bytes]:
        full_cmd = ["adb", "-s", DEVICE, "exec-out", "screencap", "-p"]
        with self._lock:
            try:
                result = subprocess.run(full_cmd, capture_output=True, timeout=10)
                if result.returncode == 0 and result.stdout:
                    return result.stdout
            except Exception as e:
                log_and_broadcast(f"Screencap Fehler: {e}")
        return None


# ── APK Installer ─────────────────────────────────────────────────────────────

class APKInstaller:
    def __init__(self, adb: ADBManager) -> None:
        self.adb = adb
        self.status: str = "pending"  # pending | downloading | installing | installed | failed

    def is_installed(self) -> bool:
        result = self.adb.run(["shell", "pm", "list", "packages", PACKAGE])
        if result is None:
            return False
        return PACKAGE in result.stdout.decode(errors="replace")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _stream_to_file(self, resp: requests.Response) -> bool:
        ct = resp.headers.get("Content-Type", "").lower()
        if "text/html" in ct:
            return False
        content_length = int(resp.headers.get("Content-Length", 0))
        if content_length and content_length < 1_000_000:
            return False
        total = 0
        Path(APK_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(APK_PATH, "wb") as f:
            for chunk in resp.iter_content(1 << 16):
                f.write(chunk)
                total += len(chunk)
                if total % (5 << 20) == 0:  # log every 5 MB
                    log_and_broadcast(f"APK Download: {total // (1 << 20)} MB ...")
        if total < 1_000_000:
            Path(APK_PATH).unlink(missing_ok=True)
            return False
        return True

    @staticmethod
    def _get(session: requests.Session, url: str, referer: str = "",
             stream: bool = False, mobile: bool = False) -> requests.Response:
        ua = (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36"
            if mobile else
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        headers = {"User-Agent": ua}
        if referer:
            headers["Referer"] = referer
        return session.get(url, headers=headers, timeout=30,
                           stream=stream, allow_redirects=True)

    def _try_hrefs(self, session: requests.Session, hrefs: list[str],
                   referer: str, base: str = "") -> bool:
        for href in hrefs:
            if href.startswith("/") and base:
                href = base + href
            if not href.startswith("http"):
                continue
            r = self._get(session, href, referer=referer, stream=True)
            if self._stream_to_file(r):
                return True
        return False

    # ── source 1: APKPure ─────────────────────────────────────────────────────

    def _dl_apkpure(self) -> bool:
        s = requests.Session()
        base = "https://apkpure.com"
        app_url = f"{base}/handyparken/{PACKAGE}"
        dl_url = f"{app_url}/download"

        s.get(app_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)  # seed cookies
        r = self._get(s, dl_url, referer=app_url)
        r.raise_for_status()
        tree = lxhtml.fromstring(r.content)

        hrefs = (
            tree.xpath('//*[@id="download_link"]/@href') +
            tree.xpath('//a[contains(@class,"download-start")]/@href') +
            tree.xpath('//a[contains(@href,"d.apkpure.com")]/@href') +
            tree.xpath('//*[@data-dt-url]/@data-dt-url') +
            tree.xpath('//a[contains(@href,".apk")]/@href')
        )
        if self._try_hrefs(s, hrefs, referer=dl_url, base=base):
            return True

        # Regex: catch any APKPure CDN URL embedded in HTML/JS
        for m in re.finditer(r'https://(?:d|cdn\d*)\.apkpure\.(?:com|net)[^\s"\'<>]+', r.text):
            r2 = self._get(s, m.group(), referer=dl_url, stream=True)
            if self._stream_to_file(r2):
                return True

        # Direct URL variations
        for url in [
            f"https://d.apkpure.com/b/APK/{PACKAGE}?version=latest",
            f"https://d.apkpure.com/b/APK/{PACKAGE}?version=latest&nc=1&nf=1",
        ]:
            r3 = self._get(s, url, referer=dl_url, stream=True)
            if self._stream_to_file(r3):
                return True

        return False

    # ── source 2: Uptodown ────────────────────────────────────────────────────

    def _dl_uptodown(self) -> bool:
        s = requests.Session()
        base = "https://handyparken.at.uptodown.com"

        r = self._get(s, f"{base}/android")
        r.raise_for_status()
        tree = lxhtml.fromstring(r.content)

        hrefs = (
            tree.xpath('//*[@id="detail-download-button"]/@href') +
            tree.xpath('//*[@data-url]/@data-url') +
            tree.xpath('//a[contains(@href,".apk")]/@href')
        )
        if self._try_hrefs(s, hrefs, referer=base, base=base):
            return True

        r2 = self._get(s, f"{base}/android/download", referer=f"{base}/android")
        tree2 = lxhtml.fromstring(r2.content)
        hrefs2 = tree2.xpath('//a[contains(@href,".apk")]/@href')
        return self._try_hrefs(s, hrefs2, referer=r2.url, base=base)

    # ── source 3: APKCombo ────────────────────────────────────────────────────

    def _dl_apkcombo(self) -> bool:
        s = requests.Session()
        base = "https://apkcombo.com"
        page = f"{base}/handyparken/{PACKAGE}/download/apk"

        r = self._get(s, page)
        r.raise_for_status()
        tree = lxhtml.fromstring(r.content)

        hrefs = (
            tree.xpath('//a[contains(@class,"variant")]/@href') +
            tree.xpath('//a[contains(@href,".apk")]/@href') +
            tree.xpath('//a[contains(@class,"download")]/@href')
        )
        if self._try_hrefs(s, hrefs, referer=page, base=base):
            return True

        for m in re.finditer(r'https://[^\s"\'<>]+\.apk[^\s"\'<>]*', r.text):
            r2 = self._get(s, m.group(), referer=page, stream=True)
            if self._stream_to_file(r2):
                return True

        return False

    # ── source 4: APKMonk ─────────────────────────────────────────────────────

    def _dl_apkmonk(self) -> bool:
        s = requests.Session()
        base = "https://www.apkmonk.com"
        page = f"{base}/app/{PACKAGE}/"

        try:
            r = self._get(s, page)
            r.raise_for_status()
            tree = lxhtml.fromstring(r.content)
            for href in tree.xpath('//a[contains(@href,"download")]/@href'):
                if href.startswith("/"):
                    href = base + href
                if not href.startswith("http"):
                    continue
                r2 = self._get(s, href, referer=page)
                tree2 = lxhtml.fromstring(r2.content)
                for dl_href in tree2.xpath('//a[contains(@href,".apk")]/@href'):
                    r3 = self._get(s, dl_href, referer=href, stream=True)
                    if self._stream_to_file(r3):
                        return True
        except Exception:
            pass
        return False

    # ── orchestration ─────────────────────────────────────────────────────────

    def download_apk(self) -> bool:
        apk_path = Path(APK_PATH)
        apk_path.parent.mkdir(parents=True, exist_ok=True)
        if apk_path.exists() and apk_path.stat().st_size > 1_000_000:
            log_and_broadcast("APK bereits vorhanden.")
            return True

        self.status = "downloading"
        for name, fn in [
            ("APKPure",  self._dl_apkpure),
            ("Uptodown", self._dl_uptodown),
            ("APKCombo", self._dl_apkcombo),
            ("APKMonk",  self._dl_apkmonk),
        ]:
            log_and_broadcast(f"APK Download: versuche {name} ...")
            try:
                if fn():
                    size = Path(APK_PATH).stat().st_size
                    log_and_broadcast(f"APK von {name} heruntergeladen: {size // 1024} KB")
                    return True
            except Exception as e:
                log_and_broadcast(f"{name} fehlgeschlagen: {e}")

        log_and_broadcast("Alle Download-Quellen fehlgeschlagen.")
        return False

    def install(self) -> bool:
        if not Path(APK_PATH).exists():
            log_and_broadcast(f"APK nicht unter {APK_PATH} gefunden.")
            return False
        log_and_broadcast("Installiere HANDYPARKEN APK ...")
        self.status = "installing"
        result = self.adb.run(["install", "-r", APK_PATH], timeout=120)
        if result and result.returncode == 0:
            log_and_broadcast("APK erfolgreich installiert.")
            self.status = "installed"
            return True
        err = result.stderr.decode(errors="replace") if result else "unbekannt"
        log_and_broadcast(f"Installation fehlgeschlagen: {err}")
        self.status = "failed"
        return False

    def ensure_installed(self) -> None:
        log_and_broadcast("Prüfe HANDYPARKEN Installation ...")
        if self.is_installed():
            log_and_broadcast("HANDYPARKEN bereits installiert.")
            self.status = "installed"
            return

        log_and_broadcast("HANDYPARKEN nicht gefunden — starte Download ...")
        if not self.download_apk():
            self.status = "failed"
            log_and_broadcast("Kein APK verfügbar. Bitte /apk/handyparken.apk manuell ablegen.")
            return

        for attempt in range(3):
            if self.install():
                return
            log_and_broadcast(f"Installationsversuch {attempt + 1} fehlgeschlagen. Wiederhole ...")
            time.sleep(3)

        self.status = "failed"
        log_and_broadcast("Alle Installationsversuche fehlgeschlagen.")


# ── Bot Controller ────────────────────────────────────────────────────────────

# ── Credentials ───────────────────────────────────────────────────────────────

class Credentials:
    """Holds login credentials in memory. Pre-loaded from env vars."""

    def __init__(self) -> None:
        self.login: str = os.environ.get("HP_LOGIN", "")
        self.password: str = os.environ.get("HP_PASSWORD", "")

    @property
    def is_set(self) -> bool:
        return bool(self.login and self.password)

    @property
    def display(self) -> str:
        """Masked version safe to show in UI."""
        if not self.login:
            return "(nicht gesetzt)"
        if "@" in self.login:
            user, domain = self.login.split("@", 1)
            return f"{user[:2]}***@{domain}"
        return f"{self.login[:3]}***"


# ── Bot Controller ─────────────────────────────────────────────────────────────

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
        log_and_broadcast(f"Bot gestartet für Kennzeichen: {self.license_plate}")

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log_and_broadcast("Bot gestoppt.")

    # ── main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self.running and not self._stop_event.is_set():
            try:
                if not apk_installer.is_installed():
                    st = apk_installer.status
                    wait = 60 if st == "failed" else 15
                    log_and_broadcast(f"Warte auf App-Installation (Status: {st}) ...")
                    self._stop_event.wait(timeout=wait)
                    continue

                self._start_app()
                time.sleep(3)
                self._run_cycle()
            except Exception as e:
                log_and_broadcast(f"Bot Fehler: {e}")
            self._stop_event.wait(timeout=60)

    # ── app launch ────────────────────────────────────────────────────────────

    def _resolve_activity(self) -> Optional[str]:
        result = self.adb.run(
            ["shell", "cmd", "package", "resolve-activity", "--brief", PACKAGE], timeout=10
        )
        if result is None or result.returncode != 0:
            return None
        for line in result.stdout.decode(errors="replace").splitlines():
            line = line.strip()
            if "/" in line and not line.startswith("priority"):
                return line
        return None

    def _start_app(self) -> None:
        log_and_broadcast("Starte HANDYPARKEN App ...")
        component = self._resolve_activity()
        if component:
            r = self.adb.run(["shell", "am", "start", "-n", component])
            if r and r.returncode == 0:
                log_and_broadcast("App gestartet.")
                return
        r = self.adb.run(["shell", "monkey", "-p", PACKAGE, "1"])
        if r and r.returncode == 0:
            log_and_broadcast("App gestartet (monkey).")
        else:
            log_and_broadcast("App-Start fehlgeschlagen.")

    # ── UI dump ───────────────────────────────────────────────────────────────

    def _dump_ui(self) -> Optional[str]:
        for attempt in range(3):
            log_and_broadcast(f"UI-Dump (Versuch {attempt + 1}) ...")
            r = self.adb.run(["shell", "uiautomator", "dump", "/sdcard/view.xml"])
            if r is None or r.returncode != 0:
                time.sleep(2)
                continue
            pull = self.adb.run(["pull", "/sdcard/view.xml", "/tmp/view.xml"])
            if pull is None or pull.returncode != 0:
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
        return (int(m.group(1)) + int(m.group(3))) // 2, (int(m.group(2)) + int(m.group(4))) // 2

    # ── text input helpers ────────────────────────────────────────────────────

    def _type_text(self, text: str) -> None:
        """Type text into the currently focused field.

        Wraps text in single quotes for the Android shell so that special
        characters like $, !, &, | are treated literally.
        Spaces are encoded as %s (ADB input text convention).
        Single quotes inside the text are shell-escaped with '\\'' .
        """
        encoded = text.replace("'", "'\\''").replace(" ", "%s")
        self.adb.run(["shell", "input", "text", f"'{encoded}'"])

    def _clear_and_type(self, x: int, y: int, text: str) -> None:
        """Tap a field, clear any existing content, then type text."""
        self.adb.run(["shell", "input", "tap", str(x), str(y)])
        time.sleep(0.4)
        # Move cursor to end, then delete backwards (handles any field length)
        self.adb.run(["shell", "input", "keyevent", "KEYCODE_MOVE_END"])
        time.sleep(0.1)
        self.adb.run(["shell", "input", "keyevent"] + ["KEYCODE_DEL"] * 200)
        time.sleep(0.2)
        self._type_text(text)
        time.sleep(0.3)

    # ── dialog / permission dismissal ────────────────────────────────────────

    # Buttons we want to tap to accept/dismiss dialogs (order matters: prefer fine-grained over broad)
    _DIALOG_ACCEPT = [
        "Nur diesmal",                          # location: only this time
        "Während der App-Nutzung zulassen",     # location: while using app
        "Beim Verwenden der App",               # location: variant
        "Zulassen",                             # generic allow
        "ZULASSEN",
        "Erlauben",
        "ERLAUBEN",
        "OK",
        "Ok",
    ]

    def _handle_dialogs(self, root) -> bool:
        """Detect and dismiss permission/info dialogs. Returns True if something was tapped."""
        for label in self._DIALOG_ACCEPT:
            nodes = root.xpath(f'//node[@text="{label}"]')
            if nodes:
                coords = self._parse_bounds(nodes[0].get("bounds", ""))
                if coords:
                    log_and_broadcast(f"Dialog erkannt — tippe '{label}' ...")
                    self.adb.run(["shell", "input", "tap", str(coords[0]), str(coords[1])])
                    time.sleep(1.5)
                    return True
        return False

    # ── login detection & automation ──────────────────────────────────────────

    _LOGIN_IDS = {
        "at.mobilkom.android.handyparken:id/etEmail",
        "at.mobilkom.android.handyparken:id/btnLogin",
    }

    def _is_login_screen(self, root) -> bool:
        for node in root.iter():
            if node.get("resource-id") in self._LOGIN_IDS:
                return True
            if node.get("text") == "HANDYPARKEN LOGIN":
                return True
        return False

    def _do_login(self, root) -> bool:
        """Fill credentials and tap Login. Returns True if login was attempted."""
        if not credentials.is_set:
            log_and_broadcast("Login-Bildschirm erkannt — keine Zugangsdaten gespeichert.")
            log_and_broadcast("Bitte Zugangsdaten im Dashboard eingeben.")
            return False

        log_and_broadcast(f"Auto-Login: {credentials.display} ...")

        # Locate fields by their stable resource-ids
        email_n = root.xpath('//node[@resource-id="at.mobilkom.android.handyparken:id/etEmail"]')
        pwd_n   = root.xpath('//node[@resource-id="at.mobilkom.android.handyparken:id/etPassword"]')
        btn_n   = root.xpath('//node[@resource-id="at.mobilkom.android.handyparken:id/btnLogin"]')

        if not email_n or not pwd_n or not btn_n:
            log_and_broadcast("Login-Felder konnten nicht lokalisiert werden.")
            return False

        email_xy = self._parse_bounds(email_n[0].get("bounds", ""))
        pwd_xy   = self._parse_bounds(pwd_n[0].get("bounds", ""))
        btn_xy   = self._parse_bounds(btn_n[0].get("bounds", ""))

        if not all([email_xy, pwd_xy, btn_xy]):
            log_and_broadcast("Login-Bounds ungültig.")
            return False

        log_and_broadcast("Gebe E-Mail ein ...")
        self._clear_and_type(*email_xy, credentials.login)

        log_and_broadcast("Gebe Passwort ein ...")
        self._clear_and_type(*pwd_xy, credentials.password)

        # Re-dump to check if Login button became enabled after filling fields
        time.sleep(0.5)
        xml2 = self._dump_ui()
        if xml2:
            from lxml import etree as _etree
            try:
                root2 = _etree.fromstring(xml2.encode())
                btn2 = root2.xpath('//node[@resource-id="at.mobilkom.android.handyparken:id/btnLogin"]')
                if btn2:
                    c = self._parse_bounds(btn2[0].get("bounds", ""))
                    if c:
                        btn_xy = c
                        if btn2[0].get("enabled") != "true":
                            log_and_broadcast("Login-Button noch deaktiviert — tippe nochmals Passwort-Feld ...")
                            self._clear_and_type(*pwd_xy, credentials.password)
                            time.sleep(0.5)
            except Exception:
                pass

        log_and_broadcast("Tippe Login ...")
        self.adb.run(["shell", "input", "tap", str(btn_xy[0]), str(btn_xy[1])])
        return True

    # ── main cycle ────────────────────────────────────────────────────────────

    def _run_cycle(self) -> None:
        """One cycle: handle login if needed, then search & tap license plate."""
        from lxml import etree
        deadline = time.time() + 90   # enough for login + navigation + search
        login_tried = False

        while time.time() < deadline and self.running:
            xml = self._dump_ui()
            if xml is None:
                log_and_broadcast("UI-Dump fehlgeschlagen.")
                return

            try:
                root = etree.fromstring(xml.encode())
            except etree.XMLSyntaxError as e:
                log_and_broadcast(f"XML Parsefehler: {e}")
                time.sleep(2)
                continue

            # ── Dialogs / permissions? ────────────────────────────────────────
            if self._handle_dialogs(root):
                continue  # re-dump after dismissing

            # ── Login screen? ─────────────────────────────────────────────────
            if self._is_login_screen(root):
                if login_tried:
                    log_and_broadcast("Login fehlgeschlagen (noch immer auf Login-Bildschirm). Zugangsdaten prüfen.")
                    return
                if self._do_login(root):
                    login_tried = True
                    log_and_broadcast("Warte auf Login-Erfolg ...")
                    time.sleep(6)
                    continue
                else:
                    # No credentials — wait and check again (user may set them via dashboard)
                    self._stop_event.wait(timeout=15)
                    continue

            login_tried = False  # Successfully past login screen

            # ── Search for license plate ──────────────────────────────────────
            log_and_broadcast(f"Suche Kennzeichen: {self.license_plate} ...")
            nodes = root.xpath(f'//*[@text="{self.license_plate}"]')
            if not nodes:
                log_and_broadcast(f"Kennzeichen {self.license_plate} nicht gefunden. Warte ...")
                time.sleep(3)
                continue

            node   = nodes[0]
            coords = self._parse_bounds(node.get("bounds", ""))
            if coords is None:
                log_and_broadcast(f"Ungültige Bounds: {node.get('bounds')}")
                return

            x, y = coords
            log_and_broadcast(f"Kennzeichen gefunden bei ({x},{y}). Klicke ...")
            r = self.adb.run(["shell", "input", "tap", str(x), str(y)])
            log_and_broadcast("Klick erfolgreich." if r and r.returncode == 0 else "Klick fehlgeschlagen.")
            return

        log_and_broadcast(f"Timeout: Kennzeichen {self.license_plate} nicht gefunden.")


# ── Globals & Startup ─────────────────────────────────────────────────────────

adb_manager   = ADBManager()
apk_installer = APKInstaller(adb_manager)
bot_controller = BotController(adb_manager)
credentials   = Credentials()


def _wait_for_emulator_and_install() -> None:
    log_and_broadcast("Warte auf Android-Emulator (kann 2–5 Minuten dauern) ...")
    for attempt in range(60):
        if adb_manager.connect():
            apk_installer.ensure_installed()
            return
        log_and_broadcast(f"Emulator noch nicht bereit, warte ... ({attempt + 1}/60)")
        time.sleep(10)
    log_and_broadcast("Emulator nicht erreichbar nach 10 Minuten.")
    apk_installer.status = "failed"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_event_loop()
    log_and_broadcast("ParkBot startet ...")
    threading.Thread(target=_wait_for_emulator_and_install, daemon=True).start()
    yield
    bot_controller.stop()


# ── API ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="ParkBot", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    try:
        return HTMLResponse(content=Path(FRONTEND_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Frontend nicht gefunden. Volume ./frontend mounten.</h1>",
            status_code=404
        )


@app.post("/start")
async def start_bot(body: dict):
    plate = body.get("license_plate", "").strip()
    if not plate:
        return {"error": "license_plate erforderlich"}
    bot_controller.start(plate)
    return {"status": "started", "license_plate": plate}


@app.post("/stop")
async def stop_bot():
    bot_controller.stop()
    return {"status": "stopped"}


@app.get("/status")
async def get_status():
    return {
        "running": bot_controller.running,
        "license_plate": bot_controller.license_plate,
        "apk_status": apk_installer.status,
    }


@app.post("/credentials")
async def set_credentials(body: dict):
    login    = body.get("login", "").strip()
    password = body.get("password", "").strip()
    if not login or not password:
        return {"error": "login und password erforderlich"}
    credentials.login    = login
    credentials.password = password
    log_and_broadcast(f"Zugangsdaten gespeichert: {credentials.display}")
    return {"ok": True, "login": credentials.display}


@app.get("/credentials")
async def get_credentials():
    return {"set": credentials.is_set, "login": credentials.display if credentials.is_set else None}


@app.get("/health")
async def health():
    return {
        "emulator": "connected" if adb_manager.is_connected() else "disconnected",
        "bot": "running" if bot_controller.running else "stopped",
        "apk": apk_installer.status,
    }


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
    await websocket.send_text(f"WebSocket verbunden — APK Status: {apk_installer.status}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(websocket)
