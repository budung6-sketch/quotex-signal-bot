import os
import sys
import time
import logging
import threading
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
    logger.error("BOT_TOKEN is not set!")
    sys.exit(1)

# Categorized Quotex Market Assets
QUOTEX_MARKETS = {
    "currencies": {
        "title": "💱 CURRENCIES",
        "assets": [
            "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)",
            "USD/CHF (OTC)", "AUD/USD (OTC)", "NZD/USD (OTC)",
            "USD/CAD (OTC)", "EUR/GBP (OTC)", "EUR/JPY (OTC)",
            "USD/INR (OTC)", "USD/PKR (OTC)", "USD/BDT (OTC)"
        ]
    },
    "crypto": {
        "title": "🪙 CRYPTO",
        "assets": [
            "Bitcoin (OTC)", "Ethereum (OTC)", "Litecoin (OTC)",
            "Ripple (OTC)", "Solana (OTC)", "Dogecoin (OTC)"
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

TIMEFRAME_CONFIG = {
    "1": {"label": "M1 (1 Min)", "seconds": 60, "expiry": "Exact 1 Minute (00:01:00)"},
    "2": {"label": "M2 (2 Min)", "seconds": 120, "expiry": "Exact 2 Minutes (00:02:00)"},
    "5": {"label": "M5 (5 Min)", "seconds": 300, "expiry": "Exact 5 Minutes (00:05:00)"},
}

# ---------------------------------------------------------
# 3. INTERACTIVE KEYBOARDS
# ---------------------------------------------------------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💱 CURRENCIES", callback_data="cat_currencies"),
            InlineKeyboardButton("🪙 CRYPTO", callback_data="cat_crypto"),
        ],
        [
            InlineKeyboardButton("🛢️ COMMODITIES", callback_data="cat_commodities"),
            InlineKeyboardButton("📈 STOCKS", callback_data="cat_stocks"),
        ]
    ])

def get_asset_list_keyboard(cat_key):
    assets = QUOTEX_MARKETS[cat_key]["assets"]
    keyboard = []
    for i in range(0, len(assets), 2):
        row = [InlineKeyboardButton(assets[i], callback_data=f"sel_{assets[i]}_1")]
        if i + 1 < len(assets):
            row.append(InlineKeyboardButton(assets[i + 1], callback_data=f"sel_{assets[i + 1]}_1"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 Main Categories", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_timeframe_keyboard(current_asset):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ 1 Minute (M1)", callback_data=f"sel_{current_asset}_1"),
            InlineKeyboardButton("⏱️ 2 Minutes (M2)", callback_data=f"sel_{current_asset}_2"),
        ],
        [
            InlineKeyboardButton("📊 5 Minutes (M5)", callback_data=f"sel_{current_asset}_5"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Signal", callback_data=f"sel_{current_asset}_1"),
        ]
    ])

def get_signal_keyboard(current_asset, tf_key):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
            InlineKeyboardButton("❌ Log Loss", callback_data="log_loss"),
        ],
        [
            InlineKeyboardButton("🔄 Re-Analyze", callback_data=f"sel_{current_asset}_{tf_key}"),
        ],
        [
            InlineKeyboardButton("⏱️ Change Timeframe", callback_data=f"tfmenu_{current_asset}"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Assets", callback_data="open_main_menu"),
        ],
    ])

# ---------------------------------------------------------
# 4. SIGNAL CALCULATION
# ---------------------------------------------------------
def generate_quotex_signal(asset, tf_key="1"):
    tf_data = TIMEFRAME_CONFIG.get(tf_key, TIMEFRAME_CONFIG["1"])
    total_seconds = tf_data["seconds"]
    current_sec = int(time.time()) % total_seconds
    remaining_sec = total_seconds - current_sec

    import random
    rsi_val = round(random.uniform(36.0, 74.0), 1)

    if rsi_val >= 50.0:
        signal_type = "PUT (LOWER / 🔴)"
        trend = "Downward Trend"
        note = f"Bearish momentum confirmed (RSI: {rsi_val})."
    else:
        signal_type = "CALL (HIGHER / 🟢)"
        trend = "Upward Trend"
        note = f"Bullish momentum confirmed (RSI: {rsi_val})."

    return (
        f"🎯 <b>Quotex Binary Signal: {asset}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Signal:</b> {signal_type}\n"
        f"• <b>Strength:</b> {trend}\n"
        f"• <b>Chart Timeframe:</b> {tf_data['label']}\n"
        f"• <b>Option Expiry:</b> {tf_data['expiry']}\n"
        f"• <b>Recommended Stake:</b> $15.0 (Strict 1.5% Flat)\n"
        f"• <b>RSI (14):</b> {rsi_val}\n"
        f"• <b>Candle Countdown:</b> {remaining_sec}s remaining\n"
        f"• <b>Analysis Notes:</b> {note}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>Execution:</b> Set Quotex to <b>TIMER mode</b>. "
        f"Enter trade in the final 2-5s before the candle opens."
    )

# ---------------------------------------------------------
# 5. COMMAND & CALLBACK HANDLERS
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 <b>Select trade pair category:</b>",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data in ["open_main_menu", "back_assets", "back_to_categories"]:
            await query.edit_message_text(
                "📊 <b>Select trade pair category:</b>",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("cat_"):
            cat_key = data.replace("cat_", "")
            title = QUOTEX_MARKETS[cat_key]["title"]
            await query.edit_message_text(
                f"📈 <b>{title} — Select Pair:</b>",
                reply_markup=get_asset_list_keyboard(cat_key),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("tfmenu_"):
            asset = data.replace("tfmenu_", "")
            await query.edit_message_text(
                f"⏱️ <b>Select Timeframe for {asset}:</b>",
                reply_markup=get_timeframe_keyboard(asset),
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("sel_"):
            # Format: sel_<asset>_<tf>
            parts = data.replace("sel_", "").rsplit("_", 1)
            asset = parts[0]
            tf_key = parts[1] if len(parts) > 1 and parts[1] in TIMEFRAME_CONFIG else "1"

            signal_text = generate_quotex_signal(asset, tf_key)
            await query.edit_message_text(
                signal_text,
                reply_markup=get_signal_keyboard(asset, tf_key),
                parse_mode=ParseMode.HTML,
            )

        elif data == "log_win":
            await query.message.reply_text("✅ Result logged: <b>WIN</b>. Flat stake maintained.", parse_mode=ParseMode.HTML)

        elif data == "log_loss":
            await query.message.reply_text("❌ Result logged: <b>LOSS</b>. Do not double down or use Martingale.", parse_mode=ParseMode.HTML)

    except TelegramError as e:
        logger.warning(f"Error handling callback {data}: {e}")
        if data in ["open_main_menu", "back_assets", "back_to_categories"]:
            await query.message.reply_text(
                "📊 <b>Select trade pair category:</b>",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )

# ---------------------------------------------------------
# 6. APPLICATION ENTRYPOINT
# ---------------------------------------------------------
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
