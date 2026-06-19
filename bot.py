#!/usr/bin/env python3
"""
Crazy Time Telegram Bot — Render-compatible
Runs a tiny HTTP health-check server on PORT so Render keeps the
service alive, while the polling loop runs in a background thread.
"""

import os
import time
import json
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID    = os.environ.get("TELEGRAM_CHANNEL_ID", "")
PORT          = int(os.environ.get("PORT", 8080))   # Render injects PORT
API_URL       = "https://api.casinoscores.com/svc-evolution-game-events/api/crazytime/latest"
POLL_INTERVAL = 30   # seconds between polls

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Sector mappings ────────────────────────────────────────────────────────────
SECTOR_LABEL = {
    "1": "1", "one": "1",
    "2": "2", "two": "2",
    "5": "5", "five": "5",
    "10": "10", "ten": "10",
    "cashhunt":  "Cash Hunt",
    "coinflip":  "Coin Flip",
    "crazytime": "Crazy Time",
    "pachinko":  "Pachinko",
}
BONUS_SECTORS = {"cashhunt", "coinflip", "crazytime", "pachinko"}

def label(raw: str) -> str:
    return SECTOR_LABEL.get(raw.lower().strip(), raw)

def is_bonus(raw: str) -> bool:
    return raw.lower().strip() in BONUS_SECTORS

# ── Number formatting (EU style) ───────────────────────────────────────────────
def fmt(value, decimals=3) -> str:
    try:
        f = float(value)
        s = f"{f:,.{decimals}f}".rstrip("0").rstrip(".")
        s = s.replace(",", "TSEP").replace(".", ",").replace("TSEP", ".")
        return s
    except (TypeError, ValueError):
        return str(value)

# ── MarkdownV2 escaping ────────────────────────────────────────────────────────
_MD_SPECIAL = set(r"\_[]()~`>#+=|{}.!")

def esc(text: str) -> str:
    out, i, s = [], 0, str(text)
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(ch); out.append(s[i + 1]); i += 2
        elif ch == "*":
            out.append(ch); i += 1
        elif ch in _MD_SPECIAL:
            out.append("\\"); out.append(ch); i += 1
        else:
            out.append(ch); i += 1
    return "".join(out)

# ── Message builder ────────────────────────────────────────────────────────────
def build_message(payload: dict) -> str:
    data      = payload.get("data", payload)
    result    = data.get("result", {}).get("outcome", {})
    top_slot  = result.get("topSlot", {})
    wheel_res = result.get("wheelResult", {})

    ts_sector  = top_slot.get("wheelSector", "?")
    ts_mult    = top_slot.get("multiplier")
    wh_sector  = wheel_res.get("wheelSector", "?")
    matched    = result.get("isTopSlotMatchedToWheelResult", False)
    max_mult   = result.get("maxMultiplier")

    participants = data.get("numOfParticipants")
    payout       = data.get("payout")
    wager        = data.get("wager")
    amount       = payout if payout else wager

    wh_label = label(wh_sector)
    ts_label = label(ts_sector)
    bonus    = is_bonus(wh_sector)

    ts_str         = f"{esc(ts_label)} ×{ts_mult}" if ts_mult else esc(ts_label)
    matched_suffix = "" if matched else " \\(missed\\)"

    header = (
        f"🎯 *{esc(wh_label)}* – BONUS hit\\!"
        if bonus else
        f"🎯 *{esc(wh_label)}* – Number hit\\!"
    )

    lines = [
        header,
        f"• *Segment:* {esc(wh_label)}",
        f"• *Top Slot:* {ts_str}{matched_suffix}",
    ]
    if participants is not None:
        lines.append(f"• *Total winners:* {esc(fmt(participants, 0))}")
    if amount is not None:
        lines.append(f"• *Total amount:* € {esc(fmt(amount))}")
    if bonus and max_mult and max_mult > 1:
        lines.append(f"• *Multiplier:* {esc(str(max_mult))}x")

    return "\n".join(lines)

# ── Telegram sender ────────────────────────────────────────────────────────────
def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": CHANNEL_ID, "text": text, "parse_mode": "MarkdownV2"},
            timeout=10,
        )
        if r.status_code == 200:
            log.info("✅ Message posted.")
            return True
        log.error("Telegram error %s: %s", r.status_code, r.text)
        return False
    except requests.RequestException as e:
        log.error("Network error sending: %s", e)
        return False

# ── API fetcher ────────────────────────────────────────────────────────────────
def fetch_latest() -> dict | None:
    try:
        r = requests.get(API_URL, timeout=10)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Fetch error: %s", e)
        return None

# ── Polling loop (runs in background thread) ───────────────────────────────────
def polling_loop():
    log.info("🎰 Polling loop started  |  channel=%s  |  interval=%ss", CHANNEL_ID, POLL_INTERVAL)
    last_id = None
    while True:
        payload = fetch_latest()
        if payload:
            game_id = payload.get("id") or payload.get("transmissionId")
            if game_id and game_id != last_id:
                log.info("🆕 New round: %s", game_id)
                try:
                    msg = build_message(payload)
                    if send_message(msg):
                        last_id = game_id
                except Exception:
                    log.exception("Error processing payload")
            else:
                log.debug("No new round (id=%s)", game_id)
        time.sleep(POLL_INTERVAL)

# ── Health-check HTTP server (keeps Render Web Service alive) ──────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Crazy Time bot is running")

    def log_message(self, format, *args):
        pass   # silence HTTP access logs

# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌  Set TELEGRAM_BOT_TOKEN environment variable.")
    if not CHANNEL_ID:
        raise SystemExit("❌  Set TELEGRAM_CHANNEL_ID environment variable.")

    # Start polling in a daemon thread
    t = threading.Thread(target=polling_loop, daemon=True)
    t.start()

    # Start HTTP server on main thread (Render requires a bound port)
    log.info("🌐 Health server listening on port %s", PORT)
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    main()
