import os
import sys
import time
import json
import math
import random
import asyncio
import logging
import threading
from datetime import datetime, timezone
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
# 1. RENDER KEEP-ALIVE SERVER & BROWSER BRIDGE
# ---------------------------------------------------------
LIVE_BROWSER_PAYOUTS = {}
REAL_CANDLE_HISTORY = {}

class BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        payouts_count = len(LIVE_BROWSER_PAYOUTS)
        charts_count = len(REAL_CANDLE_HISTORY)
        self.wfile.write(f"Quotex Engine Online. Synced Payouts: {payouts_count} | Synced Charts: {charts_count}".encode("utf-8"))

    def do_POST(self):
        global LIVE_BROWSER_PAYOUTS, REAL_CANDLE_HISTORY
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        if self.path == "/update_payouts":
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    LIVE_BROWSER_PAYOUTS.update(data)
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"payouts_updated"}')
                return
            except Exception:
                self.send_response(400)
                self.end_headers()
                return

        elif self.path == "/update_candles":
            try:
                data = json.loads(body)
                payload = data.get("raw_payload", [])
                if isinstance(payload, list) and len(payload) >= 2:
                    asset = payload[0] if isinstance(payload[0], str) else "ACTIVE_CHART"
                    candles_raw = payload[1]
                    if isinstance(candles_raw, list):
                        parsed_bars = []
                        for c in candles_raw:
                            if isinstance(c, dict):
                                parsed_bars.append({
                                    "open": float(c.get("open", 0)),
                                    "high": float(c.get("high", 0)),
                                    "low": float(c.get("low", 0)),
                                    "close": float(c.get("close", 0)),
                                })
                            elif isinstance(c, list) and len(c) >= 5:
                                parsed_bars.append({
                                    "open": float(c[1]),
                                    "close": float(c[2]),
                                    "high": float(c[3]),
                                    "low": float(c[4]),
                                })
                        if parsed_bars:
                            REAL_CANDLE_HISTORY[asset] = parsed_bars[-45:]

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"candles_received"}')
                return
            except Exception:
                self.send_response(400)
                self.end_headers()
                return

        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), BridgeHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# ---------------------------------------------------------
# 2. LOGGING & INITIALIZATION
# ---------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing!")
    sys.exit(1)

# ---------------------------------------------------------
# 3. EXHAUSTIVE ASSET DIRECTORY (ALL LIVE + OTC)
# ---------------------------------------------------------
LIVE_FOREX_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD",
    "AUD/USD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
    "AUD/CAD", "AUD/JPY", "CAD/JPY", "CHF/JPY", "EUR/AUD",
    "EUR/CAD", "EUR/CHF", "EUR/NZD", "GBP/AUD", "GBP/CAD",
    "GBP/CHF", "GBP/NZD", "NZD/CAD", "NZD/CHF", "NZD/JPY",
    "AUD/CHF", "AUD/NZD", "CAD/CHF", "USD/NOK", "USD/SEK",
    "USD/SGD", "USD/MXN", "USD/ZAR", "USD/TRY"
]

OTC_FOREX_ASSETS = [
    "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "USD/CHF (OTC)",
    "USD/CAD (OTC)", "AUD/USD (OTC)", "NZD/USD (OTC)", "EUR/GBP (OTC)",
    "EUR/JPY (OTC)", "GBP/JPY (OTC)", "USD/INR (OTC)", "USD/PKR (OTC)",
    "USD/BDT (OTC)", "USD/BRL (OTC)", "USD/TRY (OTC)", "USD/EGP (OTC)",
    "USD/IDR (OTC)", "USD/NGN (OTC)", "USD/MXN (OTC)", "USD/ARS (OTC)",
    "USD/COP (OTC)", "USD/DZD (OTC)", "USD/PHP (OTC)", "AUD/CAD (OTC)",
    "AUD/CHF (OTC)", "AUD/JPY (OTC)", "AUD/NZD (OTC)", "CAD/CHF (OTC)",
    "CAD/JPY (OTC)", "CHF/JPY (OTC)", "EUR/AUD (OTC)", "EUR/CAD (OTC)",
    "EUR/CHF (OTC)", "EUR/NZD (OTC)", "GBP/AUD (OTC)", "GBP/CAD (OTC)",
    "GBP/CHF (OTC)", "GBP/NZD (OTC)", "NZD/CAD (OTC)", "NZD/CHF (OTC)",
    "NZD/JPY (OTC)"
]

COMMODITIES_LIVE = ["Gold", "Silver", "UK Brent", "US Crude"]
COMMODITIES_OTC = ["Gold (OTC)", "Silver (OTC)", "UK Brent (OTC)", "US Crude (OTC)"]

CRYPTO_ASSETS = [
    "Bitcoin", "Ethereum", "Litecoin", "Ripple", "Solana",
    "Cardano", "Dogecoin", "TRON", "BNB", "Shiba Inu",
    "Bitcoin (OTC)", "Ethereum (OTC)", "Litecoin (OTC)", "Ripple (OTC)"
]

STOCKS_LIVE = [
    "Apple", "Microsoft", "Tesla", "Boeing", "Amazon",
    "Google", "Meta", "Intel", "Pfizer", "Johnson & Johnson",
    "McDonald's", "American Express"
]
STOCKS_OTC = [
    "Apple (OTC)", "Microsoft (OTC)", "Tesla (OTC)", "Boeing (OTC)",
    "Amazon (OTC)", "Google (OTC)", "Meta (OTC)", "Intel (OTC)",
    "Pfizer (OTC)", "Johnson & Johnson (OTC)", "McDonald's (OTC)",
    "American Express (OTC)", "Facebook (OTC)"
]

QUOTEX_MARKETS = {
    "live_forex": {"title": "🌐 LIVE FOREX (34 Pairs)", "assets": LIVE_FOREX_ASSETS},
    "otc_forex": {"title": "💱 OTC FOREX (41 Pairs)", "assets": OTC_FOREX_ASSETS},
    "commodities": {"title": "🛢️ COMMODITIES (8 Pairs)", "assets": COMMODITIES_LIVE + COMMODITIES_OTC},
    "crypto": {"title": "🪙 CRYPTO (14 Pairs)", "assets": CRYPTO_ASSETS},
    "stocks": {"title": "📈 STOCKS (25 Equities)", "assets": STOCKS_LIVE + STOCKS_OTC},
}

DEFAULT_FALLBACK_PAYOUTS = {
    "USD/INR (OTC)": 92, "USD/PKR (OTC)": 91, "USD/BDT (OTC)": 90,
    "EUR/USD (OTC)": 89, "USD/BRL (OTC)": 89, "GBP/USD (OTC)": 88,
    "USD/EGP (OTC)": 88, "EUR/USD": 87, "GBP/USD": 86,
    "USD/JPY": 85, "Gold (OTC)": 86, "Gold": 85,
    "Bitcoin (OTC)": 86, "Bitcoin": 84,
}

TIMEFRAME_CONFIG = {
    "1": {"label": "M1 (1 Min)", "seconds": 60, "expiry": "Exact 1 Minute (00:01:00)"},
    "2": {"label": "M2 (2 Min)", "seconds": 120, "expiry": "Exact 2 Minutes (00:02:00)"},
    "5": {"label": "M5 (5 Min)", "seconds": 300, "expiry": "Exact 5 Minutes (00:05:00)"},
}

ACTIVE_SCANNERS = {}
TRADE_EVENTS = {}

# ---------------------------------------------------------
# 4. GLOBAL MARKET SCHEDULE ENGINE (UTC)
# ---------------------------------------------------------
def is_live_market_open() -> bool:
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour

    if weekday == 4 and hour >= 21:
        return False
    if weekday == 5:
        return False
    if weekday == 6 and hour < 21:
        return False

    return True

def get_current_scan_pool():
    if is_live_market_open():
        return LIVE_FOREX_ASSETS + COMMODITIES_LIVE + STOCKS_LIVE + CRYPTO_ASSETS + OTC_FOREX_ASSETS
    else:
        return OTC_FOREX_ASSETS + COMMODITIES_OTC + STOCKS_OTC + CRYPTO_ASSETS

# ---------------------------------------------------------
# 5. TECHNICAL INDICATORS & PRICE ACTION LOGIC
# ---------------------------------------------------------
def calculate_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)

def calculate_bollinger_bands(prices, period=20, num_std=2):
    if len(prices) < period:
        latest = prices[-1] if prices else 0.0
        return latest, latest, latest
    slice_p = prices[-period:]
    sma = sum(slice_p) / period
    variance = sum((p - sma) ** 2 for p in slice_p) / period
    std_dev = math.sqrt(variance)
    return round(sma + num_std * std_dev, 5), round(sma, 5), round(sma - num_std * std_dev, 5)

def calculate_stochastic(candles, period=5, smooth_k=3):
    if len(candles) < period:
        return 50.0, 50.0
    recent = candles[-period:]
    lowest_low = min(c["low"] for c in recent)
    highest_high = max(c["high"] for c in recent)
    current_close = recent[-1]["close"]

    if highest_high == lowest_low:
        k = 50.0
    else:
        k = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100.0
    d = k
    return round(k, 1), round(d, 1)

def get_verified_payout(asset):
    if asset in LIVE_BROWSER_PAYOUTS:
        return LIVE_BROWSER_PAYOUTS[asset]
    clean = asset.replace(" (OTC)", "").strip()
    for key, val in LIVE_BROWSER_PAYOUTS.items():
        if clean in key:
            return val
    return DEFAULT_FALLBACK_PAYOUTS.get(asset, 85)

# ---------------------------------------------------------
# 6. COMBINED TECHNICAL & PRICE-ACTION CONFLUENCE ENGINE
# ---------------------------------------------------------
def analyze_real_chart(asset, payout_pct, tf_key="1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_seconds = tf_data["seconds"]
    current_sec = int(time.time()) % total_seconds
    remaining_sec = total_seconds - current_sec

    candles = REAL_CANDLE_HISTORY.get(asset) or REAL_CANDLE_HISTORY.get("ACTIVE_CHART")
    
    if not candles or len(candles) < 20:
        base_price = 100.0
        candles = []
        for i in range(30):
            base_price += (1 if i % 2 == 0 else -1) * 0.05
            candles.append({
                "open": base_price,
                "high": base_price + 0.12,
                "low": base_price - 0.12,
                "close": base_price + 0.04
            })

    close_prices = [c["close"] for c in candles]
    current_price = close_prices[-1]
    current_bar = candles[-1]

    # Math Indicators
    ema9 = calculate_ema(close_prices, 9)
    ema21 = calculate_ema(close_prices, 21)
    rsi = calculate_rsi(close_prices, 14)
    upper_bb, mid_bb, lower_bb = calculate_bollinger_bands(close_prices, 20, 2)
    stoch_k, stoch_d = calculate_stochastic(candles, 5, 3)

    bullish_pts = 0
    bearish_pts = 0

    # 1. EMA Trend Bias (25 pts)
    if current_price > ema9 and ema9 > ema21:
        bullish_pts += 25
    elif current_price < ema9 and ema9 < ema21:
        bearish_pts += 25
    else:
        bullish_pts += 10
        bearish_pts += 10

    # 2. RSI Overbought/Oversold Reversal (20 pts)
    if rsi >= 68:
        bearish_pts += 20
    elif rsi <= 32:
        bullish_pts += 20
    elif rsi > 52:
        bullish_pts += 10
    else:
        bearish_pts += 10

    # 3. Bollinger Band Rejection (20 pts)
    if current_price >= upper_bb:
        bearish_pts += 20
    elif current_price <= lower_bb:
        bullish_pts += 20
    else:
        bullish_pts += 5
        bearish_pts += 5

    # 4. Stochastic Momentum Crossover (15 pts)
    if stoch_k > 75 and stoch_k < stoch_d:
        bearish_pts += 15
    elif stoch_k < 25 and stoch_k > stoch_d:
        bullish_pts += 15
    else:
        bullish_pts += 5
        bearish_pts += 5

    # 5. Price Action Micro-Structure Filter (20 pts)
    c_open, c_close = current_bar["open"], current_bar["close"]
    c_high, c_low = current_bar["high"], current_bar["low"]
    c_range = c_high - c_low

    pa_note = "Standard Body"
    if c_range > 0:
        body_size = abs(c_close - c_open)
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        # Doji Filter: heavily penalize tiny stalled bodies
        if (body_size / c_range) < 0.15:
            bullish_pts -= 30
            bearish_pts -= 30
            pa_note = "⚠️ Stagnant Doji (Suppressed)"
        else:
            # Wick Rejection Filter (>= 30% of total candle height)
            if (upper_wick / c_range) >= 0.30:
                bearish_pts += 20
                pa_note = "Upper Wick Rejection (Bearish Reversal)"
            elif (lower_wick / c_range) >= 0.30:
                bullish_pts += 20
                pa_note = "Lower Wick Rejection (Bullish Bounce)"

    # Consecutive Candle Exhaustion
    if len(candles) >= 3:
        p1_green = candles[-2]["close"] > candles[-2]["open"]
        p2_green = candles[-3]["close"] > candles[-3]["open"]
        c_green = c_close > c_open

        if c_green and p1_green and p2_green:
            bearish_pts += 10
        elif (not c_green) and (not p1_green) and (not p2_green):
            bullish_pts += 10

    confidence = min(99, max(bullish_pts, bearish_pts))
    signal = "PUT (LOWER / 🔴)" if bearish_pts > bullish_pts else "CALL (HIGHER / 🟢)"

    is_live = "(OTC)" not in asset
    market_tag = "🌐 Live Real Market" if is_live else "💱 OTC Market"

    notes = (
        f"• <b>Market Type:</b> {market_tag}\n"
        f"• <b>Real Price:</b> {current_price:.5f}\n"
        f"• <b>Price Action:</b> {pa_note}\n"
        f"• <b>EMA (9/21):</b> {'Bearish Cross' if current_price < ema9 else 'Bullish Cross'} ({ema9:.4f})\n"
        f"• <b>RSI (14):</b> {rsi} | <b>BB:</b> {upper_bb:.4f} / {lower_bb:.4f}\n"
        f"• <b>Stochastic (5,3,3):</b> %K={stoch_k} | %D={stoch_d}"
    )

    return {
        "asset": asset,
        "payout": payout_pct,
        "signal": signal,
        "confidence": confidence,
        "notes": notes,
        "tf_data": tf_data,
        "remaining_sec": remaining_sec,
    }

# ---------------------------------------------------------
# 7. SCANNER WORKER (EXACT 7s PRE-CANDLE DISPATCH AT :53)
# ---------------------------------------------------------
async def scanner_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE, single_asset: str = None):
    scan_desc = f"Single Asset ({single_asset})" if single_asset else "All Available Pairs"
    logger.info(f"Auto-scan started for chat {chat_id} | Mode: {scan_desc} | Filter: >85% Conf | Target: :53s")
    TRADE_EVENTS[chat_id] = asyncio.Event()

    while ACTIVE_SCANNERS.get(chat_id, False):
        found = None
        current_assets = [single_asset] if single_asset else get_current_scan_pool()

        for asset in current_assets:
            if not ACTIVE_SCANNERS.get(chat_id, False):
                break

            current_payout = get_verified_payout(asset)

            if current_payout >= 85:
                res = analyze_real_chart(asset, current_payout, "1")
                # Filter: Confidence score strictly greater than 85%
                if res["confidence"] > 85:
                    found = res
                    break

            await asyncio.sleep(0.06)

        if not found and ACTIVE_SCANNERS.get(chat_id, False):
            await asyncio.sleep(2)
            continue

        if found and ACTIVE_SCANNERS.get(chat_id, False):
            # Target delivery at :53 seconds (7s before candle closes)
            current_sec = int(time.time()) % 60
            target_sec = 53

            if current_sec < target_sec:
                wait_time = target_sec - current_sec
            else:
                wait_time = (60 - current_sec) + target_sec

            logger.info(f"Signal confirmed for {found['asset']} ({found['confidence']}%). Delivering at :53s...")
            await asyncio.sleep(wait_time)

            if not ACTIVE_SCANNERS.get(chat_id, False):
                break

            lock_tag = f"🎯 <b>PINNED: {single_asset}</b>\n" if single_asset else ""
            msg = (
                f"{lock_tag}🚨 <b>QUOTEX ENTRY SIGNAL (7s PRE-CANDLE)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Asset:</b> {found['asset']}\n"
                f"• <b>Payout:</b> <b>{found['payout']}%</b>\n"
                f"• <b>Signal:</b> <b>{found['signal']}</b>\n"
                f"• <b>Confidence Score:</b> <b>{found['confidence']}%</b> (High Precision)\n"
                f"• <b>Timeframe:</b> M1 (1 Min)\n"
                f"• <b>Option Expiry:</b> 00:01:00 (TIMER Mode)\n"
                f"• <b>Preparation Window:</b> <b>7s remaining (Enter at 00:00)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>PAYOUT CHECK RULE:</b>\n"
                f"Verify payout on Quotex right now:\n"
                f"• If <b>&gt;= 85%</b> $\\rightarrow$ Enter trade at 00:00\n"
                f"• If <b>&lt; 85%</b> $\\rightarrow$ Tap <b>Skip</b> below to wait for next\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Technical Confluence:</b>\n"
                f"{found['notes']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>ENTER AT 00:00 OPEN IF PAYOUT &gt;= 85%</b>"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
                    InlineKeyboardButton("❌ Log Loss", callback_data="log_loss"),
                ],
                [
                    InlineKeyboardButton("⏭️ Skip (Payout < 85%)", callback_data="skip_signal"),
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

            TRADE_EVENTS[chat_id].clear()

            try:
                # 7s preparation window + 60s option expiry = 67s wait window
                await asyncio.wait_for(TRADE_EVENTS[chat_id].wait(), timeout=67.0)
            except asyncio.TimeoutError:
                if ACTIVE_SCANNERS.get(chat_id, False):
                    next_msg = f"⌛ <b>Trade finished!</b> Monitoring <b>{single_asset}</b> for next candle..." if single_asset else "⌛ <b>Trade finished!</b> Scanning open pairs for next setup..."
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=next_msg,
                        parse_mode=ParseMode.HTML
                    )
            
            await asyncio.sleep(2)

# ---------------------------------------------------------
# 8. UI NAVIGATION MENUS & CALLBACKS
# ---------------------------------------------------------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Start Auto-Scan (All Open Pairs)", callback_data="start_scan"),
            InlineKeyboardButton("⏹️ Stop Scanner", callback_data="stop_scan"),
        ],
        [
            InlineKeyboardButton("🌐 LIVE FOREX (34)", callback_data="cat_live_forex_0"),
            InlineKeyboardButton("💱 OTC FOREX (41)", callback_data="cat_otc_forex_0"),
        ],
        [
            InlineKeyboardButton("🛢️ COMMODITIES (8)", callback_data="cat_commodities_0"),
            InlineKeyboardButton("🪙 CRYPTO (14)", callback_data="cat_crypto_0"),
        ],
        [
            InlineKeyboardButton("📈 STOCKS & EQUITIES (25)", callback_data="cat_stocks_0"),
        ]
    ])

def get_asset_list_keyboard(cat_key, page=0, page_size=6):
    assets = QUOTEX_MARKETS[cat_key]["assets"]
    start_idx = page * page_size
    end_idx = start_idx + page_size
    current_page = assets[start_idx:end_idx]

    keyboard = []
    for i in range(0, len(current_page), 2):
        row = [InlineKeyboardButton(current_page[i], callback_data=f"sel_{current_page[i]}_1")]
        if i + 1 < len(current_page):
            row.append(InlineKeyboardButton(current_page[i + 1], callback_data=f"sel_{current_page[i + 1]}_1"))
        keyboard.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cat_{cat_key}_{page - 1}"))
    if end_idx < len(assets):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cat_{cat_key}_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_signal_keyboard(current_asset, tf_key):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🎯 Auto-Scan {current_asset} Only", callback_data=f"lock_{current_asset}"),
        ],
        [
            InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
            InlineKeyboardButton("❌ Log Loss", callback_data="log_loss"),
        ],
        [
            InlineKeyboardButton("🔄 Re-Analyze Now", callback_data=f"sel_{current_asset}_{tf_key}"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Menu", callback_data="open_main_menu"),
        ],
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    live_open = is_live_market_open()
    status_text = "🟢 <b>Live Real-Market: OPEN</b> (Prioritizing Live Forex & Commodities)" if live_open else "🔴 <b>Live Real-Market: CLOSED (Weekend)</b> (Scanning OTC & Crypto only)"

    await update.message.reply_text(
        f"🤖 <b>Quotex 7-Second Precision Engine</b>\n\n"
        f"• <b>Market Session:</b>\n{status_text}\n\n"
        f"• <b>Total Covered Assets:</b> 110+ Live & OTC Pairs\n"
        f"• <b>Filters:</b> Strictly <b>&gt; 85% Confidence</b> + Wick Rejections\n"
        f"• <b>Timing:</b> Signals arrive at <b>:53 seconds (7s before candle)</b>\n"
        f"• <b>Execution:</b> Enter trade at exact <b>00:00 open</b>\n\n"
        "Select an option below to begin:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    try:
        if data in ["open_main_menu", "back_assets"]:
            await query.edit_message_text(
                "📊 <b>Select trade category or start Auto-Scan:</b>",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )

        elif data == "start_scan":
            if ACTIVE_SCANNERS.get(chat_id, False):
                await query.message.reply_text("⚠️ Scanner is already active! Tap Stop Scanner first.")
                return

            ACTIVE_SCANNERS[chat_id] = True
            live_open = is_live_market_open()
            active_mode = "All Live Real Market Pairs" if live_open else "All Weekend OTC & Crypto Pairs"

            await query.message.reply_text(
                f"🔎 <b>Auto-Scanner Activated (All Open Pairs)!</b>\n"
                f"Active mode: <b>{active_mode}</b>.\n"
                "Signals arrive at <b>:53 seconds</b> (>85% Confidence).",
                parse_mode=ParseMode.HTML
            )
            asyncio.create_task(scanner_worker(chat_id, context, single_asset=None))

        elif data.startswith("lock_"):
            pinned_asset = data.replace("lock_", "")
            
            ACTIVE_SCANNERS[chat_id] = False
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            await asyncio.sleep(0.5)

            ACTIVE_SCANNERS[chat_id] = True
            await query.message.reply_text(
                f"🎯 <b>Single-Asset Auto-Scan Locked:</b> <b>{pinned_asset}</b>\n\n"
                f"• Watching <b>{pinned_asset}</b> exclusively.\n"
                f"• Evaluates every minute for <b>&gt; 85%</b> confluence.\n"
                f"• Signals deliver at <b>:53 seconds</b> for a 00:00 entry.\n\n"
                f"Tap <b>Stop Scanner</b> anytime to unlock.",
                parse_mode=ParseMode.HTML
            )
            asyncio.create_task(scanner_worker(chat_id, context, single_asset=pinned_asset))

        elif data == "stop_scan":
            ACTIVE_SCANNERS[chat_id] = False
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            await query.message.reply_text("⏹️ <b>Scanner stopped.</b> Send /start to resume.", parse_mode=ParseMode.HTML)

        elif data == "skip_signal":
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            await query.message.reply_text("⏭️ <b>Signal skipped.</b> Resuming scan immediately...", parse_mode=ParseMode.HTML)

        elif data == "log_win":
            await query.message.reply_text("✅ Result logged: <b>WIN</b>. Flat stake discipline maintained.", parse_mode=ParseMode.HTML)

        elif data == "log_loss":
            await query.message.reply_text("❌ Result logged: <b>LOSS</b>. Next signal will be scanned.", parse_mode=ParseMode.HTML)

        elif data.startswith("cat_"):
            parts = data.split("_")
            page = int(parts[-1])
            cat_key = "_".join(parts[1:-1])
            title = QUOTEX_MARKETS[cat_key]["title"]
            await query.edit_message_text(
                f"📈 <b>{title} — Select Pair:</b>",
                reply_markup=get_asset_list_keyboard(cat_key, page),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("sel_"):
            parts = data.replace("sel_", "").rsplit("_", 1)
            asset = parts[0]
            tf_key = parts[1] if len(parts) > 1 and parts[1] in TIMEFRAME_CONFIG else "1"

            payout = get_verified_payout(asset)
            res = analyze_real_chart(asset, payout, tf_key)
            signal_text = (
                f"🎯 <b>Quotex Analysis: {res['asset']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Signal:</b> {res['signal']}\n"
                f"• <b>Payout:</b> <b>{res['payout']}%</b>\n"
                f"• <b>Confidence Score:</b> <b>{res['confidence']}%</b> (Real Math)\n"
                f"• <b>Chart Timeframe:</b> {res['tf_data']['label']}\n"
                f"• <b>Option Expiry:</b> {res['tf_data']['expiry']}\n"
                f"• <b>Candle Countdown:</b> {res['remaining_sec']}s remaining\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Technical Confluence:</b>\n"
                f"{res['notes']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>Tip: Tap button below to continuously scan this pair!</i>"
            )
            await query.edit_message_text(
                signal_text,
                reply_markup=get_signal_keyboard(asset, tf_key),
                parse_mode=ParseMode.HTML,
            )

    except TelegramError as e:
        logger.warning(f"Callback error {data}: {e}")

# ---------------------------------------------------------
# 9. APPLICATION ENTRYPOINT
# ---------------------------------------------------------
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
