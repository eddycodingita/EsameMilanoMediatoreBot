#!/usr/bin/env python3
"""
Monitor delle sessioni d'esame per agenti d'affari in mediazione
(Camera di Commercio Milano Monza Brianza Lodi).

Carica la pagina con un browser vero (Playwright/Chromium), estrae le righe
che contengono date o riferimenti ai posti, e confronta con lo snapshot
precedente. Se qualcosa cambia manda un messaggio Telegram.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://eolmi.infocamere.it/fnmiWeb/immobiliari?client=milano"
STATE_FILE = Path("state/snapshot.json")
SCREENSHOT = Path("screenshot.png")

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

# Righe interessanti: contengono una data, oppure parole chiave del portale.
DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
KEYWORD_RE = re.compile(
    r"(posti|disponibil|sessione|sessioni|esaurit|iscriv|prenota|completo)",
    re.IGNORECASE,
)
# Segnale di disponibilità: "3 posti disponibili", "posti disponibili: 5"
AVAILABLE_RE = re.compile(
    r"(?:(?<!0\s)\b([1-9]\d*)\s*posti?\s*disponibil|posti?\s*disponibil\w*\s*:?\s*([1-9]\d*))",
    re.IGNORECASE,
)


def tg_send(text: str, photo: Path | None = None) -> None:
    """Invia un messaggio (e volendo uno screenshot) su Telegram."""
    if not TG_TOKEN or not TG_CHAT:
        print("[warn] Credenziali Telegram assenti, stampo soltanto:\n" + text)
        return

    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": TG_CHAT,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    try:
        with urllib.request.urlopen(api, data=data, timeout=30) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print(f"[error] Invio Telegram fallito: {e}", file=sys.stderr)

    if photo and photo.exists():
        _tg_send_photo(photo)


def _tg_send_photo(photo: Path) -> None:
    """Upload multipart dello screenshot, senza dipendenze esterne."""
    boundary = "----monitorboundary7d91"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
    body.extend(f"{TG_CHAT}\r\n".encode())
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        b'Content-Disposition: form-data; name="photo"; filename="pagina.png"\r\n'
        b"Content-Type: image/png\r\n\r\n"
    )
    body.extend(photo.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print(f"[error] Invio screenshot fallito: {e}", file=sys.stderr)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"lines": [], "failures": 0, "last_ok": None, "first_run": True}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def scrape() -> list[str]:
    """Restituisce le righe significative della pagina renderizzata."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1280, "height": 1600},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle", timeout=90_000)
        # margine extra: alcune tabelle si popolano dopo il networkidle
        page.wait_for_timeout(4000)
        text = page.inner_text("body")
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        browser.close()

    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or len(line) > 300:
            continue
        if DATE_RE.search(line) or KEYWORD_RE.search(line):
            lines.append(line)

    # dedup mantenendo l'ordine
    seen, out = set(), []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def has_availability(lines: list[str]) -> bool:
    return any(AVAILABLE_RE.search(line) for line in lines)


def main() -> int:
    state = load_state()
    now = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")

    try:
        lines = scrape()
    except Exception as e:  # noqa: BLE001
        state["failures"] = state.get("failures", 0) + 1
        save_state(state)
        print(f"[error] Scraping fallito ({state['failures']}): {e}", file=sys.stderr)
        if state["failures"] in (3, 12, 48):
            tg_send(
                f"⚠️ <b>Monitor esame: {state['failures']} controlli falliti di fila.</b>\n"
                f"Il silenzio potrebbe non significare 'nessuna data nuova'.\n"
                f"Ultimo errore: {type(e).__name__}\n{URL}"
            )
        return 0

    if not lines:
        state["failures"] = state.get("failures", 0) + 1
        save_state(state)
        print("[warn] Nessuna riga utile estratta", file=sys.stderr)
        if state["failures"] == 3:
            tg_send(
                "⚠️ <b>Monitor esame: la pagina si carica ma non trovo date o posti.</b>\n"
                "Probabile cambio di struttura del portale: da controllare a mano.\n"
                f"{URL}"
            )
        return 0

    old = state.get("lines", [])
    added = [l for l in lines if l not in old]
    removed = [l for l in old if l not in lines]

    state.update({"lines": lines, "failures": 0, "last_ok": now})

    if state.get("first_run"):
        state["first_run"] = False
        save_state(state)
        tg_send(
            "✅ <b>Monitor attivo.</b>\nStato di partenza della pagina:\n\n"
            + "\n".join(f"• {l}" for l in lines[:25])
            + f"\n\n{URL}",
            SCREENSHOT,
        )
        return 0

    save_state(state)

    if not added and not removed:
        print(f"[ok] Nessuna variazione ({now})")
        return 0

    header = (
        "🟢 <b>POSTI DISPONIBILI — iscriviti subito</b>"
        if has_availability(added)
        else "🔔 <b>La pagina esami è cambiata</b>"
    )
    msg = [header, ""]
    if added:
        msg.append("<b>Nuovo:</b>")
        msg += [f"➕ {l}" for l in added[:20]]
    if removed:
        msg.append("")
        msg.append("<b>Sparito:</b>")
        msg += [f"➖ {l}" for l in removed[:20]]
    msg += ["", f"{URL}", f"<i>Controllo delle {now}</i>"]

    tg_send("\n".join(msg), SCREENSHOT)
    print(f"[alert] {len(added)} aggiunte, {len(removed)} rimozioni")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
