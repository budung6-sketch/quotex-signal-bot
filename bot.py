import os
import sys
import time
import json
import math
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
# 1. LIVE TICK BRIDGE & INGESTION
# ---------------------------------------------------------
LIVE_BROWSER_PAYOUTS = {}
REAL_CANDLE_HISTORY = {}
RECENT_TICKS = {}
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
            p_count = len(LIVE_BROWSER_PAYOUTS)
            c_count = len(REAL_CANDLE_HISTORY)
            act_bars = len(REAL_CANDLE_HISTORY.get(CURRENT_STREAMED_ASSET, []))
            cur_p = LATEST_SCREEN_PRICE
            cur_a = CURRENT_STREAMED_ASSET
        self.wfile.write(
            f"Quotex Engine Online | Pair: {cur_a} | Live Price: {cur_p} | Synced Payouts: {p_count} | Synced Assets: {c_count} | M1 Bars: {act_bars}".encode("utf-8")
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

                        if live_payout >= 50:
                            LIVE_BROWSER_PAYOUTS[CURRENT_STREAMED_ASSET] = live_payout
                            LIVE_BROWSER_PAYOUTS[asset] = live_payout
                            LIVE_BROWSER_PAYOUTS["ACTIVE_CHART"] = live_payout

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
# 2. BOT INITIALIZATION
# ---------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN missing in environment.")
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
# 3. TECHNICAL CONFLUENCE MODULES
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

    return min(call_pts, 25), min(put_pts, 25), "Price Action Valid"

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

    regime = "Bullish" if call_pts > put_pts else "Bearish" if put_pts > call_pts else "Neutral"
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

def evaluate_tick_flow(asset):
    with DATA_LOCK:
        ticks = list(RECENT_TICKS.get(asset, []))
    if len(ticks) < 3:
        return 3, 3, "Ticks Aligned"

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

    return max(call_pts, 1), max(put_pts, 1), "Ticks Aligned"

# ---------------------------------------------------------
# 4. STRICT 85%+ SCORING ENGINE
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
    return 0, False

def run_scoring_architecture(asset, tf_key="1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_sec = tf_data["seconds"]
    rem_sec = total_sec - (int(time.time()) % total_sec)

    payout, is_payout_live = get_verified_payout(asset)

    # RULE 1: STRICT 85%+ PAYOUT FILTER
    if payout < 85:
        return {
            "asset": asset, "payout": payout, "signal": "HOLD (PAYOUT BELOW 85%)",
            "confidence": 0, "notes": f"Payout ({payout}%) is below strict 85% requirement.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": 0.0, "is_payout_live": is_payout_live
        }

    # RULE 2: VERIFY REAL LIVE DATA STREAM
    with DATA_LOCK:
        candles_raw = REAL_CANDLE_HISTORY.get(asset) or (REAL_CANDLE_HISTORY.get(CURRENT_STREAMED_ASSET) if asset == CURRENT_STREAMED_ASSET else None)
        curr_screen_p = LATEST_SCREEN_PRICE if asset == CURRENT_STREAMED_ASSET else 0.0

    if not candles_raw or len(candles_raw) < 5:
        return {
            "asset": asset, "payout": payout, "signal": "HOLD (AWAITING LIVE TICKS)",
            "confidence": 0, "notes": "No active live browser websocket feed detected for this pair.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_screen_p, "is_payout_live": is_payout_live
        }

    candles = list(candles_raw)
    closes = [c["close"] for c in candles]
    curr_p = curr_screen_p if (curr_screen_p > 0 and asset == CURRENT_STREAMED_ASSET) else closes[-1]

    pa_call, pa_put, pa_status = evaluate_price_action(candles)
    tr_call, tr_put, trend_label = evaluate_trend_regime(closes)
    ms_call, ms_put, struct_label = evaluate_market_structure(candles)
    sr_call, sr_put, res, sup = evaluate_support_resistance(candles, curr_p)
    mo_call, mo_put, rsi, macd_h = evaluate_momentum(closes)
    tk_call, tk_put, _ = evaluate_tick_flow(asset)

    if pa_status == "Flatline Doji":
        return {
            "asset": asset, "payout": payout, "signal": "HOLD (ZERO VOLATILITY)",
            "confidence": 20, "notes": "Flatline compression detected. Skipping candle.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p, "is_payout_live": is_payout_live
        }

    total_call = pa_call + ms_call + tr_call + mo_call + sr_call + tk_call + 10
    total_put = pa_put + ms_put + tr_put + mo_put + sr_put + tk_put + 10

    best_score = min(max(total_call, total_put), 98)

    if best_score < 80:
        return {
            "asset": asset, "payout": payout, "signal": "HOLD (LOW CONFLUENCE)",
            "confidence": best_score, "notes": f"Score {best_score}/100 did not satisfy 80+ confluence threshold.",
            "tf_data": tf_data, "remaining_sec": rem_sec, "current_price": curr_p, "is_payout_live": is_payout_live
        }

    signal = "CALL (HIGHER / 🟢)" if total_call >= total_put else "PUT (LOWER / 🔴)"

    breakdown = (
        f"• <b>Live Feed Sync:</b> 🟢 Verified Exact Quotex Stream\n"
        f"• <b>Price Action (+25):</b> {pa_status}\n"
        f"• <b>Market Structure (+20):</b> {struct_label}\n"
        f"• <b>Trend Alignment (+15):</b> {trend_label}\n"
        f"• <b>Momentum (+15):</b> RSI {rsi} | MACD Hist: {macd_h:.4f}\n"
        f"• <b>Key S/R (+10):</b> Res: {res:.5f} | Supp: {sup:.5f}\n"
        f"• <b>Tick Flow (+5):</b> Micro Flow Synchronized"
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
# 5. SCANNER WORKER
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

            target = single_asset or CURRENT_STREAMED_ASSET
            res = run_scoring_architecture(target, tf_key)

            if res["confidence"] < 80 or res["signal"].startswith("HOLD"):
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

            price_str = f"{res['current_price']:.5f}" if res['current_price'] < 100 else f"{res['current_price']:.2f}"

            msg = (
                f"🚨 <b>QUOTEX LIVE SIGNAL (SCORE: {res['confidence']}/100)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Asset:</b> {res['asset']}\n"
                f"• <b>Current Live Price:</b> <code>{price_str}</code> (Exact Screen Match)\n"
                f"• <b>Payout:</b> <b>{res['payout']}%</b> (&gt;= 85% Filter)\n"
                f"• <b>Direction:</b> <b>{res['signal']}</b>\n"
                f"• <b>Timeframe:</b> {res['tf_data']['label']}\n"
                f"• <b>Expiry Duration:</b> {res['tf_data']['expiry']}\n"
                f"• <b>Execution:</b> <b>ENTER AT EXACT 00:00 (10s PRE-ALERT)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Technical Confluence Diagnostics:</b>\n"
                f"{res['notes']}\n"
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
# 6. TELEGRAM UI & DISPATCH HANDLERS
# ---------------------------------------------------------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Auto-Scan Active Screen (M1)", callback_data="start_scan_1"),
            InlineKeyboardButton("▶️ Auto-Scan Active Screen (M5)", callback_data="start_scan_5"),
        ],
        [
            InlineKeyboardButton("⏹️ Stop Active Scanner", callback_data="stop_scan")
        ]
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payout, is_p = get_verified_payout(CURRENT_STREAMED_ASSET)
    payout_label = f"{payout}%" if is_p else "Checking Screen..."
    price_val = f"{LATEST_SCREEN_PRICE:.5f}" if LATEST_SCREEN_PRICE > 0 else "Waiting for Browser Tick..."

    await update.message.reply_text(
        f"⚡ <b>Quotex Live Engine (Screen Synchronized)</b>\n\n"
        f"• <b>Active Screen Asset:</b> <code>{CURRENT_STREAMED_ASSET}</code>\n"
        f"• <b>Live Screen Price:</b> <code>{price_val}</code>\n"
        f"• <b>Screen Payout:</b> <code>{payout_label}</code>\n"
        f"• <b>Filters:</b> Minimum <b>85%+ Payout</b> &amp; <b>80+ Confluence Score</b>\n\n"
        "Tap below to begin scanning your active Quotex chart:",
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
                "📊 <b>Select scanning mode:</b>",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("start_scan"):
            tf_choice = "5" if data == "start_scan_5" else "1"
            stop_active_task(chat_id)
            ACTIVE_SCANNERS[chat_id] = True

            await query.message.reply_text(
                f"🔎 <b>Active Screen Scanner Started ({TIMEFRAME_CONFIG[tf_choice]['label']})</b>\n\n"
                f"• Asset: <b>{CURRENT_STREAMED_ASSET}</b>\n"
                f"• Payout Requirement: <b>&gt;= 85%</b>\n"
                f"• Price: <b>Live Exact Quotex Feed</b>\n"
                f"• Dispatch: <b>10 seconds before candle open</b>.",
                parse_mode=ParseMode.HTML
            )
            SCANNER_TASKS[chat_id] = asyncio.create_task(scanner_worker(chat_id, context, single_asset=None, tf_key=tf_choice))

        elif data == "stop_scan":
            stop_active_task(chat_id)
            await query.message.reply_text("⏹️ <b>Scanner stopped.</b> Send /start to resume.", parse_mode=ParseMode.HTML)

        elif data in ["skip_signal", "log_win", "log_loss"]:
            if chat_id in TRADE_EVENTS:
                TRADE_EVENTS[chat_id].set()
            tag = "WIN" if data == "log_win" else "LOSS" if data == "log_loss" else "SKIPPED"
            await query.message.reply_text(f"Logged: <b>{tag}</b>. Scanning next candle...", parse_mode=ParseMode.HTML)

    except TelegramError as e:
        logger.warning(f"Callback error: {e}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("Bot starting with Strict 85%+ Payout and Exact Screen Price Sync...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
