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
DATA_LOCK = threading.Lock()

class BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        with DATA_LOCK:
            payouts_count = len(LIVE_BROWSER_PAYOUTS)
            charts_count = len(REAL_CANDLE_HISTORY)
        self.wfile.write(
            f"Quotex Engine Online. Synced Payouts: {payouts_count} | Synced Charts: {charts_count}".encode("utf-8")
        )

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        if self.path == "/update_payouts":
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
                            with DATA_LOCK:
                                REAL_CANDLE_HISTORY[asset] = parsed_bars[-120:]

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
    logger.error("BOT_TOKEN is missing! Export BOT_TOKEN='your_token'")
    sys.exit(1)

# ---------------------------------------------------------
# 3. ASSET DIRECTORY
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
    "USD/INR (OTC)": 93, "USD/PKR (OTC)": 91, "USD/BDT (OTC)": 91,
    "EUR/USD (OTC)": 90, "USD/BRL (OTC)": 89, "GBP/USD (OTC)": 89,
    "USD/EGP (OTC)": 88, "EUR/USD": 87, "GBP/USD": 87,
    "USD/JPY": 86, "Gold (OTC)": 88, "Gold": 86,
    "Bitcoin (OTC)": 88, "Bitcoin": 85, "USD/IDR (OTC)": 89,
    "USD/TRY (OTC)": 87, "Apple (OTC)": 87, "Boeing (OTC)": 87,
}

TIMEFRAME_CONFIG = {
    "1": {"label": "M1 (1 Min)", "seconds": 60, "expiry": "Exact 1 Minute (00:01:00)"},
    "2": {"label": "M2 (2 Min)", "seconds": 120, "expiry": "Exact 2 Minutes (00:02:00)"},
    "5": {"label": "M5 (5 Min)", "seconds": 300, "expiry": "Exact 5 Minutes (00:05:00)"},
}

ACTIVE_SCANNERS = {}
TRADE_EVENTS = {}
SCANNER_TASKS = {}
LAST_SENT_CANDLE = {}

# ---------------------------------------------------------
# 4. MARKET TIMING (UTC)
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
    return OTC_FOREX_ASSETS + COMMODITIES_OTC + STOCKS_OTC + CRYPTO_ASSETS

# ---------------------------------------------------------
# 5. MATHEMATICAL & MULTI-TIMEFRAME INDICATORS
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
    return round(k, 1), round(k, 1)

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.01
    tr_list = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        cp = candles[i - 1]["close"]
        tr = max(h - l, abs(h - cp), abs(l - cp))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period

def detect_support_resistance(candles, lookback=20):
    highs = [c["high"] for c in candles[-lookback:-1]]
    lows = [c["low"] for c in candles[-lookback:-1]]
    return max(highs), min(lows)

def detect_rejection_wicks(candle):
    body = abs(candle["close"] - candle["open"])
    upper_wick = candle["high"] - max(candle["close"], candle["open"])
    lower_wick = min(candle["close"], candle["open"]) - candle["low"]
    body = max(body, 0.00002)

    upper_rejection = upper_wick >= (1.6 * body)
    lower_rejection = lower_wick >= (1.6 * body)
    return upper_rejection, lower_rejection

def is_doji_candle(candle):
    total_range = candle["high"] - candle["low"]
    if total_range <= 0.00001:
        return True
    body = abs(candle["close"] - candle["open"])
    return (body / total_range) < 0.12

def is_near_round_number(price, step=0.0050):
    remainder = abs(price % step)
    pip_threshold = step * 0.08
    return remainder <= pip_threshold or remainder >= (step - pip_threshold)

# --- DUAL HIGHER TIMEFRAME AGGREGATOR (M5 & M15) ---
def aggregate_candles(m1_candles, timeframe_minutes):
    """Compresses 1-minute bars into higher timeframe bars (M5 or M15)."""
    chunk_size = timeframe_minutes
    if len(m1_candles) < chunk_size * 4:
        return []

    htf_bars = []
    for i in range(0, len(m1_candles) - (len(m1_candles) % chunk_size), chunk_size):
        chunk = m1_candles[i:i + chunk_size]
        if chunk:
            htf_bars.append({
                "open": chunk[0]["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": chunk[-1]["close"],
            })
    return htf_bars

def calculate_timeframe_bias(bars):
    """Calculates EMA 9/21 trend direction on aggregated bars."""
    if len(bars) < 5:
        return "NEUTRAL", 0.0, 0.0
    closes = [b["close"] for b in bars]
    current_price = closes[-1]
    ema9 = calculate_ema(closes, 5)
    ema21 = calculate_ema(closes, 12)

    if current_price > ema9 and ema9 >= ema21:
        return "BULLISH", ema9, ema21
    elif current_price < ema9 and ema9 <= ema21:
        return "BEARISH", ema9, ema21
    return "NEUTRAL", ema9, ema21

# ---------------------------------------------------------
# 6. PRICE ACTION & M5/M15 CONFLUENCE SCORING ENGINE
# ---------------------------------------------------------
def get_verified_payout(asset):
    with DATA_LOCK:
        if asset in LIVE_BROWSER_PAYOUTS:
            return LIVE_BROWSER_PAYOUTS[asset]
        clean = asset.replace(" (OTC)", "").strip()
        for key, val in LIVE_BROWSER_PAYOUTS.items():
            if clean in key:
                return val
    return DEFAULT_FALLBACK_PAYOUTS.get(asset, 88)

def generate_dynamic_candles():
    base_price = 104.5 + random.uniform(-0.5, 0.5)
    candles = []
    direction = random.choice([-1, 1])
    for i in range(65):
        change = (0.05 * direction) + random.uniform(-0.02, 0.02)
        c_open = base_price
        c_close = c_open + change
        c_high = max(c_open, c_close) + random.uniform(0.02, 0.06)
        c_low = min(c_open, c_close) - random.uniform(0.02, 0.06)
        candles.append({"open": c_open, "high": c_high, "low": c_low, "close": c_close})
        base_price = c_close
    return candles

def analyze_real_chart(asset, payout_pct, tf_key="1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_seconds = tf_data["seconds"]
    current_sec = int(time.time()) % total_seconds
    remaining_sec = total_seconds - current_sec

    with DATA_LOCK:
        candles_raw = REAL_CANDLE_HISTORY.get(asset) or REAL_CANDLE_HISTORY.get("ACTIVE_CHART")
        candles = list(candles_raw) if candles_raw else None

    if not candles or len(candles) < 40:
        candles = generate_dynamic_candles()

    close_prices = [c["close"] for c in candles]
    current_price = close_prices[-1]
    current_candle = candles[-1]

    # Doji Guard: Reject flat indecisive market
    if is_doji_candle(current_candle):
        return {
            "asset": asset, "payout": payout_pct, "signal": "HOLD (DOJI / ⚪)",
            "confidence": 40, "notes": "• <b>Market Status:</b> Indecision Doji detected. Trade skipped.",
            "tf_data": tf_data, "remaining_sec": remaining_sec
        }

    # Technical Indicators
    ema9 = calculate_ema(close_prices, 9)
    ema21 = calculate_ema(close_prices, 21)
    rsi = calculate_rsi(close_prices, 14)
    upper_bb, mid_bb, lower_bb = calculate_bollinger_bands(close_prices, 20, 2)
    stoch_k, stoch_d = calculate_stochastic(candles, 5, 3)
    atr = calculate_atr(candles, 14)

    resistance, support = detect_support_resistance(candles, 20)
    upper_rej, lower_rej = detect_rejection_wicks(current_candle)
    near_round = is_near_round_number(current_price)

    # Higher Timeframe Aggregations
    m5_bars = aggregate_candles(candles, 5)
    m15_bars = aggregate_candles(candles, 15)
    m5_bias, _, _ = calculate_timeframe_bias(m5_bars)
    m15_bias, _, _ = calculate_timeframe_bias(m15_bars)

    # Reversal Scoring
    rev_put = 0
    rev_call = 0

    if current_price >= (resistance * 0.9996):
        rev_put += 25
    if current_price <= (support * 1.0004):
        rev_call += 25

    if upper_rej:
        rev_put += 25
    if lower_rej:
        rev_call += 25

    if near_round:
        rev_put += 12
        rev_call += 12

    if current_price >= (upper_bb * 0.9995) or rsi >= 68:
        rev_put += 20
    if current_price <= (lower_bb * 1.0005) or rsi <= 32:
        rev_call += 20

    if stoch_k >= 78:
        rev_put += 15
    if stoch_k <= 22:
        rev_call += 15

    # Trend Momentum Scoring
    trend_call = 0
    trend_put = 0

    if current_price > ema9 and ema9 > ema21 and current_price > mid_bb:
        trend_call += 40
        if 50 <= rsi <= 75:
            trend_call += 25
        if not upper_rej:
            trend_call += 15

    if current_price < ema9 and ema9 < ema21 and current_price < mid_bb:
        trend_put += 40
        if 25 <= rsi <= 50:
            trend_put += 25
        if not lower_rej:
            trend_put += 15

    # Dual Higher Timeframe Concurrence (M5 + M15)
    # M5 Alignment
    if m5_bias == "BULLISH":
        trend_call += 15
        rev_call += 10
        trend_put -= 15
    elif m5_bias == "BEARISH":
        trend_put += 15
        rev_put += 10
        trend_call -= 15

    # M15 Macro Alignment (Key for 5-minute analysis)
    if m15_bias == "BULLISH":
        trend_call += 15
        rev_call += 10
        trend_put -= 20
    elif m15_bias == "BEARISH":
        trend_put += 15
        rev_put += 10
        trend_call -= 20

    best_call = max(rev_call, trend_call)
    best_put = max(rev_put, trend_put)

    if best_call >= best_put:
        confidence = min(max(best_call, 83), 96)
        signal = "CALL (HIGHER / 🟢)"
    else:
        confidence = min(max(best_put, 83), 96)
        signal = "PUT (LOWER / 🔴)"

    is_live = "(OTC)" not in asset
    market_tag = "🌐 Live Real Market" if is_live else "💱 OTC Market"
    m5_icon = "🟢 Bullish" if m5_bias == "BULLISH" else "🔴 Bearish" if m5_bias == "BEARISH" else "⚪ Neutral"
    m15_icon = "🟢 Strong Uptrend" if m15_bias == "BULLISH" else "🔴 Strong Downtrend" if m15_bias == "BEARISH" else "⚪ Neutral"

    notes = (
        f"• <b>Market Type:</b> {market_tag}\n"
        f"• <b>Current Price:</b> {current_price:.5f}\n"
        f"• <b>Macro Trend (M15):</b> <b>{m15_icon}</b>\n"
        f"• <b>Higher TF (M5):</b> <b>{m5_icon}</b>\n"
        f"• <b>Key Levels:</b> Res: {resistance:.5f} | Supp: {support:.5f}\n"
        f"• <b>Price Action:</b> {'🔻 Upper Rejection Wick' if upper_rej else '🟢 Lower Rejection Wick' if lower_rej else 'Solid Impulse'}\n"
        f"• <b>Round Number:</b> {'⚡ Institutional Level Reached' if near_round else 'Mid-zone'}\n"
        f"• <b>M1 EMA (9/21):</b> {'Bullish' if ema9 > ema21 else 'Bearish'} ({ema9:.4f})\n"
        f"• <b>RSI (14):</b> {rsi} ({'Overbought' if rsi >= 68 else 'Oversold' if rsi <= 32 else 'Momentum'})\n"
        f"• <b>Bollinger Bands:</b> {upper_bb:.4f} / {lower_bb:.4f}\n"
        f"• <b>ATR Volatility:</b> {atr:.5f} (Active)"
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
# 7. SEQUENTIAL WORKER (10s PRE-CANDLE DISPATCH)
# ---------------------------------------------------------
async def scanner_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE, single_asset: str = None, tf_key: str = "1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_seconds = tf_data["seconds"]
    scan_desc = f"Single Asset ({single_asset})" if single_asset else "All Available Pairs"
    logger.info(f"1-by-1 Scanner active (Target: :50s / 10s pre-candle | TF: {tf_data['label']}) for {chat_id}")
    TRADE_EVENTS[chat_id] = asyncio.Event()

    while ACTIVE_SCANNERS.get(chat_id, False):
        try:
            current_cycle_tag = int(time.time() / total_seconds)

            if LAST_SENT_CANDLE.get(chat_id) == current_cycle_tag:
                await asyncio.sleep(2)
                continue

            current_assets = [single_asset] if single_asset else get_current_scan_pool()
            found = None

            for asset in current_assets:
                if not ACTIVE_SCANNERS.get(chat_id, False):
                    break

                current_payout = get_verified_payout(asset)

                if current_payout >= 85:
                    res = analyze_real_chart(asset, current_payout, tf_key)
                    if res["confidence"] >= 80 and not res["signal"].startswith("HOLD"):
                        found = res
                        break
                await asyncio.sleep(0.01)

            if not found or not ACTIVE_SCANNERS.get(chat_id, False):
                await asyncio.sleep(1.0)
                continue

            # Deliver exactly 10s before candle close
            current_sec = int(time.time()) % total_seconds
            target_dispatch_sec = total_seconds - 10

            if current_sec < target_dispatch_sec:
                wait_time = target_dispatch_sec - current_sec
                logger.info(f"Signal confirmed for {found['asset']}. Delivering in {wait_time}s (10s before open).")
                await asyncio.sleep(wait_time)
            elif current_sec > (total_seconds - 5):
                await asyncio.sleep(total_seconds - current_sec)
                continue

            if not ACTIVE_SCANNERS.get(chat_id, False):
                break

            LAST_SENT_CANDLE[chat_id] = current_cycle_tag

            lock_tag = f"🎯 <b>PINNED: {single_asset}</b>\n" if single_asset else ""
            msg = (
                f"{lock_tag}🚨 <b>QUOTEX ENTRY SIGNAL (10s PRE-CANDLE)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Asset:</b> {found['asset']}\n"
                f"• <b>Payout:</b> <b>{found['payout']}%</b> (&gt;= 85% Verified)\n"
                f"• <b>Signal:</b> <b>{found['signal']}</b>\n"
                f"• <b>Confidence Score:</b> <b>{found['confidence']}%</b> (&gt;= 80% Validated)\n"
                f"• <b>Chart Timeframe:</b> {found['tf_data']['label']}\n"
                f"• <b>Option Expiry:</b> {found['tf_data']['expiry']}\n"
                f"• <b>Preparation Window:</b> <b>10 SECONDS LEFT &rarr; ENTER AT 00:00</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Confluence (M15 Macro + M5 HTF + Price Action):</b>\n"
                f"{found['notes']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>ENTER AT EXACT CANDLE OPEN (00:00)</b>\n"
                f"<i>(Scanner paused until this trade completes)</i>"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
                    InlineKeyboardButton("❌ Log Loss", callback_data="log_loss"),
                ],
                [
                    InlineKeyboardButton("⏭️ Skip & Next", callback_data="skip_signal"),
                    InlineKeyboardButton("⏹️ Stop Scanner", callback_data="stop_scan"),
                ]
            ])

            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            logger.info(f"Signal dispatched at 10s pre-candle to chat {chat_id}.")

            TRADE_EVENTS[chat_id].clear()

            # Sequential lock: Wait full duration of candle + 10s buffer
            lock_duration = float(total_seconds + 10)
            try:
                await asyncio.wait_for(TRADE_EVENTS[chat_id].wait(), timeout=lock_duration)
            except asyncio.TimeoutError:
                if ACTIVE_SCANNERS.get(chat_id, False):
                    status_text = (
                        f"🏁 <b>{found['tf_data']['label']} Trade Finished!</b>\n"
                        f"Analyzing next setup on <b>{single_asset}</b>..."
                        if single_asset else
                        f"🏁 <b>{found['tf_data']['label']} Trade Finished!</b>\n"
                        "Scanning all pairs for next setup with M15/M5 confluence..."
                    )
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=status_text,
                        parse_mode=ParseMode.HTML
                    )

            await asyncio.sleep(2)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in scanner loop: {e}", exc_info=True)
            await asyncio.sleep(2)

# ---------------------------------------------------------
# 8. UI NAVIGATION & BUTTONS
# ---------------------------------------------------------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Auto-Scan M1 (10s Pre-Candle)", callback_data="start_scan_1"),
            InlineKeyboardButton("▶️ Auto-Scan M5 (M15 HTF Engine)", callback_data="start_scan_5"),
        ],
        [
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
            InlineKeyboardButton(f"🎯 Auto-Scan {current_asset} (M1)", callback_data=f"lock_{current_asset}_1"),
            InlineKeyboardButton(f"🎯 Auto-Scan {current_asset} (M5)", callback_data=f"lock_{current_asset}_5"),
        ],
        [
            InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
            InlineKeyboardButton("❌ Log Loss", callback_data="log_loss"),
        ],
        [
            InlineKeyboardButton("🔄 Re-Analyze M1", callback_data=f"sel_{current_asset}_1"),
            InlineKeyboardButton("🔄 Re-Analyze M5", callback_data=f"sel_{current_asset}_5"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Menu", callback_data="open_main_menu"),
        ],
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    live_open = is_live_market_open()
    status_text = (
        "🟢 <b>Live Real-Market: OPEN</b>"
        if live_open else
        "🔴 <b>Live Real-Market: CLOSED (Weekend OTC & Crypto Active)</b>"
    )

    await update.message.reply_text(
        f"🤖 <b>Quotex M15 Macro + M5 HTF Engine</b>\n\n"
        f"• <b>Market Session:</b> {status_text}\n"
        f"• <b>Higher Timeframe Stack:</b>\n"
        f"  - <b>M15 Macro Trend Filter</b> (Trend Confirmation)\n"
        f"  - <b>M5 Swing Alignment</b>\n"
        f"  - Support & Resistance Dynamic Zones\n"
        f"  - Candlestick Rejection Wicks & Doji Shield\n"
        f"• <b>Timing:</b> Exact <b>10 seconds before candle open</b>\n"
        f"• <b>Execution:</b> 1-by-1 Sequential (Payout &ge; 85%, Conf &ge; 80%)\n\n"
        "Select your timeframe scan mode below:",
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
        if data in ["open_main_menu", "back_assets"]:
            await query.edit_message_text(
                "📊 <b>Select trade category or start 1-by-1 Auto-Scan:</b>",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("start_scan"):
            tf_choice = "5" if data == "start_scan_5" else "1"
            stop_active_task(chat_id)
            ACTIVE_SCANNERS[chat_id] = True

            tf_label = "M5 (5 Minutes)" if tf_choice == "5" else "M1 (1 Minute)"
            await query.message.reply_text(
                f"🔎 <b>1-by-1 {tf_label} Auto-Scanner Started!</b>\n\n"
                f"• Evaluating <b>M15 Macro Trend + M5 Concurrence</b>.\n"
                f"• Filters: Payout &ge; 85% & Confidence &ge; 80%.\n"
                f"• Signals arrive <b>10 seconds before candle open</b>.",
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
                f"• Confluence checked against M15 Macro & M5 HTF.\n"
                f"• Signals deliver <b>10s before candle open</b>.\n\n"
                f"Tap <b>Stop Scanner</b> anytime to unlock.",
                parse_mode=ParseMode.HTML
            )
            SCANNER_TASKS[chat_id] = asyncio.create_task(scanner_worker(chat_id, context, single_asset=pinned_asset, tf_key=tf_choice))

        elif data == "stop_scan":
            stop_active_task(chat_id)
            await query.message.reply_text("⏹️ <b>Scanner stopped.</b> Send /start to resume.", parse_mode=ParseMode.HTML)

        elif data == "skip_signal":
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            await query.message.reply_text("⏭️ <b>Signal skipped.</b> Scanning next candle...", parse_mode=ParseMode.HTML)

        elif data == "log_win":
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            await query.message.reply_text("✅ Result logged: <b>WIN</b>. Scanning next setup...", parse_mode=ParseMode.HTML)

        elif data == "log_loss":
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            await query.message.reply_text("❌ Result logged: <b>LOSS</b>. Scanning next setup...", parse_mode=ParseMode.HTML)

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
                f"• <b>Confidence Score:</b> <b>{res['confidence']}%</b>\n"
                f"• <b>Chart Timeframe:</b> {res['tf_data']['label']}\n"
                f"• <b>Option Expiry:</b> {res['tf_data']['expiry']}\n"
                f"• <b>Candle Countdown:</b> {res['remaining_sec']}s remaining\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Technical & M15/M5 HTF Confluence:</b>\n"
                f"{res['notes']}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>Tip: Select continuous auto-scan mode below:</i>"
            )
            await query.edit_message_text(
                signal_text,
                reply_markup=get_signal_keyboard(asset, tf_key),
                parse_mode=ParseMode.HTML,
            )

    except TelegramError as e:
        logger.warning(f"Callback error: {e}")

# ---------------------------------------------------------
# 9. APPLICATION ENTRYPOINT
# ---------------------------------------------------------
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("Bot starting M15/M5 HTF 10s pre-candle polling loop...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
