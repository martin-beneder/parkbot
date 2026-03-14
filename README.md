# ParkBot

Automatisierter Parkschein-Bot für die [HANDYPARKEN](https://www.handyparken.at/) App (Wien / Österreich).

ParkBot läuft als Docker-Stack: Ein Android-Emulator ([ReDroid](https://github.com/remote-android/redroid-doc)) führt die App aus, ein Python-Controller steuert sie per ADB-UI-Automation und stellt ein Web-Dashboard bereit.

---

## Features

- **Automatische Buchung** – Bucht zyklisch Kurzparkscheine ohne manuelle Interaktion
- **Kennzeichen-Verwaltung** – Legt neue Kennzeichen in der App an, falls noch nicht vorhanden
- **Konfigurierbare Dauer** – Parkdauer wählbar (15 – 120 Min.)
- **Konfigurierbare Pause** – Wartezeit zwischen Buchungen einstellbar
- **Web-Dashboard** – Live-Emulator-Screen, Logs, Start/Stop, Zugangsdaten
- **Automatischer APK-Download** – Lädt die HANDYPARKEN APK beim ersten Start automatisch herunter

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| Linux-Host | Windows/macOS nicht unterstützt (ReDroid benötigt Linux-Kernel) |
| Docker + Docker Compose | `docker compose version` ≥ 2.x |
| Kernel mit Android Binder | `CONFIG_ANDROID_BINDER_IPC=y` – auf CachyOS/Arch eingebaut |

**Kernel prüfen:**
```bash
ls /dev/binder || lsmod | grep binder_linux || grep CONFIG_ANDROID_BINDER_IPC /boot/config-$(uname -r)
```

---

## Schnellstart

```bash
git clone https://github.com/martin-beneder/parkbot.git
cd parkbot

# Einmalige Installation (Docker, Binder-Check, APK-Download, Image-Build)
bash scripts/install.sh

# Zugangsdaten setzen (optional, alternativ über Dashboard)
cp .env.example .env
nano .env   # HP_LOGIN und HP_PASSWORD eintragen

# Starten
bash scripts/start.sh
```

Dashboard öffnen: **http://localhost:8000**

Der Android-Emulator benötigt beim ersten Start 2–5 Minuten. Das Dashboard ist sofort erreichbar.

---

## Konfiguration

### Umgebungsvariablen (`.env` oder `docker compose`)

| Variable | Standard | Beschreibung |
|---|---|---|
| `HP_LOGIN` | – | E-Mail oder Telefonnummer für HANDYPARKEN |
| `HP_PASSWORD` | – | Passwort |
| `BOOKING_DURATION` | `15` | Standard-Parkdauer in Minuten |
| `BOOKING_PAUSE` | `14` | Standard-Pause zwischen Buchungen in Minuten |

### `.env.example`
```env
HP_LOGIN=your@email.com
HP_PASSWORD=yourpassword
```

Zugangsdaten können auch zur Laufzeit über das Dashboard gesetzt werden (werden nicht persistiert, bei Neustart neu eingeben oder `.env` verwenden).

---

## Dashboard

| Bereich | Funktion |
|---|---|
| **Steuerung** | Kennzeichen, Parkdauer, Pause, Start/Stop |
| **Zugangsdaten** | Login und Passwort zur Laufzeit setzen |
| **Status** | Bot- und APK-Status |
| **Emulator Screen** | Live-Vorschau des Android-Bildschirms |
| **Live Logs** | Echtzeit-Log aller Bot-Aktionen via WebSocket |

---

## API

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `GET /` | – | Dashboard |
| `POST /start` | JSON | Bot starten |
| `POST /stop` | JSON | Bot stoppen |
| `GET /status` | – | Aktueller Bot-Status |
| `GET /health` | – | Emulator- und APK-Status |
| `POST /credentials` | JSON | Zugangsdaten setzen |
| `GET /credentials` | – | Zugangsdaten-Status abfragen |
| `GET /video_feed` | – | MJPEG-Stream des Emulators |
| `WS /ws/logs` | WebSocket | Live-Log-Stream |

**POST /start Beispiel:**
```json
{
  "license_plate": "W123AB",
  "duration_min": 30,
  "pause_min": 17
}
```

---

## Projektstruktur

```
parkbot/
├── main.py              # FastAPI-App, Bot-Controller, ADB-Automation
├── Dockerfile           # Controller-Image
├── docker-compose.yml   # Emulator + Controller Stack
├── requirements.txt
├── frontend/
│   └── index.html       # Dashboard (Single-Page, Bootstrap)
├── scripts/
│   ├── install.sh       # Einmalige Installation
│   ├── start.sh         # Stack starten
│   ├── cleanup.sh       # Stack und Volumes löschen
│   └── download_apk.py  # APK-Downloader (APKPure / Uptodown / APKCombo)
├── apk/                 # APK-Ablage (gitignored)
└── tests/
    └── test_parkbot.py  # Unit-Tests
```

---

## Manuelle APK-Installation

Falls der automatische Download fehlschlägt:

1. HANDYPARKEN APK manuell von [APKPure](https://apkpure.com/handyparken/at.mobilkom.android.handyparken) herunterladen
2. Als `apk/handyparken.apk` ablegen
3. Container neu starten: `docker compose restart controller`

---

## Tests ausführen

```bash
python3 -m pytest tests/ -v
```

---

## Troubleshooting

**Emulator startet nicht / bleibt bei "Verbindung wird geprüft"**
→ Kernel-Binder-Support fehlt. Siehe [Voraussetzungen](#voraussetzungen).

**APK-Download schlägt fehl**
→ APK manuell ablegen (siehe [Manuelle APK-Installation](#manuelle-apk-installation)).

**Bot findet Kennzeichen nicht**
→ Kennzeichen exakt so eingeben wie in der HANDYPARKEN App (z.B. `W123AB`).

**Dashboard nicht erreichbar**
→ `docker compose logs controller` prüfen; Port 8000 muss frei sein.

---

## Hinweis

Dieses Projekt ist ein privates Automatisierungsprojekt und steht in keiner Verbindung zu A1 Telekom Austria / mobilkom austria. Die Nutzung erfolgt auf eigene Verantwortung.
