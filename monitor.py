#!/usr/bin/env python3
"""
Monitor delle sessioni d'esame per agenti d'affari in mediazione
(Camera di Commercio Milano Monza Brianza Lodi).

Legge la tabella delle sessioni con un browser vero (Playwright/Chromium) e
confronta le righe con lo snapshot precedente. Notifica su Telegram quando:
  - compare una sessione nuova (caso principale)
  - si liberano posti su una sessione gia' presente (0/10 -> 1/10)
  - il portale smette di rispondere o cambia struttura
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

POSTI_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")          # "0/10"
DATA_RE = re.compile(r"^\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*$")  # "30/09/2026"
ORA_RE = re.compile(r"^\s*(\d{1,2}[:.]\d{2})\s*$")            # "09:15"


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def tg_send(text: str, photo: Path | None = None) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("[warn] Credenziali Telegram assenti, stampo soltanto:\n" + text)
        return

    data = urllib.parse.urlencode(
        {
            "chat_id": TG_CHAT,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=30
        ) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print(f"[error] Invio Telegram fallito: {e}", file=sys.stderr)

    if photo and photo.exists():
        _tg_send_photo(photo)


def _tg_send_photo(photo: Path) -> None:
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


# --------------------------------------------------------------------------- #
# Stato
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"rows": {}, "failures": 0, "last_ok": None, "first_run": True}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #
def scrape() -> dict[str, dict]:
    """Restituisce {chiave_sessione: {label, data, ora, luogo, liberi, totali}}."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1400, "height": 1800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(4000)  # margine per tabelle popolate dopo il networkidle

        rows_raw = []
        for tr in page.query_selector_all("tr"):
            cells = [
                " ".join((td.inner_text() or "").split())
                for td in tr.query_selector_all("td")
            ]
            if len(cells) >= 3 and any(c for c in cells):
                rows_raw.append(cells)

        page.screenshot(path=str(SCREENSHOT), full_page=True)
        browser.close()

    sessioni: dict[str, dict] = {}
    for cells in rows_raw:
        liberi = totali = None
        data = ora = None
        for c in cells:
            if liberi is None and (m := POSTI_RE.match(c)):
                liberi, totali = int(m.group(1)), int(m.group(2))
            elif data is None and (m := DATA_RE.match(c)):
                data = m.group(1)
            elif ora is None and (m := ORA_RE.match(c)):
                ora = m.group(1)

        # una riga valida ha almeno la data; senza posti non e' la tabella giusta
        if data is None or liberi is None:
            continue

        label = cells[0] if cells[0] else f"{data} {ora or ''}".strip()
        luogo = next(
            (c for c in cells if "," in c and not DATA_RE.match(c) and len(c) > 5), ""
        )
        key = f"{data}|{ora or ''}|{label}"
        sessioni[key] = {
            "label": label,
            "data": data,
            "ora": ora or "",
            "luogo": luogo,
            "liberi": liberi,
            "totali": totali,
        }
    return sessioni


def fmt(row: dict) -> str:
    base = f"{row['data']}"
    if row["ora"]:
        base += f" ore {row['ora']}"
    if row["label"] and row["label"] not in base:
        base += f" — {row['label']}"
    return f"{base} <b>[{row['liberi']}/{row['totali']} liberi]</b>"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    state = load_state()
    now = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")

    try:
        rows = scrape()
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

    if not rows:
        state["failures"] = state.get("failures", 0) + 1
        save_state(state)
        print("[warn] Nessuna riga trovata nella tabella", file=sys.stderr)
        if state["failures"] == 3:
            tg_send(
                "⚠️ <b>Monitor esame: la pagina si carica ma la tabella è vuota.</b>\n"
                "Probabile cambio di struttura del portale: controlla a mano.\n"
                f"{URL}"
            )
        return 0

    old = state.get("rows", {})
    state.update({"rows": rows, "failures": 0, "last_ok": now})

    # --- primo avvio: fotografia iniziale, nessuna allerta ------------------ #
    if state.get("first_run") or not old:
        state["first_run"] = False
        save_state(state)
        elenco = "\n".join(f"• {fmt(r)}" for r in sorted(rows.values(), key=lambda r: r["data"]))
        tg_send(
            f"✅ <b>Monitor attivo</b> — {len(rows)} sessioni in pagina.\n"
            f"Controlli alle 9, 11, 14, 16, 18 e 20.\n\n{elenco}\n\n{URL}",
            SCREENSHOT,
        )
        return 0

    save_state(state)

    nuove = [r for k, r in rows.items() if k not in old]
    sparite = [r for k, r in old.items() if k not in rows]
    liberati = [
        r
        for k, r in rows.items()
        if k in old and r["liberi"] > old[k].get("liberi", 0)
    ]

    if not (nuove or sparite or liberati):
        print(f"[ok] Nessuna variazione ({now}) — {len(rows)} sessioni")
        return 0

    if nuove:
        titolo = "🆕 <b>NUOVA DATA D'ESAME PUBBLICATA</b>"
    elif liberati:
        titolo = "🟢 <b>SI SONO LIBERATI DEI POSTI</b>"
    else:
        titolo = "🔔 <b>La tabella esami è cambiata</b>"

    msg = [titolo, ""]
    if nuove:
        msg.append("<b>Sessioni nuove:</b>")
        msg += [f"➕ {fmt(r)}" for r in sorted(nuove, key=lambda r: r["data"])]
        msg.append("")
    if liberati:
        msg.append("<b>Posti liberati:</b>")
        msg += [f"🟢 {fmt(r)}" for r in sorted(liberati, key=lambda r: r["data"])]
        msg.append("")
    if sparite:
        msg.append("<b>Non più in elenco:</b>")
        msg += [f"➖ {fmt(r)}" for r in sorted(sparite, key=lambda r: r["data"])]
        msg.append("")

    disponibili = [r for r in rows.values() if r["liberi"] > 0]
    if disponibili:
        msg.append("<b>Prenotabili ora:</b>")
        msg += [f"👉 {fmt(r)}" for r in sorted(disponibili, key=lambda r: r["data"])]
    else:
        msg.append("<i>Al momento nessuna sessione ha posti liberi.</i>")

    msg += ["", URL, f"<i>Controllo delle {now}</i>"]
    tg_send("\n".join(msg), SCREENSHOT)
    print(f"[alert] nuove={len(nuove)} liberati={len(liberati)} sparite={len(sparite)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
