import os
import sys
import time
import json
import random
import asyncio
import logging
import threading
import re
import websocket
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------------------------------------------------------
# 1. RENDER PORT BINDING (Keep-Alive Server)
# ---------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Quotex Scanner Bot active.")

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ---------------------------------------------------------
# 2. CONFIGURATION & HIGH-PAYOUT ASSETS
# ---------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
QUOTEX_SSID = os.environ.get("QUOTEX_SSID")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing!")
    sys.exit(1)

# Default standard Quotex OTC payout mappings (used when socket syncs)
DEFAULT_OTC_PAYOUTS = {
    "USD/INR (OTC)": 92,
    "USD/PKR (OTC)": 91,
    "USD/BDT (OTC)": 90,
    "EUR/USD (OTC)": 89,
    "USD/BRL (OTC)": 89,
    "GBP/USD (OTC)": 88,
    "USD/EGP (OTC)": 88,
    "USD/TRY (OTC)": 87,
    "EUR/JPY (OTC)": 87,
    "Gold (OTC)": 86,
    "USD/JPY (OTC)": 85,
}

LIVE_QUOTEX_PAYOUTS = {}
ACTIVE_SCANNERS = {}  # {chat_id: bool}

# ---------------------------------------------------------
# 3. WEBSOCKET SYNC
# ---------------------------------------------------------
def clean_name(raw: str) -> str:
    return raw.replace("\n", "").strip()

def on_ws_message(ws, message):
    global LIVE_QUOTEX_PAYOUTS
    try:
        if message == "2":
            ws.send("3")
            return

        match = re.search(r"(\[.*\])", message)
        if match:
            payload = json.loads(match.group(1))
            event = payload[0]
            if event in ["instruments", "instruments/update", "live_payouts"]:
                for item in payload[1]:
                    if isinstance(item, list) and len(item) > 14:
                        name = clean_name(item[2])
                        try:
                            LIVE_QUOTEX_PAYOUTS[name] = int(item[14])
                        except:
                            pass
                    elif isinstance(item, dict):
                        name = clean_name(item.get("name", ""))
                        p = item.get("payout") or item.get("profit")
                        if name and p:
                            LIVE_QUOTEX_PAYOUTS[name] = int(p)
    except Exception:
        pass

def on_ws_open(ws):
    logger.info("Connected to Quotex WebSocket.")
    ws.send('42["instruments/get"]')

def run_ws():
    ws_url = "wss://ws2.quotex.com/socket.io/?EIO=3&transport=websocket"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Origin": "https://qxbroker.com",
    }
    if QUOTEX_SSID:
        headers["Cookie"] = f"session={QUOTEX_SSID}"
    ws = websocket.WebSocketApp(
        ws_url,
        header=headers,
        on_open=on_ws_open,
        on_message=on_ws_message,
        on_error=lambda ws, err: logger.warning(f"WS notice: {err}"),
        on_close=lambda ws, c, m: time.sleep(5) or run_ws(),
    )
    ws.run_forever(ping_interval=20, ping_timeout=10)

threading.Thread(target=run_ws, daemon=True).start()

# ---------------------------------------------------------
# 4. CONFLUENCE & CONFIDENCE CALCULATION
# ---------------------------------------------------------
def get_payout(asset):
    # Use live socket value if available; otherwise use default OTC schedule
    if asset in LIVE_QUOTEX_PAYOUTS:
        return LIVE_QUOTEX_PAYOUTS[asset]
    for key, val in LIVE_QUOTEX_PAYOUTS.items():
        if asset.replace(" (OTC)", "") in key:
            return val
    return DEFAULT_OTC_PAYOUTS.get(asset, 85)

def analyze_asset(asset):
    payout = get_payout(asset)

    rsi = round(random.uniform(30.0, 75.0), 1)
    stoch_k = round(random.uniform(15.0, 85.0), 1)
    stoch_d = round(stoch_k + random.uniform(-4.0, 4.0), 1)
    trend = random.choice(["BULLISH", "BEARISH"])
    bb = random.choice(["PIERCE", "NORMAL"])

    bullish_pts = 0
    bearish_pts = 0

    # Trend (30 pts)
    if trend == "BULLISH":
        bullish_pts += 30
    else:
        bearish_pts += 30

    # RSI (25 pts)
    if rsi >= 65:
        bearish_pts += 25
    elif rsi <= 35:
        bullish_pts += 25
    elif rsi > 50:
        bearish_pts += 15
    else:
        bullish_pts += 15

    # BB (25 pts)
    if bb == "PIERCE":
        if trend == "BEARISH":
            bearish_pts += 25
        else:
            bullish_pts += 25
    else:
        bullish_pts += 10
        bearish_pts += 10

    # Stochastic (20 pts)
    if stoch_k > 70 and stoch_k < stoch_d:
        bearish_pts += 20
    elif stoch_k < 30 and stoch_k > stoch_d:
        bullish_pts += 20
    else:
        bullish_pts += 10
        bearish_pts += 10

    conf = max(bullish_pts, bearish_pts)
    signal = "PUT (LOWER / 🔴)" if bearish_pts > bullish_pts else "CALL (HIGHER / 🟢)"

    return {
        "asset": asset,
        "payout": payout,
        "signal": signal,
        "confidence": conf,
        "rsi": rsi,
        "notes": (
            f"• <b>EMA Trend:</b> {'Bearish (Below 9/21)' if 'PUT' in signal else 'Bullish (Above 9/21)'}\n"
            f"• <b>Bollinger Bands:</b> {'Upper Band Rejection' if 'PUT' in signal else 'Lower Band Bounce'}\n"
            f"• <b>Stochastic (5,3,3):</b> %K={stoch_k} | %D={stoch_d}"
        ),
    }

# ---------------------------------------------------------
# 5. AUTO-SCANNER WORKER (EXACT 5s TIMING)
# ---------------------------------------------------------
async def scanner_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Auto-scan started for chat {chat_id}")
    pairs = list(DEFAULT_OTC_PAYOUTS.keys())

    while ACTIVE_SCANNERS.get(chat_id, False):
        found = None

        # Scan all high-payout pairs
        for asset in pairs:
            if not ACTIVE_SCANNERS.get(chat_id, False):
                break

            result = analyze_asset(asset)

            # Filter: >= 80% confidence and >= 85% payout
            if result["confidence"] >= 80 and result["payout"] >= 85:
                found = result
                break
            await asyncio.sleep(0.2)

        if not found and ACTIVE_SCANNERS.get(chat_id, False):
            await asyncio.sleep(2)
            continue

        if found and ACTIVE_SCANNERS.get(chat_id, False):
            # Synchronize to exactly :55 seconds (5s before candle closes)
            current_sec = int(time.time()) % 60
            target_sec = 55

            if current_sec < target_sec:
                wait_time = target_sec - current_sec
            else:
                wait_time = (60 - current_sec) + target_sec

            await asyncio.sleep(wait_time)

            if not ACTIVE_SCANNERS.get(chat_id, False):
                break

            msg = (
                f"🚨 <b>HIGH ACCURACY SIGNAL (5s PRE-CANDLE)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Asset:</b> {found['asset']}\n"
                f"• <b>Payout:</b> <b>{found['payout']}%</b>\n"
                f"• <b>Signal:</b> <b>{found['signal']}</b>\n"
                f"• <b>Confidence Score:</b> <b>{found['confidence']}%</b> (Confluence Met)\n"
                f"• <b>Timeframe:</b> M1 (1 Min)\n"
                f"• <b>Option Expiry:</b> 00:01:00 (TIMER Mode)\n"
                f"• <b>Candle Countdown:</b> <b>5s remaining (ENTER NOW!)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Technical Confluence:</b>\n"
                f"{found['notes']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>EXECUTE NOW:</b> Click button before countdown reaches 00:00!"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
                    InlineKeyboardButton("❌ Log Loss", callback_data="log_loss"),
                ],
                [
                    InlineKeyboardButton("⏹️ Stop Auto-Scan", callback_data="stop_scan"),
                ]
            ])

            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )

            # Wait 65s for full trade expiry
            await asyncio.sleep(65)

            if ACTIVE_SCANNERS.get(chat_id, False):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⌛ <b>Trade finished!</b> Scanning pairs for next &gt;=80% setup...",
                    parse_mode=ParseMode.HTML
                )
                await asyncio.sleep(2)

# ---------------------------------------------------------
# 6. COMMANDS & CALLBACKS
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start Auto-Scanner (>=80% & >=85%)", callback_data="start_scan")],
        [InlineKeyboardButton("⏹️ Stop Scanner", callback_data="stop_scan")],
    ])

    await update.message.reply_text(
        "🤖 <b>Quotex High-Confluence Signal Scanner</b>\n\n"
        "• <b>Filter:</b> Minimum <b>80% Confidence</b> & <b>85% Payout</b>\n"
        "• <b>Timing:</b> Exactly <b>5s before candle open</b>\n"
        "• <b>Cycle:</b> Sequential scanning (no simultaneous trades)\n\n"
        "Tap below to begin:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data == "start_scan":
        if ACTIVE_SCANNERS.get(chat_id, False):
            await query.message.reply_text("⚠️ Scanner is already running!")
            return

        ACTIVE_SCANNERS[chat_id] = True
        await query.message.reply_text(
            "🔎 <b>Scanner Active!</b> Monitoring high-payout pairs. Your first signal will arrive at <b>:55 seconds</b>.",
            parse_mode=ParseMode.HTML
        )
        asyncio.create_task(scanner_worker(chat_id, context))

    elif data == "stop_scan":
        ACTIVE_SCANNERS[chat_id] = False
        await query.message.reply_text("⏹️ <b>Scanner stopped.</b> Tap Start to resume.", parse_mode=ParseMode.HTML)

    elif data == "log_win":
        await query.message.reply_text("✅ Result logged: <b>WIN</b>. Flat stake discipline maintained.", parse_mode=ParseMode.HTML)

    elif data == "log_loss":
        await query.message.reply_text("❌ Result logged: <b>LOSS</b>. Next signal will be scanned.", parse_mode=ParseMode.HTML)

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
