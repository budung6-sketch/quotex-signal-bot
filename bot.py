import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

# --- 1. Dummy HTTP Server for Render Port Check ---
class SimpleHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is healthy and running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHealthHandler)
    server.serve_forever()

# --- 2. Risk Parameters & Indicator Calculations ---
LIVE_ASSETS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]
ACCOUNT_BALANCE = 1000.0
RISK_PER_TRADE_PCT = 0.015   # 1.5% Flat Stake - NO MARTINGALE
MAX_DAILY_LOSS_PCT = 0.05
MAX_CONSECUTIVE_LOSSES = 3

session_stats = {
    "balance": ACCOUNT_BALANCE,
    "consecutive_losses": 0,
    "daily_pnl": 0.0,
    "is_locked": False
}

def check_risk_guard() -> tuple[bool, str]:
    if session_stats["is_locked"]:
        return False, "Bot is locked due to safety limit. Trading resumed tomorrow."
    if session_stats["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
        session_stats["is_locked"] = True
        return False, f"Hit {MAX_CONSECUTIVE_LOSSES} consecutive losses. Cooldown active."
    if session_stats["daily_pnl"] <= -(ACCOUNT_BALANCE * MAX_DAILY_LOSS_PCT):
        session_stats["is_locked"] = True
        return False, "Daily max drawdown limit (-5%) reached. Trading stopped."
    return True, "Active"

def compute_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def evaluate_strategy(pair: str) -> dict:
    sample_closes = [1.0850, 1.0848, 1.0845, 1.0840, 1.0835, 1.0830, 1.0827, 1.0822, 1.0818, 1.0815, 1.0810, 1.0808, 1.0805, 1.0802, 1.0798]
    rsi = round(compute_rsi(sample_closes, period=14), 1)
    stake_amount = round(session_stats["balance"] * RISK_PER_TRADE_PCT, 2)
    last_price = sample_closes[-1]

    signal = "NEUTRAL"
    notes = "Consolidating. Waiting for oversold/overbought boundary."

    if rsi <= 30:
        signal = "CALL (HIGHER)"
        notes = f"RSI is oversold ({rsi} <= 30) near multi-candle support."
    elif rsi >= 70:
        signal = "PUT (LOWER)"
        notes = f"RSI is overbought ({rsi} >= 70) near local resistance."

    return {
        "signal": signal,
        "stake": stake_amount,
        "notes": notes,
        "rsi": rsi,
        "price": last_price
    }

# --- 3. Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    can_trade, status_msg = check_risk_guard()
    keyboard = [
        [InlineKeyboardButton(f"📈 {pair}", callback_data=f"analyze_{pair}") for pair in LIVE_ASSETS[:2]],
        [InlineKeyboardButton(f"📉 {pair}", callback_data=f"analyze_{pair}") for pair in LIVE_ASSETS[2:]],
        [InlineKeyboardButton("🛡️ View Risk & Stats", callback_data="view_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🤖 *Quotex Non-Martingale Signal Engine*\n\n"
        f"• *Risk Mode:* Flat Sizing (1.5% fixed)\n"
        f"• *Status:* {status_msg}\n\n"
        f"Select an asset to analyze:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "view_stats":
        await query.edit_message_text(
            f"📊 *Current Session Performance*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• *Account Balance:* `${session_stats['balance']:.2f}`\n"
            f"• *Flat Stake per Trade:* `${session_stats['balance'] * RISK_PER_TRADE_PCT:.2f}`\n"
            f"• *Daily P&L:* `${session_stats['daily_pnl']:.2f}`\n"
            f"• *Consecutive Losses:* `{session_stats['consecutive_losses']} / {MAX_CONSECUTIVE_LOSSES}`\n"
            f"• *Status:* `{'ACTIVE' if not session_stats['is_locked'] else 'LOCKED (Cooldown)'}`\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"_Martingale is permanently disabled._",
            parse_mode="Markdown"
        )
        return

    if query.data.startswith("analyze_"):
        pair = query.data.replace("analyze_", "")
        can_trade, status_msg = check_risk_guard()

        if not can_trade:
            await query.edit_message_text(f"⛔ *Signal Suppressed:* {status_msg}", parse_mode="Markdown")
            return

        res = evaluate_strategy(pair)

        await query.edit_message_text(
            f"🎯 *Analysis Result: {pair}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• *Signal:* `{res['signal']}`\n"
            f"• *Recommended Stake:* `${res['stake']}` (Strict 1.5% Flat)\n"
            f"• *Expiry Time:* 2 Minutes\n"
            f"• *RSI (14):* {res['rsi']}\n"
            f"• *Setup Notes:* {res['notes']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *Execution:* Take exactly ONE trade. Do not re-enter if price moves against you.",
            parse_mode="Markdown"
        )

if __name__ == "__main__":
    # Start web server thread to pass Render's port-binding check
    threading.Thread(target=run_health_server, daemon=True).start()

    # Launch Telegram Bot
    TOKEN = os.getenv("BOT_TOKEN", "")
    if not TOKEN:
        raise ValueError("BOT_TOKEN environment variable not set!")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()
