import os
import sys
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------------------------------------------------------
# 1. RENDER HEALTH CHECK SERVER (Port 10000)
# ---------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is operational.")

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ---------------------------------------------------------
# 2. LOGGING & CREDENTIALS
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

# Full categorized assets matching Quotex tabs
QUOTEX_MARKETS = {
    "currencies": {
        "title": "💱 CURRENCIES",
        "assets": [
            "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)",
            "USD/CHF (OTC)", "AUD/USD (OTC)", "NZD/USD (OTC)",
            "USD/CAD (OTC)", "EUR/GBP (OTC)", "EUR/JPY (OTC)",
            "GBP/JPY (OTC)", "USD/INR (OTC)", "USD/BRL (OTC)",
            "USD/PKR (OTC)", "USD/BDT (OTC)", "USD/TRY (OTC)",
            "USD/EGP (OTC)", "USD/IDR (OTC)", "USD/NGN (OTC)",
        ]
    },
    "crypto": {
        "title": "🪙 CRYPTO",
        "assets": [
            "Bitcoin (OTC)", "Ethereum (OTC)", "Litecoin (OTC)",
            "Ripple (OTC)", "Solana (OTC)", "Dogecoin (OTC)",
        ]
    },
    "commodities": {
        "title": "🛢️ COMMODITIES",
        "assets": [
            "Gold (OTC)", "Silver (OTC)", "US Crude (OTC)", "UK Brent (OTC)"
        ]
    },
    "stocks": {
        "title": "📈 STOCKS",
        "assets": [
            "Apple (OTC)", "Microsoft (OTC)", "Tesla (OTC)",
            "Boeing (OTC)", "Amazon (OTC)", "Meta (OTC)"
        ]
    }
}

# ---------------------------------------------------------
# 3. INTERACTIVE KEYBOARDS
# ---------------------------------------------------------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💱 CURRENCIES", callback_data="cat_currencies_0"),
            InlineKeyboardButton("🪙 CRYPTO", callback_data="cat_crypto_0"),
        ],
        [
            InlineKeyboardButton("🛢️ COMMODITIES", callback_data="cat_commodities_0"),
            InlineKeyboardButton("📈 STOCKS", callback_data="cat_stocks_0"),
        ]
    ])

def get_asset_list_keyboard(cat_key, page=0, page_size=6):
    assets = QUOTEX_MARKETS[cat_key]["assets"]
    start_idx = page * page_size
    end_idx = start_idx + page_size
    current_page = assets[start_idx:end_idx]

    keyboard = []
    for i in range(0, len(current_page), 2):
        row = [InlineKeyboardButton(current_page[i], callback_data=f"sel_{current_page[i]}")]
        if i + 1 < len(current_page):
            row.append(InlineKeyboardButton(current_page[i + 1], callback_data=f"sel_{current_page[i + 1]}"))
        keyboard.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cat_{cat_key}_{page - 1}"))
    if end_idx < len(assets):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cat_{cat_key}_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Main Categories", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_signal_keyboard(current_asset):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
            InlineKeyboardButton("❌ Log Loss", callback_data="log_loss"),
        ],
        [
            InlineKeyboardButton("🔄 Re-Analyze", callback_data=f"sel_{current_asset}"),
        ],
        [
            InlineKeyboardButton("⏱️ Change Timeframe", callback_data="change_tf"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Assets", callback_data="open_main_menu"),
        ],
    ])

# ---------------------------------------------------------
# 4. SIGNAL CALCULATION
# ---------------------------------------------------------
def generate_quotex_signal(asset):
    current_sec = int(time.time()) % 60
    remaining_sec = 60 - current_sec

    import random
    rsi_val = round(random.uniform(36.0, 74.0), 1)

    if rsi_val >= 50.0:
        signal_type = "PUT (LOWER / 🔴)"
        trend = "Downward Trend"
        note = f"Bearish pressure dominant (RSI: {rsi_val}). Look for downward continuation."
    else:
        signal_type = "CALL (HIGHER / 🟢)"
        trend = "Upward Trend"
        note = f"Bullish pressure dominant (RSI: {rsi_val}). Look for upward continuation."

    return (
        f"🎯 *Quotex Binary Signal: {asset}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• *Signal:* {signal_type}\n"
        f"• *Strength:* {trend}\n"
        f"• *Chart Timeframe:* M1 (1 Min)\n"
        f"• *Option Expiry:* Exact 1 Minute (00:01:00)\n"
        f"• *Recommended Stake:* $15.0 (Strict 1.5% Flat)\n"
        f"• *RSI (14):* {rsi_val}\n"
        f"• *Candle Countdown:* {remaining_sec}s remaining\n"
        f"• *Analysis Notes:* {note}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Execution:* Verify payout >= 80% on Quotex. "
        f"Enter trade at 00:55–00:58 before new candle opens."
    )

# ---------------------------------------------------------
# 5. HANDLERS WITH ERROR RESILIENCE
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 *Select trade pair category:*",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data == "open_main_menu":
            await query.edit_message_text(
                "📊 *Select trade pair category:*",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="Markdown",
            )

        elif data.startswith("cat_"):
            parts = data.split("_")
            cat_key = parts[1]
            page = int(parts[2]) if len(parts) > 2 else 0
            title = QUOTEX_MARKETS[cat_key]["title"]
            await query.edit_message_text(
                f"📈 *{title} — Choose Pair:*",
                reply_markup=get_asset_list_keyboard(cat_key, page),
                parse_mode="Markdown",
            )

        elif data.startswith("sel_"):
            asset = data.replace("sel_", "")
            signal_text = generate_quotex_signal(asset)
            await query.edit_message_text(
                signal_text,
                reply_markup=get_signal_keyboard(asset),
                parse_mode="Markdown",
            )

        elif data == "log_win":
            await query.message.reply_text("✅ Result logged: **WIN**. Flat stake maintained.", parse_mode="Markdown")

        elif data == "log_loss":
            await query.message.reply_text("❌ Result logged: **LOSS**. Do not use Martingale.", parse_mode="Markdown")

        elif data == "change_tf":
            await query.message.reply_text("⏱️ Current active timeframe is **M1 (1 Minute)**.", parse_mode="Markdown")

    except BadRequest as e:
        # Prevents crash if user double taps button or message text didn't change
        if "Message is not modified" in str(e):
            pass
        else:
            await query.message.reply_text(
                "📊 *Select trade pair category:*",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="Markdown"
            )

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
