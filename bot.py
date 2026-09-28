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
# 1. LIVE SCREEN INGESTION & DATA LOCK
# ---------------------------------------------------------
LIVE_BROWSER_PAYOUTS = {}
REAL_CANDLE_HISTORY = {}
RECENT_TICKS = {}
CURRENT_STREAMED_ASSET = "EUR/USD"
LATEST_SCREEN_PRICE = 0.0
DATA_LOCK = threading.Lock()

# Anti-MTG compounding manager per chat
USER_STAKE_CONFIG = {}  # {chat_id: {"base": 100, "current": 100, "step": 1, "max_steps": 3, "streak": 0}}

# Master directory of all Quotex assets
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
    "USD/BDT (OTC)", "USD/BRL (OTC)", "USD/IDR (OTC)", "USD/EGP (OTC)",
    "USD/TRY (OTC)", "USD/NGN (OTC)", "USD/MXN (OTC)", "USD/ARS (OTC)",
    "USD/COP (OTC)", "USD/DZD (OTC)", "USD/PHP (OTC)", "AUD/CAD (OTC)",
    "AUD/CHF (OTC)", "AUD/JPY (OTC)", "AUD/NZD (OTC)", "CAD/CHF (OTC)",
    "CAD/JPY (OTC)", "CHF/JPY (OTC)", "EUR/AUD (OTC)", "EUR/CAD (OTC)",
    "EUR/CHF (OTC)", "EUR/NZD (OTC)", "GBP/AUD (OTC)", "GBP/CAD (OTC)",
    "GBP/CHF (OTC)", "GBP/NZD (OTC)", "NZD/CAD (OTC)", "NZD/CHF (OTC)",
    "NZD/JPY (OTC)"
]

COMMODITIES_ASSETS = [
    "Gold", "Silver", "UK Brent", "US Crude",
    "Gold (OTC)", "Silver (OTC)", "UK Brent (OTC)", "US Crude (OTC)"
]

CRYPTO_ASSETS = [
    "Bitcoin", "Ethereum", "Litecoin", "Ripple", "Solana",
    "Cardano", "Dogecoin", "TRON", "BNB", "Shiba Inu",
    "Bitcoin (OTC)", "Ethereum (OTC)", "Litecoin (OTC)", "Ripple (OTC)"
]

STOCKS_ASSETS = [
    "Apple", "Microsoft", "Tesla", "Boeing", "Amazon", "Google", "Meta",
    "Intel", "Pfizer", "Johnson & Johnson", "McDonald's", "American Express",
    "Apple (OTC)", "Microsoft (OTC)", "Tesla (OTC)", "Boeing (OTC)",
    "Amazon (OTC)", "Google (OTC)", "Meta (OTC)"
]

QUOTEX_MARKETS = {
    "live_forex": {"title": "🌐 LIVE FOREX (34)", "assets": LIVE_FOREX_ASSETS},
    "otc_forex": {"title": "💱 OTC FOREX (41)", "assets": OTC_FOREX_ASSETS},
    "commodities": {"title": "🛢️ COMMODITIES (8)", "assets": COMMODITIES_ASSETS},
    "crypto": {"title": "🪙 CRYPTO (14)", "assets": CRYPTO_ASSETS},
    "stocks": {"title": "📈 STOCKS (19)", "assets": STOCKS_ASSETS},
}

class BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with DATA_LOCK:
            p_count = len(LIVE_BROWSER_PAYOUTS)
            c_count = len(REAL_CANDLE_HISTORY)
            cur_p = LATEST_SCREEN_PRICE
            cur_a = CURRENT_STREAMED_ASSET
        self.wfile.write(
            f"Quotex Engine Online | Pair: {cur_a} | Price: {cur_p} | Payouts: {p_count} | Synced Assets: {c_count}".encode("utf-8")
        )

    def do_POST(self):
        global CURRENT_STREAMED_ASSET, LATEST_SCREEN_PRICE
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        if self.path == "/live_tick":
            try:
                data = json.loads(body)
                price = float(data.get("price", 0))
                asset = data.get("asset", "ACTIVE_CHART").strip()
                live_payout = int(data.get("payout", 0))
                current_time = float(data.get("time", time.time()))
                minute_bucket = int(current_time // 60) * 60

                if price > 0:
                    with DATA_LOCK:
                        if asset != "ACTIVE_CHART":
                            CURRENT_STREAMED_ASSET = asset

                        LATEST_SCREEN_PRICE = price

                        clean_name = asset.replace(" (OTC)", "").strip()
                        if live_payout >= 50:
                            LIVE_BROWSER_PAYOUTS[asset] = live_payout
                            LIVE_BROWSER_PAYOUTS[CURRENT_STREAMED_ASSET] = live_payout
                            LIVE_BROWSER_PAYOUTS[clean_name] = live_payout
                            LIVE_BROWSER_PAYOUTS[clean_name + " (OTC)"] = live_payout

                        for k in [asset, CURRENT_STREAMED_ASSET]:
                            if k not in RECENT_TICKS:
                                RECENT_TICKS[k] = []
                            RECENT_TICKS[k].append((current_time, price))
                            RECENT_TICKS[k] = [t for t in RECENT_TICKS[k] if current_time - t[0] <= 60]

                            if k not in REAL_CANDLE_HISTORY:
                                REAL_CANDLE_HISTORY[k] = []
                            history = REAL_CANDLE_HISTORY[k]

                            if not history or history[-1].get("time") != minute_bucket:
                                history.append({
                                    "time": minute_bucket,
                                    "open": price,
                                    "high": price,
                                    "low": price,
                                    "close": price,
                                })
                                if len(history) > 150:
                                    history.pop(0)
                            else:
                                c = history[-1]
                                c["high"] = max(c["high"], price)
                                c["low"] = min(c["low"], price)
                                c["close"] = price

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"tick_received"}')
                return
            except Exception:
                self.send_response(400)
                self.end_headers()
                return

        elif self.path == "/update_payouts":
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    with DATA_LOCK:
                        for k, v in data.items():
                            LIVE_BROWSER_PAYOUTS[k] = int(v)
                            clean = k.replace(" (OTC)", "").strip()
                            LIVE_BROWSER_PAYOUTS[clean] = int(v)
                            LIVE_BROWSER_PAYOUTS[clean + " (OTC)"] = int(v)
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

        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), BridgeHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# ---------------------------------------------------------
# 2. LOGGING & APPLICATION RUNTIME
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

TIMEFRAME_CONFIG = {
    "1": {"label": "M1 (1 Min)", "seconds": 60, "expiry": "00:01:00"},
    "5": {"label": "M5 (5 Min)", "seconds": 300, "expiry": "00:05:00"},
}

ACTIVE_SCANNERS = {}
TRADE_EVENTS = {}
SCANNER_TASKS = {}
LAST_SENT_CANDLE = {}

# ---------------------------------------------------------
# 3. ANTI-MARTINGALE & TECHNICAL ANALYSIS
# ---------------------------------------------------------
def get_user_anti_mtg(chat_id):
    if chat_id not in USER_STAKE_CONFIG:
        USER_STAKE_CONFIG[chat_id] = {
            "base": 100.0,
            "current": 100.0,
            "step": 1,
            "max_steps": 3,
            "streak": 0
        }
    return USER_STAKE_CONFIG[chat_id]

def update_anti_mtg_outcome(chat_id, outcome: str, payout_pct: int):
    cfg = get_user_anti_mtg(chat_id)
    if outcome == "WIN":
        cfg["streak"] += 1
        if cfg["step"] < cfg["max_steps"]:
            profit = cfg["current"] * (payout_pct / 100.0)
            cfg["current"] = round(cfg["current"] + profit, 2)
            cfg["step"] += 1
        else:
            cfg["current"] = cfg["base"]
            cfg["step"] = 1
    else:
        cfg["current"] = cfg["base"]
        cfg["step"] = 1
        cfg["streak"] = 0

def calculate_ema(series, period):
    if len(series) < period:
        return series[-1] if series else 0.0
    multiplier = 2 / (period + 1)
    ema = sum(series[:period]) / period
    for price in series[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(abs(min(d, 0.0)))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100.0 - (100.0 / (1.0 + (avg_gain / avg_loss))), 2)

def calculate_macd(prices):
    if len(prices) < 26:
        return 0.0, 0.0, 0.0
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    macd_line = ema12 - ema26
    signal_line = macd_line * 0.85
    return macd_line, signal_line, macd_line - signal_line

def evaluate_price_action(candles):
    call_pts, put_pts = 0, 0
    c = candles[-1]
    prev = candles[-2]
    
    body = abs(c["close"] - c["open"])
    c_range = max(c["high"] - c["low"], 0.000001)
    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]

    if (body / c_range) < 0.05 and c_range < 0.00003:
        return -25, -25, "Flatline Doji"

    if c["close"] > c["open"] and prev["close"] < prev["open"]:
        if c["close"] >= prev["open"] and c["open"] <= prev["close"]:
            call_pts += 15

    if c["close"] < c["open"] and prev["close"] > prev["open"]:
        if c["close"] <= prev["open"] and c["open"] >= prev["close"]:
            put_pts += 15

    if lower_wick >= 1.5 * body:
        call_pts += 15
    if upper_wick >= 1.5 * body:
        put_pts += 15

    if c["close"] > c["open"]:
        call_pts += 10
    elif c["close"] < c["open"]:
        put_pts += 10

    return min(call_pts, 25), min(put_pts, 25), "Action Valid"

def evaluate_trend_regime(closes):
    call_pts, put_pts = 0, 0
    p = closes[-1]
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    ema50 = calculate_ema(closes, min(50, len(closes)))

    if p > ema9 >= ema21:
        call_pts += 10
        if ema21 >= ema50:
            call_pts += 5
    elif p < ema9 <= ema21:
        put_pts += 10
        if ema21 <= ema50:
            put_pts += 5
    else:
        if p >= ema9:
            call_pts += 5
        else:
            put_pts += 5

    regime = "Bullish Trend" if call_pts > put_pts else "Bearish Trend" if put_pts > call_pts else "Neutral"
    return call_pts, put_pts, regime

def evaluate_market_structure(candles):
    if len(candles) < 8:
        return 10, 10, "Structure Base"
    highs = [c["high"] for c in candles[-10:]]
    lows = [c["low"] for c in candles[-10:]]
    p = candles[-1]["close"]
    
    swing_high = max(highs[:-2])
    swing_low = min(lows[:-2])

    call_pts, put_pts = 0, 0
    if p >= swing_high:
        call_pts += 20
        status = "BOS Bullish Breakout"
    elif p <= swing_low:
        put_pts += 20
        status = "BOS Bearish Breakdown"
    else:
        mid_low = min(lows[-5:-1])
        mid_high = max(highs[-5:-1])
        if mid_low > swing_low:
            call_pts += 15
            status = "Higher Lows"
        elif mid_high < swing_high:
            put_pts += 15
            status = "Lower Highs"
        else:
            call_pts += 8
            put_pts += 8
            status = "Consolidation"
    return call_pts, put_pts, status

def evaluate_support_resistance(candles, current_price):
    if len(candles) < 15:
        return 5, 5, 0.0, 0.0
    highs = [c["high"] for c in candles[-20:-1]]
    lows = [c["low"] for c in candles[-20:-1]]
    res = max(highs)
    sup = min(lows)

    call_pts, put_pts = 5, 5
    dist_to_res = (res - current_price) / max(current_price, 0.0001)
    dist_to_sup = (current_price - sup) / max(current_price, 0.0001)

    if dist_to_sup <= 0.0005:
        call_pts = 10
        put_pts = 0
    elif dist_to_res <= 0.0005:
        put_pts = 10
        call_pts = 0

    return call_pts, put_pts, res, sup

def evaluate_momentum(closes):
    call_pts, put_pts = 0, 0
    rsi = calculate_rsi(closes, 14)
    _, _, macd_hist = calculate_macd(closes)

    if 50 <= rsi <= 68:
        call_pts += 10
    elif 32 <= rsi <= 50:
        put_pts += 10
    elif rsi > 68:
        put_pts += 10
    elif rsi < 32:
        call_pts += 10

    if macd_hist >= 0:
        call_pts += 5
    else:
        put_pts += 5

    return call_pts, put_pts, rsi, macd_hist

# ---------------------------------------------------------
# 4. STRICT LIVE SCREEN-SYNC SCORING ENGINE
# ---------------------------------------------------------
def run_scoring_architecture(asset, tf_key="1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_sec = tf_data["seconds"]
    rem_sec = total_sec - (int(time.time()) % total_sec)

    clean_asset = asset.replace(" (OTC)", "").strip()

    with DATA_LOCK:
        payout = (
            LIVE_BROWSER_PAYOUTS.get(asset) or
            LIVE_BROWSER_PAYOUTS.get(clean_asset) or
            LIVE_BROWSER_PAYOUTS.get(CURRENT_STREAMED_ASSET, 0)
        )
        curr_p = LATEST_SCREEN_PRICE
        candles_raw = (
            REAL_CANDLE_HISTORY.get(CURRENT_STREAMED_ASSET) or
            REAL_CANDLE_HISTORY.get(asset) or []
        )
        candles = list(candles_raw)

    # STRICT 88%+ PAYOUT FILTER
    if payout < 88:
        return {
            "asset": CURRENT_STREAMED_ASSET, "payout": payout, "signal": "HOLD (LOW PAYOUT)",
            "confidence": 0, "notes": f"Screen payout ({payout}%) below 88% requirement.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p
        }

    # STRICT REAL TICK RECEPTION GUARD
    if curr_p <= 0 or len(candles) < 2:
        return {
            "asset": CURRENT_STREAMED_ASSET, "payout": payout, "signal": "HOLD (AWAITING TICKS)",
            "confidence": 0, "notes": "Awaiting active Lemur Browser WebSocket ticks.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p
        }

    closes = [c["close"] for c in candles]
    closes[-1] = curr_p  # Sync current candle close strictly to the screen tick

    pa_call, pa_put, pa_status = evaluate_price_action(candles)
    tr_call, tr_put, trend_label = evaluate_trend_regime(closes)
    ms_call, ms_put, struct_label = evaluate_market_structure(candles)
    sr_call, sr_put, res, sup = evaluate_support_resistance(candles, curr_p)
    mo_call, mo_put, rsi, macd_h = evaluate_momentum(closes)

    if pa_status == "Flatline Doji":
        return {
            "asset": CURRENT_STREAMED_ASSET, "payout": payout, "signal": "HOLD (DOJI)",
            "confidence": 20, "notes": "Flatline Doji detected. Skipping.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p
        }

    total_call = pa_call + ms_call + tr_call + mo_call + sr_call + 10
    total_put = pa_put + ms_put + tr_put + mo_put + sr_put + 10

    best_score = min(max(total_call, total_put), 98)

    # STRICT 80+ CONFIDENCE THRESHOLD
    if best_score < 80:
        return {
            "asset": CURRENT_STREAMED_ASSET, "payout": payout, "signal": "HOLD (LOW CONFLUENCE)",
            "confidence": best_score, "notes": f"Score {best_score}/100 below 80 threshold.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p
        }

    signal = "CALL (HIGHER / 🟢)" if total_call >= total_put else "PUT (LOWER / 🔴)"

    breakdown = (
        f"• <b>Live Feed Sync:</b> 🟢 100% Direct Screen Match\n"
        f"• <b>Price Action (+25):</b> {pa_status}\n"
        f"• <b>Market Structure (+20):</b> {struct_label}\n"
        f"• <b>Trend Alignment (+15):</b> {trend_label}\n"
        f"• <b>Momentum (+15):</b> RSI {rsi} | MACD Hist: {macd_h:.4f}\n"
        f"• <b>Key S/R (+10):</b> Res: {res:.5f} | Supp: {sup:.5f}"
    )

    return {
        "asset": CURRENT_STREAMED_ASSET,
        "payout": payout,
        "signal": signal,
        "confidence": best_score,
        "notes": breakdown,
        "tf_data": tf_data,
        "remaining_sec": rem_sec,
        "current_price": curr_p
    }

# ---------------------------------------------------------
# 5. ZERO-LAG SCANNER WORKER
# ---------------------------------------------------------
async def scanner_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE, single_asset: str = None, tf_key: str = "1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_seconds = tf_data["seconds"]
    TRADE_EVENTS[chat_id] = asyncio.Event()

    while ACTIVE_SCANNERS.get(chat_id, False):
        try:
            cycle = int(time.time() / total_seconds)
            if LAST_SENT_CANDLE.get(chat_id) == cycle:
                await asyncio.sleep(1.0)
                continue

            target = single_asset if single_asset else CURRENT_STREAMED_ASSET

            if LATEST_SCREEN_PRICE <= 0:
                await asyncio.sleep(1.0)
                continue

            res = run_scoring_architecture(target, tf_key)

            if res["payout"] < 88 or res["confidence"] < 80 or res["signal"].startswith("HOLD"):
                await asyncio.sleep(0.5)
                continue

            curr_sec = int(time.time()) % total_seconds
            target_dispatch = total_seconds - 10

            if curr_sec <= target_dispatch:
                await asyncio.sleep(target_dispatch - curr_sec)
            elif curr_sec > (total_seconds - 3):
                await asyncio.sleep(total_seconds - curr_sec)
                continue

            if not ACTIVE_SCANNERS.get(chat_id, False):
                break

            LAST_SENT_CANDLE[chat_id] = cycle

            if LATEST_SCREEN_PRICE < 100:
                price_str = f"{LATEST_SCREEN_PRICE:.5f}"
            else:
                price_str = f"{LATEST_SCREEN_PRICE:.2f}"

            actual_payout = LIVE_BROWSER_PAYOUTS.get(target, res["payout"])
            anti_cfg = get_user_anti_mtg(chat_id)

            msg = (
                f"🚨 <b>QUOTEX LIVE SIGNAL (SCORE: {res['confidence']}/100)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Asset:</b> {target}\n"
                f"• <b>Current Live Price:</b> <code>{price_str}</code> (Exact Screen Match)\n"
                f"• <b>Payout:</b> <b>{actual_payout}%</b> (From Quotex UI)\n"
                f"• <b>Direction:</b> <b>{res['signal']}</b>\n"
                f"• <b>Chart Timeframe:</b> {res['tf_data']['label']}\n"
                f"• <b>Option Expiry:</b> {res['tf_data']['expiry']}\n"
                f"• <b>Preparation Window:</b> <b>10 SECONDS LEFT &rarr; ENTER AT 00:00</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🛡️ <b>Anti-MTG Money Management:</b>\n"
                f"• <b>Recommended Stake:</b> <code>₹{anti_cfg['current']}</code> (Step {anti_cfg['step']}/{anti_cfg['max_steps']})\n"
                f"• <b>Rule:</b> Compounding win streak. Instant reset to ₹{anti_cfg['base']} on loss.\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Technical Confluence Overview:</b>\n"
                f"{res['notes']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Trade active. Log result below:</i>"
            )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Log Win (Step {anti_cfg['step']})", callback_data=f"log_win_{actual_payout}"),
                 InlineKeyboardButton("❌ Log Loss (Reset)", callback_data="log_loss")],
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_signal"),
                 InlineKeyboardButton("⏹️ Stop Scanner", callback_data="stop_scan")]
            ])

            await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            TRADE_EVENTS[chat_id].clear()
            await asyncio.wait_for(TRADE_EVENTS[chat_id].wait(), timeout=float(total_seconds + 10))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Scanner cycle error: {e}")
            await asyncio.sleep(1.5)

# ---------------------------------------------------------
# 6. TELEGRAM UI & NAVIGATION HANDLERS
# ---------------------------------------------------------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Auto-Scan M1 (Live + OTC)", callback_data="start_scan_1"),
            InlineKeyboardButton("▶️ Auto-Scan M5 (Live + OTC)", callback_data="start_scan_5"),
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
            InlineKeyboardButton("📈 STOCKS & EQUITIES (19)", callback_data="cat_stocks_0"),
        ],
        [
            InlineKeyboardButton("⏹️ Stop Active Scanner", callback_data="stop_scan")
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

def get_signal_keyboard(current_asset):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🎯 Auto-Scan {current_asset} (M1)", callback_data=f"lock_{current_asset}_1"),
            InlineKeyboardButton(f"🎯 Auto-Scan {current_asset} (M5)", callback_data=f"lock_{current_asset}_5"),
        ],
        [
            InlineKeyboardButton("🔄 Re-Analyze M1", callback_data=f"sel_{current_asset}_1"),
            InlineKeyboardButton("🔄 Re-Analyze M5", callback_data=f"sel_{current_asset}_5"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="open_main_menu")
        ]
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    anti_cfg = get_user_anti_mtg(chat_id)
    payout = LIVE_BROWSER_PAYOUTS.get(CURRENT_STREAMED_ASSET, 0)
    p_badge = f"{payout}% (Screen Sync 🟢)" if payout > 0 else "Waiting for Browser Sync..."
    price_val = f"{LATEST_SCREEN_PRICE:.5f}" if LATEST_SCREEN_PRICE > 0 else "Waiting for Browser Tick..."

    await update.message.reply_text(
        f"⚡ <b>Quotex Live + OTC Dual Engine (Anti-MTG)</b>\n\n"
        f"• <b>Live Screen Stream:</b> <code>{CURRENT_STREAMED_ASSET}</code>\n"
        f"• <b>Current Live Price:</b> <code>{price_val}</code>\n"
        f"• <b>Screen Payout:</b> <code>{p_badge}</code>\n"
        f"• <b>Active Scope:</b> Evaluates Live Real-Markets + OTC Markets simultaneously\n"
        f"• <b>Filters:</b> Payout <b>&ge; 88% ONLY</b> &amp; <b>80+ Confluence Score</b>\n\n"
        "Select scanning mode or browse assets below:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

def stop_active_task(chat_id):
    ACTIVE_SCANNERS[chat_id] = False
    if chat_id in TRADE_EVENTS:
        TRADE_EVENTS[chat_id].set()
    if chat_id in SCANNER_TASKS and not SCANNER_TASKS[chat_id].done():
        SCANNER_TASKS[chat_id].cancel()

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    try:
        if data == "open_main_menu":
            await query.edit_message_text(
                "📊 <b>Select trade category or initialize auto-scan:</b>",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("start_scan"):
            tf_choice = "5" if data == "start_scan_5" else "1"
            stop_active_task(chat_id)
            ACTIVE_SCANNERS[chat_id] = True

            await query.message.reply_text(
                f"🔎 <b>Unified Live + OTC Auto-Scanner Started ({TIMEFRAME_CONFIG[tf_choice]['label']})</b>\n\n"
                f"• Priority: Active Phone Chart $\\rightarrow$ All high-payout Live &amp; OTC assets\n"
                f"• Filter: <b>&ge; 88% Payout &amp; &ge; 80/100 Confidence</b>\n"
                f"• Strategy: <b>Anti-MTG (Reverse Martingale Compounding)</b>\n"
                f"• Alerts arrive <b>10 seconds before candle open</b>.",
                parse_mode=ParseMode.HTML
            )
            SCANNER_TASKS[chat_id] = asyncio.create_task(scanner_worker(chat_id, context, single_asset=None, tf_key=tf_choice))

        elif data.startswith("lock_"):
            parts = data.replace("lock_", "").rsplit("_", 1)
            pinned_asset = parts[0]
            tf_choice = parts[1] if len(parts) > 1 and parts[1] in TIMEFRAME_CONFIG else "1"

            stop_active_task(chat_id)
            ACTIVE_SCANNERS[chat_id] = True
            await query.message.reply_text(
                f"🎯 <b>Scanner Locked on: {pinned_asset} ({TIMEFRAME_CONFIG[tf_choice]['label']})</b>\n\n"
                f"Signals evaluate 10s pre-candle open. Tap <b>Stop Active Scanner</b> to release.",
                parse_mode=ParseMode.HTML
            )
            SCANNER_TASKS[chat_id] = asyncio.create_task(scanner_worker(chat_id, context, single_asset=pinned_asset, tf_key=tf_choice))

        elif data == "stop_scan":
            stop_active_task(chat_id)
            await query.message.reply_text("⏹️ <b>Scanner deactivated.</b> Send /start to reopen console.", parse_mode=ParseMode.HTML)

        elif data.startswith("log_win"):
            parts = data.split("_")
            payout_val = int(parts[2]) if len(parts) > 2 else 88
            update_anti_mtg_outcome(chat_id, "WIN", payout_val)
            anti_cfg = get_user_anti_mtg(chat_id)

            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()

            await query.message.reply_text(
                f"✅ <b>WIN Confirmed!</b>\n"
                f"• Anti-MTG Step: <b>{anti_cfg['step']}/{anti_cfg['max_steps']}</b>\n"
                f"• Next Target Stake: <b>₹{anti_cfg['current']}</b> (Compounding profit)\n"
                f"Scanning next 88%+ setup...",
                parse_mode=ParseMode.HTML
            )

        elif data == "log_loss":
            update_anti_mtg_outcome(chat_id, "LOSS", 0)
            anti_cfg = get_user_anti_mtg(chat_id)

            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()

            await query.message.reply_text(
                f"❌ <b>LOSS Logged.</b>\n"
                f"• Anti-MTG Action: <b>NO MARTINGALE</b>.\n"
                f"• Stake Reset: Back to base <b>₹{anti_cfg['base']}</b> (Step 1/3).\n"
                f"Scanning next 88%+ setup...",
                parse_mode=ParseMode.HTML
            )

        elif data == "skip_signal":
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            await query.message.reply_text("⏭️ <b>Signal skipped.</b> Scanning next candle...", parse_mode=ParseMode.HTML)

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

            res = run_scoring_architecture(asset, tf_key)
            price_str = f"{res['current_price']:.5f}" if res['current_price'] < 100 else f"{res['current_price']:.2f}"
            out = (
                f"🎯 <b>Confluence Audit: {res['asset']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Direction:</b> <b>{res['signal']}</b>\n"
                f"• <b>Current Market Price:</b> <code>{price_str}</code>\n"
                f"• <b>Payout:</b> <b>{res['payout']}%</b>\n"
                f"• <b>Confluence Score:</b> <b>{res['confidence']}/100</b>\n"
                f"• <b>Timeframe:</b> {res['tf_data']['label']}\n"
                f"• <b>Candle Expiry:</b> {res['remaining_sec']}s remaining\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Layer Diagnostics:</b>\n"
                f"{res['notes']}"
            )
            await query.edit_message_text(out, reply_markup=get_signal_keyboard(asset), parse_mode=ParseMode.HTML)

    except TelegramError as e:
        logger.warning(f"Telegram callback error: {e}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("Bot starting with Direct Screen Hook & Anti-MTG...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
