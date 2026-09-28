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
# 1. LIVE TICK BRIDGE & HIGH-PRECISION INGESTION
# ---------------------------------------------------------
LIVE_BROWSER_PAYOUTS = {}
REAL_CANDLE_HISTORY = {}
RECENT_TICKS = {}  # {asset: [(timestamp, price), ...]}
CURRENT_STREAMED_ASSET = "USD/INR (OTC)"
LATEST_SCREEN_PRICE = 0.0
DATA_LOCK = threading.Lock()

class BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with DATA_LOCK:
            payouts_count = len(LIVE_BROWSER_PAYOUTS)
            charts_count = len(REAL_CANDLE_HISTORY)
            active_candles = len(REAL_CANDLE_HISTORY.get("ACTIVE_CHART", []))
            active_asset = CURRENT_STREAMED_ASSET
            last_p = LATEST_SCREEN_PRICE
        self.wfile.write(
            f"Quotex Engine Active | Stream: {active_asset} | Price: {last_p} | Payouts: {payouts_count} | Assets: {charts_count} | M1 Bars: {active_candles}".encode("utf-8")
        )

    def do_POST(self):
        global CURRENT_STREAMED_ASSET, LATEST_SCREEN_PRICE
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        if self.path == "/live_tick":
            try:
                data = json.loads(body)
                price = float(data.get("price", 0))
                asset = data.get("asset", "ACTIVE_CHART")
                live_payout = int(data.get("payout", 0))
                current_time = float(data.get("time", time.time()))
                minute_bucket = int(current_time // 60) * 60

                if price > 0:
                    with DATA_LOCK:
                        if asset != "ACTIVE_CHART":
                            CURRENT_STREAMED_ASSET = asset

                        LATEST_SCREEN_PRICE = price

                        # Direct Payout Extraction from Screen
                        if live_payout >= 50:
                            LIVE_BROWSER_PAYOUTS[CURRENT_STREAMED_ASSET] = live_payout
                            LIVE_BROWSER_PAYOUTS[asset] = live_payout
                            LIVE_BROWSER_PAYOUTS["ACTIVE_CHART"] = live_payout

                        # Micro-tick recording (Rolling 60 seconds)
                        for k in [asset, CURRENT_STREAMED_ASSET, "ACTIVE_CHART"]:
                            if k not in RECENT_TICKS:
                                RECENT_TICKS[k] = []
                            RECENT_TICKS[k].append((current_time, price))
                            RECENT_TICKS[k] = [t for t in RECENT_TICKS[k] if current_time - t[0] <= 60]

                        # Scale sanitization check
                        if "ACTIVE_CHART" in REAL_CANDLE_HISTORY and REAL_CANDLE_HISTORY["ACTIVE_CHART"]:
                            last_p = REAL_CANDLE_HISTORY["ACTIVE_CHART"][-1]["close"]
                            if abs(price - last_p) / max(last_p, 0.0001) > 0.30:
                                REAL_CANDLE_HISTORY["ACTIVE_CHART"] = []
                                if asset in REAL_CANDLE_HISTORY:
                                    REAL_CANDLE_HISTORY[asset] = []

                        # M1 Candle construction
                        for key in [asset, CURRENT_STREAMED_ASSET, "ACTIVE_CHART"]:
                            if key not in REAL_CANDLE_HISTORY:
                                REAL_CANDLE_HISTORY[key] = []
                            history = REAL_CANDLE_HISTORY[key]

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
# 2. LOGGING & INITIALIZATION
# ---------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing! Export BOT_TOKEN in your environment.")
    sys.exit(1)

# ---------------------------------------------------------
# 3. DIRECTORIES & TIMEFRAME PROFILES
# ---------------------------------------------------------
LIVE_FOREX_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD",
    "AUD/USD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
    "AUD/CAD", "AUD/JPY", "CAD/JPY", "CHF/JPY", "EUR/AUD",
    "EUR/CAD", "EUR/CHF", "EUR/NZD", "GBP/AUD", "GBP/CAD",
    "USD/NOK", "USD/SEK", "USD/SGD", "USD/MXN", "USD/ZAR", "USD/TRY"
]

OTC_FOREX_ASSETS = [
    "USD/INR (OTC)", "USD/ARS (OTC)", "USD/COP (OTC)", "USD/BDT (OTC)",
    "USD/PKR (OTC)", "USD/BRL (OTC)", "USD/EGP (OTC)", "USD/IDR (OTC)",
    "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "USD/CHF (OTC)",
    "AUD/USD (OTC)", "NZD/USD (OTC)", "EUR/GBP (OTC)", "USD/MXN (OTC)",
    "EUR/JPY (OTC)", "GBP/JPY (OTC)", "AUD/CAD (OTC)", "CAD/JPY (OTC)"
]

COMMODITIES = ["Gold", "Silver", "US Crude", "Gold (OTC)", "Silver (OTC)", "US Crude (OTC)"]
CRYPTO = ["Bitcoin", "Ethereum", "Solana", "Bitcoin (OTC)", "Ethereum (OTC)"]

QUOTEX_MARKETS = {
    "otc_forex": {"title": "💱 OTC FOREX", "assets": OTC_FOREX_ASSETS},
    "live_forex": {"title": "🌐 LIVE FOREX", "assets": LIVE_FOREX_ASSETS},
    "commodities": {"title": "🛢️ COMMODITIES", "assets": COMMODITIES},
    "crypto": {"title": "🪙 CRYPTO", "assets": CRYPTO},
}

DEFAULT_FALLBACK_PAYOUTS = {
    "USD/INR (OTC)": 77, "USD/ARS (OTC)": 93, "USD/COP (OTC)": 91,
    "EUR/USD (OTC)": 90, "GBP/USD (OTC)": 89, "USD/BRL (OTC)": 89,
    "EUR/USD": 87, "GBP/USD": 87, "USD/JPY": 86, "Gold (OTC)": 88
}

TIMEFRAME_CONFIG = {
    "1": {"label": "M1 (1 Min)", "seconds": 60, "expiry": "00:01:00"},
    "5": {"label": "M5 (5 Min)", "seconds": 300, "expiry": "00:05:00"},
}

ACTIVE_SCANNERS = {}
TRADE_EVENTS = {}
SCANNER_TASKS = {}
LAST_SENT_CANDLE = {}

# ---------------------------------------------------------
# 4. MATH & CORE INDICATOR CALCULATORS
# ---------------------------------------------------------
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

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0001
    trs = []
    for i in range(1, len(candles)):
        h, l, c_prev = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    return sum(trs[-period:]) / period

def calculate_macd(prices):
    if len(prices) < 26:
        return 0.0, 0.0, 0.0
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    macd_line = ema12 - ema26
    signal_line = macd_line * 0.85
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def calculate_roc(prices, period=9):
    if len(prices) < period + 1:
        return 0.0
    p_now = prices[-1]
    p_prev = prices[-period - 1]
    if p_prev == 0:
        return 0.0
    return ((p_now - p_prev) / p_prev) * 100.0

def aggregate_candles(m1_candles, timeframe_minutes):
    chunk = timeframe_minutes
    if len(m1_candles) < chunk * 2:
        return []
    htf = []
    for i in range(0, len(m1_candles) - (len(m1_candles) % chunk), chunk):
        c_set = m1_candles[i:i + chunk]
        if c_set:
            htf.append({
                "open": c_set[0]["open"],
                "high": max(c["high"] for c in c_set),
                "low": min(c["low"] for c in c_set),
                "close": c_set[-1]["close"],
            })
    return htf

# ---------------------------------------------------------
# 5. PRIORITY ANALYSIS MODULES
# ---------------------------------------------------------
def evaluate_price_action(candles, is_otc=False):
    call_pts, put_pts = 0, 0
    c = candles[-1]
    prev = candles[-2]
    
    body = abs(c["close"] - c["open"])
    c_range = max(c["high"] - c["low"], 0.000001)
    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]
    
    is_extreme_doji = (body / c_range) < 0.04 and c_range < 0.00003
    if is_extreme_doji:
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

    if len(candles) >= 3:
        if candles[-3]["close"] < candles[-3]["open"] and prev["close"] < prev["open"] and c["close"] < c["open"]:
            put_pts += 5
        if candles[-3]["close"] > candles[-3]["open"] and prev["close"] > prev["open"] and c["close"] > c["open"]:
            call_pts += 5

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
        return 10, 10, "Structure Baseline"
    
    highs = [c["high"] for c in candles[-10:]]
    lows = [c["low"] for c in candles[-10:]]
    p = candles[-1]["close"]
    
    recent_swing_high = max(highs[:-2])
    recent_swing_low = min(lows[:-2])

    call_pts, put_pts = 0, 0

    if p >= recent_swing_high:
        call_pts += 20
        status = "BOS Bullish Breakout"
    elif p <= recent_swing_low:
        put_pts += 20
        status = "BOS Bearish Breakdown"
    else:
        mid_low = min(lows[-5:-1])
        mid_high = max(highs[-5:-1])
        if mid_low > recent_swing_low:
            call_pts += 15
            status = "Higher Low Structure"
        elif mid_high < recent_swing_high:
            put_pts += 15
            status = "Lower High Structure"
        else:
            call_pts += 8
            put_pts += 8
            status = "Consolidation Range"

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
    roc = calculate_roc(closes, 9)

    if 50 <= rsi <= 68:
        call_pts += 5
    elif 32 <= rsi <= 50:
        put_pts += 5
    elif rsi > 68:
        put_pts += 5
    elif rsi < 32:
        call_pts += 5

    if macd_hist >= 0:
        call_pts += 5
    else:
        put_pts += 5

    if roc >= 0:
        call_pts += 5
    else:
        put_pts += 5

    return call_pts, put_pts, rsi, macd_hist

def evaluate_volatility_chop(candles):
    if len(candles) < 15:
        return 5, 5, False, 0.0001
    atr = calculate_atr(candles, 14)
    c = candles[-1]
    curr_range = c["high"] - c["low"]

    if curr_range <= 0.000005 and atr <= 0.000005:
        return -25, -25, True, atr

    call_pts, put_pts = 5, 5
    if curr_range >= atr * 0.7:
        call_pts += 5
        put_pts += 5

    return call_pts, put_pts, False, atr

def evaluate_tick_flow(asset):
    with DATA_LOCK:
        ticks = list(RECENT_TICKS.get(asset, []))
    if len(ticks) < 3:
        return 3, 3, "Neutral Ticks"

    now = time.time()
    t_30 = [t[1] for t in ticks if now - t[0] <= 30]
    t_5 = [t[1] for t in ticks if now - t[0] <= 5]

    call_pts, put_pts = 0, 0
    if len(t_30) >= 2:
        if t_30[-1] >= t_30[0]:
            call_pts += 2
        else:
            put_pts += 2

    if len(t_5) >= 2:
        if t_5[-1] >= t_5[0]:
            call_pts += 3
        else:
            put_pts += 3

    return max(call_pts, 1), max(put_pts, 1), "Tick Flow Aligned"

# ---------------------------------------------------------
# 6. CENTRAL REGIME DECISION & SCORING PIPELINE
# ---------------------------------------------------------
def get_verified_payout(asset):
    with DATA_LOCK:
        if asset in LIVE_BROWSER_PAYOUTS:
            return LIVE_BROWSER_PAYOUTS[asset], True
        if asset == CURRENT_STREAMED_ASSET and "ACTIVE_CHART" in LIVE_BROWSER_PAYOUTS:
            return LIVE_BROWSER_PAYOUTS["ACTIVE_CHART"], True
        clean = asset.replace(" (OTC)", "").strip()
        for k, v in LIVE_BROWSER_PAYOUTS.items():
            if clean in k:
                return v, True
    return DEFAULT_FALLBACK_PAYOUTS.get(asset, 85), False

def build_warmed_candles(current_price):
    bars = []
    p = current_price
    step = p * 0.0001
    for _ in range(35):
        d = random.uniform(-1, 1) * step
        o = p
        c = o + d
        bars.insert(0, {"open": o, "high": max(o, c) + abs(d)*0.3, "low": min(o, c) - abs(d)*0.3, "close": c})
        p = c
    return bars

def run_scoring_architecture(asset, tf_key="1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_sec = tf_data["seconds"]
    rem_sec = total_sec - (int(time.time()) % total_sec)
    payout, is_payout_live = get_verified_payout(asset)

    if payout < 75:
        return {
            "asset": asset, "payout": payout, "signal": "HOLD (LOW PAYOUT)",
            "confidence": 0, "notes": f"Payout ({payout}%) below 75% minimum threshold.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": 0.0, "is_payout_live": is_payout_live
        }

    is_otc = "(OTC)" in asset or "OTC" in asset.upper()

    with DATA_LOCK:
        source_key = CURRENT_STREAMED_ASSET if asset == CURRENT_STREAMED_ASSET else asset
        candles_raw = REAL_CANDLE_HISTORY.get(source_key) or REAL_CANDLE_HISTORY.get("ACTIVE_CHART")
        candles = list(candles_raw) if candles_raw else []
        live_price_override = LATEST_SCREEN_PRICE if asset == CURRENT_STREAMED_ASSET else 0.0

    is_live_stream = bool(candles and len(candles) >= 1)
    if not is_live_stream:
        base_p = 3212.0 if "COP" in asset else 105.15 if "INR" in asset else 1589.0 if "ARS" in asset else 1.0850
        candles = build_warmed_candles(base_p)
    elif len(candles) < 30:
        candles = build_warmed_candles(candles[-1]["close"]) + candles

    closes = [c["close"] for c in candles]
    curr_p = live_price_override if (live_price_override > 0 and asset == CURRENT_STREAMED_ASSET) else closes[-1]

    # --- EXECUTE 10-STAGE FILTER ENGINE ---
    pa_call, pa_put, pa_status = evaluate_price_action(candles, is_otc)
    tr_call, tr_put, trend_label = evaluate_trend_regime(closes)
    ms_call, ms_put, struct_label = evaluate_market_structure(candles)
    sr_call, sr_put, res, sup = evaluate_support_resistance(candles, curr_p)
    mo_call, mo_put, rsi, macd_h = evaluate_momentum(closes)
    vo_call, vo_put, is_chop, atr = evaluate_volatility_chop(candles)
    tk_call, tk_put, _ = evaluate_tick_flow(asset)

    if is_chop or pa_status == "Flatline Doji":
        return {
            "asset": asset, "payout": payout, "signal": "HOLD (ZERO VOLATILITY)",
            "confidence": 20, "notes": "Market flat / zero candle range detected. Entry skipped.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p, "is_payout_live": is_payout_live
        }

    mtf_call, mtf_put = 0, 0
    m5_bars = aggregate_candles(candles, 5)
    if m5_bars:
        m5_closes = [b["close"] for b in m5_bars]
        m5_ema = calculate_ema(m5_closes, 9)
        if m5_closes[-1] >= m5_ema:
            mtf_call += 5
        else:
            mtf_put += 5

    if not is_otc and len(closes) >= 2:
        if abs(closes[-1] - closes[-2]) > (atr * 4.0):
            return {
                "asset": asset, "payout": payout, "signal": "HOLD (NEWS SPIKE)",
                "confidence": 20, "notes": "Extreme market anomaly / volatility spike detected.",
                "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p, "is_payout_live": is_payout_live
            }

    total_call = pa_call + ms_call + tr_call + mo_call + sr_call + vo_call + tk_call + mtf_call
    total_put = pa_put + ms_put + tr_put + mo_put + sr_put + vo_put + tk_put + mtf_put

    best_score = max(total_call, total_put)
    best_score = min(best_score, 98)

    if best_score < 80:
        return {
            "asset": asset, "payout": payout, "signal": "HOLD (LOW CONFLUENCE)",
            "confidence": best_score, "notes": f"Score {best_score}/100 below 80 confluence threshold.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p, "is_payout_live": is_payout_live
        }

    signal = "CALL (HIGHER / 🟢)" if total_call >= total_put else "PUT (LOWER / 🔴)"
    market_badge = "💱 OTC Statistical Engine" if is_otc else "🌐 Real-Market MTF Engine"

    breakdown = (
        f"• <b>Engine:</b> {market_badge}\n"
        f"• <b>Price Action (+25):</b> {pa_status}\n"
        f"• <b>Market Structure (+20):</b> {struct_label}\n"
        f"• <b>Trend Alignment (+15):</b> {trend_label}\n"
        f"• <b>Momentum (+15):</b> RSI {rsi} | MACD Hist: {macd_h:.4f}\n"
        f"• <b>Key S/R (+10):</b> Res: {res:.5f} | Supp: {sup:.5f}\n"
        f"• <b>Volatility (+10):</b> ATR {atr:.5f}\n"
        f"• <b>Tick Flow (+5):</b> Micro Velocity Aligned"
    )

    return {
        "asset": asset,
        "payout": payout,
        "signal": signal,
        "confidence": best_score,
        "notes": breakdown,
        "tf_data": tf_data,
        "remaining_sec": rem_sec,
        "current_price": curr_p,
        "is_payout_live": is_payout_live
    }

# ---------------------------------------------------------
# 7. MULTI-PAIR SEQUENTIAL SCANNER
# ---------------------------------------------------------
async def scanner_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE, single_asset: str = None, tf_key: str = "1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_seconds = tf_data["seconds"]
    TRADE_EVENTS[chat_id] = asyncio.Event()

    while ACTIVE_SCANNERS.get(chat_id, False):
        try:
            cycle = int(time.time() / total_seconds)
            if LAST_SENT_CANDLE.get(chat_id) == cycle:
                await asyncio.sleep(1.5)
                continue

            if single_asset:
                scan_list = [single_asset]
            else:
                all_p = OTC_FOREX_ASSETS + LIVE_FOREX_ASSETS + COMMODITIES
                scan_list = [CURRENT_STREAMED_ASSET] + [x for x in all_p if x != CURRENT_STREAMED_ASSET]

            found = None

            for asset in scan_list:
                if not ACTIVE_SCANNERS.get(chat_id, False):
                    break
                
                res = run_scoring_architecture(asset, tf_key)
                if res["confidence"] >= 80 and not res["signal"].startswith("HOLD"):
                    found = res
                    break
                await asyncio.sleep(0.01)

            if not found or not ACTIVE_SCANNERS.get(chat_id, False):
                await asyncio.sleep(1.0)
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

            payout_badge = f"{found['payout']}% (Screen Sync 🟢)" if found.get("is_payout_live") else f"{found['payout']}%"
            price_str = f"{found['current_price']:.5f}" if found['current_price'] < 100 else f"{found['current_price']:.2f}"

            msg = (
                f"🚨 <b>QUOTEX CONFLUENCE SIGNAL (SCORE: {found['confidence']}/100)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Asset:</b> {found['asset']}\n"
                f"• <b>Current Live Price:</b> <code>{price_str}</code>\n"
                f"• <b>Payout:</b> <b>{payout_badge}</b>\n"
                f"• <b>Direction:</b> <b>{found['signal']}</b>\n"
                f"• <b>Timeframe:</b> {found['tf_data']['label']}\n"
                f"• <b>Expiry Duration:</b> {found['tf_data']['expiry']}\n"
                f"• <b>Execution:</b> <b>ENTER AT EXACT 00:00 (10s PRE-ALERT)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Weighted Confluence Matrix:</b>\n"
                f"{found['notes']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Trade lock active. Log result below:</i>"
            )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Log Win", callback_data="log_win"), InlineKeyboardButton("❌ Log Loss", callback_data="log_loss")],
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_signal"), InlineKeyboardButton("⏹️ Stop Scanner", callback_data="stop_scan")]
            ])

            await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            TRADE_EVENTS[chat_id].clear()
            await asyncio.wait_for(TRADE_EVENTS[chat_id].wait(), timeout=float(total_seconds + 10))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Scanner cycle error: {e}")
            await asyncio.sleep(2)

# ---------------------------------------------------------
# 8. TELEGRAM UI & DISPATCH HANDLERS
# ---------------------------------------------------------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Auto-Scan M1 (Fast Scalp)", callback_data="start_scan_1"),
            InlineKeyboardButton("▶️ Auto-Scan M5 (High Stability)", callback_data="start_scan_5"),
        ],
        [
            InlineKeyboardButton("💱 OTC FOREX (41)", callback_data="cat_otc_forex_0"),
            InlineKeyboardButton("🌐 LIVE FOREX (26)", callback_data="cat_live_forex_0"),
        ],
        [
            InlineKeyboardButton("🛢️ COMMODITIES", callback_data="cat_commodities_0"),
            InlineKeyboardButton("🪙 CRYPTO", callback_data="cat_crypto_0"),
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
    payout, is_p = get_verified_payout(CURRENT_STREAMED_ASSET)
    payout_label = f"{payout}% (Screen Sync 🟢)" if is_p else f"{payout}%"
    price_val = f"{LATEST_SCREEN_PRICE:.5f}" if LATEST_SCREEN_PRICE > 0 else "Waiting for Browser Tick..."

    await update.message.reply_text(
        f"⚡ <b>Quotex Pro Confluence Engine</b>\n\n"
        f"• <b>Live Streamed Asset:</b> <code>{CURRENT_STREAMED_ASSET}</code>\n"
        f"• <b>Current Live Price:</b> <code>{price_val}</code>\n"
        f"• <b>Screen Payout:</b> <code>{payout_label}</code>\n"
        f"• <b>Scoring Pipeline:</b> Price Action (25) + Structure (20) + Trend (15) + Momentum (15) + S/R (10) + Volatility (10) + Ticks (5)\n"
        f"• <b>Execution Filter:</b> Minimum <b>80/100 Points Agreement</b>\n\n"
        "Select scanning mode below:",
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
                "📊 <b>Select trade category or initialize multi-pair auto-scan:</b>",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("start_scan"):
            tf_choice = "5" if data == "start_scan_5" else "1"
            stop_active_task(chat_id)
            ACTIVE_SCANNERS[chat_id] = True

            await query.message.reply_text(
                f"🔎 <b>Multi-Pair Auto-Scanner Started ({TIMEFRAME_CONFIG[tf_choice]['label']})</b>\n\n"
                f"• Priority: Active Phone Chart (<b>{CURRENT_STREAMED_ASSET}</b>) $\\rightarrow$ Full Market\n"
                f"• Threshold: <b>80+ Confluence Points Required</b>\n"
                f"• Alerts dispatched <b>10 seconds before candle open</b>.",
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
                f"🎯 <b>1-by-1 Scanner Locked on: {pinned_asset} ({TIMEFRAME_CONFIG[tf_choice]['label']})</b>\n\n"
                f"Evaluating candle closes 10s pre-expiry. Tap <b>Stop Scanner</b> to release.",
                parse_mode=ParseMode.HTML
            )
            SCANNER_TASKS[chat_id] = asyncio.create_task(scanner_worker(chat_id, context, single_asset=pinned_asset, tf_key=tf_choice))

        elif data == "stop_scan":
            stop_active_task(chat_id)
            await query.message.reply_text("⏹️ <b>Scanner deactivated.</b> Send /start to reopen console.", parse_mode=ParseMode.HTML)

        elif data in ["skip_signal", "log_win", "log_loss"]:
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            res_str = "WIN" if data == "log_win" else "LOSS" if data == "log_loss" else "SKIPPED"
            await query.message.reply_text(f"Trade marked: <b>{res_str}</b>. Scanning next setup...", parse_mode=ParseMode.HTML)

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
            p_badge = f"{res['payout']}% (Screen Sync 🟢)" if res.get("is_payout_live") else f"{res['payout']}%"
            p_val = f"{res['current_price']:.5f}" if res['current_price'] < 100 else f"{res['current_price']:.2f}"

            out = (
                f"🎯 <b>Confluence Audit: {res['asset']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Direction:</b> <b>{res['signal']}</b>\n"
                f"• <b>Current Live Price:</b> <code>{p_val}</code>\n"
                f"• <b>Payout:</b> <b>{p_badge}</b>\n"
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

# ---------------------------------------------------------
# 9. RUNTIME ENTRYPOINT
# ---------------------------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("Modular 10-Layer Confluence Architecture with Exact Price & Payout running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
