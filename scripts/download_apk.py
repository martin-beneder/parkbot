#!/usr/bin/env python3
"""
Standalone APK downloader for HANDYPARKEN.
Tries APKPure (page scrape), Uptodown, and APKCombo in order.
Run directly:  python3 scripts/download_apk.py
"""
import os
import sys
from pathlib import Path

try:
    import requests
    from lxml import html as lxhtml
except ImportError:
    print("Installing required packages ...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "requests", "lxml"])
    import requests
    from lxml import html as lxhtml

PACKAGE = "at.mobilkom.android.handyparken"
OUTPUT = Path(os.environ.get("APK_PATH", str(Path(__file__).parent.parent / "apk" / "handyparken.apk")))

UA_DESKTOP = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36"
)


def _save_stream(resp: requests.Response) -> bool:
    ct = resp.headers.get("Content-Type", "")
    if "html" in ct.lower():
        print(f"  Warnung: Antwort ist HTML ({len(resp.content)} B), kein APK.")
        return False
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with OUTPUT.open("wb") as fh:
        for chunk in resp.iter_content(1 << 16):
            fh.write(chunk)
            total += len(chunk)
            print(f"\r  {total // 1024:,} KB heruntergeladen ...", end="", flush=True)
    print()
    if total < 1_000_000:
        print(f"  Datei zu klein ({total} B) – kein gültiges APK.")
        OUTPUT.unlink(missing_ok=True)
        return False
    return True


def _fetch_apk(url: str, session: requests.Session, referer: str = "", ua: str = UA_DESKTOP) -> bool:
    headers = {"User-Agent": ua}
    if referer:
        headers["Referer"] = referer
    try:
        r = session.get(url, headers=headers, timeout=120, stream=True, allow_redirects=True)
        r.raise_for_status()
        return _save_stream(r)
    except Exception as exc:
        print(f"  Fehler beim Download: {exc}")
        return False


# ── Source 1: APKPure ───────────────────────────────────────────────────────

def try_apkpure() -> bool:
    s = requests.Session()
    base = "https://apkpure.com"
    app_url = f"{base}/handyparken/{PACKAGE}"
    dl_url = f"{app_url}/download"

    try:
        # Seed cookies
        s.get(app_url, headers={"User-Agent": UA_DESKTOP}, timeout=15)

        r = s.get(dl_url, headers={"User-Agent": UA_DESKTOP, "Referer": app_url}, timeout=15)
        r.raise_for_status()
        tree = lxhtml.fromstring(r.content)

        candidates = (
            tree.xpath('//*[@id="download_link"]/@href') +
            tree.xpath('//a[contains(@class,"download-start")]/@href') +
            tree.xpath('//a[contains(@href,"d.apkpure.com")]/@href') +
            tree.xpath('//a[contains(@href,".apk")]/@href')
        )

        for href in candidates:
            if href.startswith("/"):
                href = base + href
            if not href.startswith("http"):
                continue
            print(f"  Link: {href[:90]}")
            if _fetch_apk(href, s, referer=dl_url):
                return True

        # Last resort: direct URL with proper Referer
        direct = f"https://d.apkpure.com/b/APK/{PACKAGE}?version=latest"
        print(f"  Direktversuch: {direct}")
        return _fetch_apk(direct, s, referer=dl_url)

    except Exception as exc:
        print(f"  Fehler: {exc}")
        return False


# ── Source 2: Uptodown ──────────────────────────────────────────────────────

def try_uptodown() -> bool:
    s = requests.Session()
    base = "https://handyparken.at.uptodown.com"

    try:
        r = s.get(f"{base}/android", headers={"User-Agent": UA_DESKTOP}, timeout=15)
        r.raise_for_status()
        tree = lxhtml.fromstring(r.content)

        # Get latest version download page URL
        version_links = tree.xpath('//a[@data-url]/@data-url')
        dl_buttons = tree.xpath('//a[@id="detail-download-button"]/@href')
        candidates = version_links + dl_buttons

        for href in candidates:
            if href.startswith("/"):
                href = base + href
            print(f"  Link: {href[:90]}")
            r2 = s.get(href, headers={"User-Agent": UA_DESKTOP, "Referer": base}, timeout=15)
            r2.raise_for_status()
            tree2 = lxhtml.fromstring(r2.content)
            apk_links = tree2.xpath('//a[@id="detail-download-button"]/@href')
            for apk_href in apk_links:
                if apk_href.startswith("/"):
                    apk_href = base + apk_href
                if _fetch_apk(apk_href, s, referer=href):
                    return True

        # Try direct download page
        direct = f"{base}/android/download"
        r = s.get(direct, headers={"User-Agent": UA_DESKTOP}, timeout=15)
        tree = lxhtml.fromstring(r.content)
        for href in tree.xpath('//a[contains(@href,".apk")]/@href'):
            if href.startswith("/"):
                href = base + href
            if _fetch_apk(href, s, referer=direct):
                return True

        return False

    except Exception as exc:
        print(f"  Fehler: {exc}")
        return False


# ── Source 3: APKCombo ──────────────────────────────────────────────────────

def try_apkcombo() -> bool:
    s = requests.Session()
    base = "https://apkcombo.com"
    page = f"{base}/handyparken/{PACKAGE}/download/apk"

    try:
        r = s.get(page, headers={"User-Agent": UA_DESKTOP}, timeout=15)
        r.raise_for_status()
        tree = lxhtml.fromstring(r.content)

        candidates = (
            tree.xpath('//a[contains(@class,"variant")]/@href') +
            tree.xpath('//a[contains(@href,".apk")]/@href') +
            tree.xpath('//a[contains(@class,"download")]/@href')
        )

        for href in candidates:
            if href.startswith("/"):
                href = base + href
            if not href.startswith("http"):
                continue
            print(f"  Link: {href[:90]}")
            if _fetch_apk(href, s, referer=page):
                return True

        return False

    except Exception as exc:
        print(f"  Fehler: {exc}")
        return False


# ── Main ────────────────────────────────────────────────────────────────────

SOURCES = [
    ("APKPure",  try_apkpure),
    ("Uptodown", try_uptodown),
    ("APKCombo", try_apkcombo),
]

if __name__ == "__main__":
    if OUTPUT.exists() and OUTPUT.stat().st_size > 1_000_000:
        print(f"APK bereits vorhanden: {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)")
        sys.exit(0)

    print(f"==> HANDYPARKEN APK Download")
    print(f"==> Ziel: {OUTPUT}")
    print()

    for name, fn in SOURCES:
        print(f"[{name}]")
        if fn():
            print(f"\n==> Erfolgreich heruntergeladen von {name}!")
            sys.exit(0)
        print()

    print("==> Alle Quellen fehlgeschlagen.")
    print("    Manuell herunterladen und ablegen unter:")
    print(f"    {OUTPUT}")
    print("    https://apkpure.com/handyparken/at.mobilkom.android.handyparken")
    sys.exit(1)
