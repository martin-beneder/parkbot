# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run locally (Docker)
```bash
bash scripts/install.sh   # first-time setup: installs Docker, loads kernel modules, builds containers
bash scripts/start.sh     # start all services (emulator + controller)
docker-compose logs -f controller  # stream logs
bash scripts/cleanup.sh   # stop and remove containers
```

### Run tests
```bash
pip install -r requirements.txt pytest
pytest tests/test_parkbot.py          # all tests
pytest tests/test_parkbot.py::test_xml_find_plate  # single test
```

### Run the FastAPI app directly (no emulator)
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Architecture

The project automates parking ticket renewal via the Austrian **HANDYPARKEN** Android app (`at.mobilkom.android.handyparken`), running inside a [ReDroid](https://github.com/remote-android/redroid-doc) Android-in-Docker emulator.

**Two Docker services** (defined in `docker-compose.yml`):
- `emulator` — ReDroid Android 11 container, exposes ADB on port 5555
- `controller` — Python/FastAPI app that connects to the emulator over ADB

**`main.py` — single-file application with four classes:**
- `ADBManager` — thread-safe wrapper around `adb` subprocess calls. Uses a lock to serialize ADB commands, handles reconnect on failure. `screencap()` uses `exec-out` for binary output.
- `APKInstaller` — checks if HANDYPARKEN is installed, downloads the APK from APKPure if needed, installs via `adb install -r`.
- `BotController` — runs a background thread that repeatedly: launches the app via `adb shell monkey`, dumps the UI hierarchy via `uiautomator dump`, parses XML with lxml/XPath to find the license plate node by `@text`, then taps its center coordinates.
- FastAPI app — serves the dashboard (`GET /`), REST endpoints (`POST /start`, `POST /stop`, `GET /status`, `GET /health`), MJPEG video stream (`GET /video_feed` — screencap → OpenCV PNG→JPEG), and WebSocket log streaming (`WS /ws/logs`).

**Log broadcasting:** `log_and_broadcast()` logs via Python logging and pushes messages to all connected WebSocket clients using `asyncio.run_coroutine_threadsafe` (since BotController runs in a thread, not the async event loop).

**Frontend** (`frontend/index.html`) — single-page Bootstrap 5 dashboard with live MJPEG screen, start/stop controls, live log panel (WebSocket), and health polling every 5 seconds.

**APK placement:** Place a pre-downloaded APK at `apk/handyparken.apk` (>1 MB) to skip the runtime download.

**Kernel requirements:** ReDroid requires `binder_linux` and `ashmem_linux` kernel modules. The install script loads them with `modprobe`.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ADB_HOST` | `emulator` | Hostname of the Android emulator |
| `ADB_PORT` | `5555` | ADB port |
