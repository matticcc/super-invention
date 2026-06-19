#!/usr/bin/env python3
"""
Crazy Time Telegram Bot — Render-compatible
- Polls Crazy Time API every 10 seconds, posts new results to channel
- Every 30 minutes sends a promo message, pins it, deletes the previous one
- Runs a tiny HTTP health-check server so Render free plan stays alive
"""

import os
import time
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID     = os.environ.get("TELEGRAM_CHANNEL_ID", "")
PORT           = int(os.environ.get("PORT", 8080))
API_URL        = "https://api.casinoscores.com/svc-evolution-game-events/api/crazytime/latest"
POLL_INTERVAL  = 10          # seconds between API polls
PROMO_INTERVAL = 30 * 60     # 30 minutes in seconds

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
    "cashhunt":   "Cash Hunt",
    "coinflip":   "Coin Flip",
    "crazytime":  "Crazy Time",
    "crazybonus": "Crazy Time",   # API alias
    "pachinko":   "Pachinko",
}
BONUS_SECTORS = {"cashhunt", "coinflip", "crazytime", "crazybonus", "pachinko"}

def label(raw: str) -> str:
    return SECTOR_LABEL.get(raw.lower().strip(), raw)

def is_bonus(raw: str) -> bool:
    return raw.lower().strip() in BONUS_SECTORS

# ── Number formatting (EU style: 1.234,56) ────────────────────────────────────
def fmt(value, decimals=3) -> str:
    try:
        f = float(value)
        s = f"{f:,.{decimals}f}".rstrip("0").rstrip(".")
        s = s.replace(",", "TSEP").replace(".", ",").replace("TSEP", ".")
        return s
    except (TypeError, ValueError):
        return str(value)

# ── HTML escaping (safe for Telegram HTML parse mode) ─────────────────────────
def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def b(text: str) -> str:
    """Wrap text in HTML bold tags."""
    return f"<b>{esc(text)}</b>"

# ── Message builder (HTML) ────────────────────────────────────────────────────
def build_message(payload: dict) -> str:
    # Top-level fields
    total_winners = payload.get("totalWinners")
    total_amount  = payload.get("totalAmount")

    data      = payload.get("data", payload)
    result    = data.get("result", {}).get("outcome", {})
    top_slot  = result.get("topSlot", {})
    wheel_res = result.get("wheelResult", {})

    ts_sector = top_slot.get("wheelSector", "?")
    ts_mult   = top_slot.get("multiplier")
    wh_sector = wheel_res.get("wheelSector", "?")
    matched   = result.get("isTopSlotMatchedToWheelResult", False)
    max_mult  = result.get("maxMultiplier")

    wh_label = label(wh_sector)
    ts_label = label(ts_sector)
    bonus    = is_bonus(wh_sector)

    ts_str         = f"{esc(ts_label)} ×{ts_mult}" if ts_mult else esc(ts_label)
    matched_suffix = "" if matched else " (missed)"

    header = (
        f"🎯 {b(wh_label)} – BONUS hit!"
        if bonus else
        f"🎯 {b(wh_label)} – Number hit!"
    )

    lines = [
        header,
        f"• {b('Segment:')} {esc(wh_label)}",
        f"• {b('Top Slot:')} {ts_str}{matched_suffix}",
    ]
    if total_winners is not None:
        lines.append(f"• {b('Total winners:')} {esc(fmt(total_winners, 0))}")
    if total_amount is not None:
        lines.append(f"• {b('Total amount:')} € {esc(fmt(total_amount))}")
    if bonus and max_mult and max_mult > 1:
        lines.append(f"• {b('Multiplier:')} {esc(str(max_mult))}x")

    return "\n".join(lines)

# ── Telegram API helpers ───────────────────────────────────────────────────────
def tg(method: str, **kwargs) -> dict | None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=kwargs, timeout=10)
        data = r.json()
        if not data.get("ok"):
            log.error("Telegram %s error: %s", method, data)
            return None
        return data
    except requests.RequestException as e:
        log.error("Network error on %s: %s", method, e)
        return None

def send_message(text: str, parse_mode: str = "HTML") -> int | None:
    """Send a message, return message_id or None."""
    resp = tg("sendMessage", chat_id=CHANNEL_ID, text=text, parse_mode=parse_mode,
              disable_web_page_preview=True)
    if resp:
        msg_id = resp["result"]["message_id"]
        log.info("✅ Message sent (id=%s)", msg_id)
        return msg_id
    return None

def pin_message(message_id: int) -> bool:
    resp = tg("pinChatMessage", chat_id=CHANNEL_ID, message_id=message_id,
              disable_notification=True)
    if resp:
        log.info("📌 Pinned message %s", message_id)
        return True
    return False

def delete_message(message_id: int) -> bool:
    resp = tg("deleteMessage", chat_id=CHANNEL_ID, message_id=message_id)
    if resp:
        log.info("🗑️  Deleted message %s", message_id)
        return True
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

# ── Promo loop ─────────────────────────────────────────────────────────────────
def promo_loop():
    """Every 30 minutes: send promo, pin it, delete the previous promo."""
    log.info("📢 Promo loop started  |  interval=%sm", PROMO_INTERVAL // 60)
    last_promo_id = None

    while True:
        time.sleep(PROMO_INTERVAL)

        # Send new promo using HTML so the hyperlink works cleanly
        text = '🎰 Play Crazy Time on <a href="https://roobet.com/?ref=aittam">Roobet</a>'
        msg_id = send_message(text, parse_mode="HTML")

        if msg_id:
            pin_message(msg_id)
            if last_promo_id:
                delete_message(last_promo_id)
            last_promo_id = msg_id

# ── Polling loop ───────────────────────────────────────────────────────────────
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
                    if send_message(msg) is not None:
                        last_id = game_id
                except Exception:
                    log.exception("Error processing payload")
            else:
                log.debug("No new round (id=%s)", game_id)
        time.sleep(POLL_INTERVAL)

# ── Health-check HTTP server ───────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Crazy Time bot is running")

    def log_message(self, format, *args):
        pass  # silence access logs

# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌  Set TELEGRAM_BOT_TOKEN environment variable.")
    if not CHANNEL_ID:
        raise SystemExit("❌  Set TELEGRAM_CHANNEL_ID environment variable.")

    threading.Thread(target=polling_loop, daemon=True).start()
    threading.Thread(target=promo_loop,   daemon=True).start()

    log.info("🌐 Health server listening on port %s", PORT)
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()

if __name__ == "__main__":
    main()
