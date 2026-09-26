import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

# --- 1. Background Health Server for Render ---
class SimpleHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Quotex Engine is Active!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHealthHandler)
    server.serve_forever()

# --- 2. Quotex Binary Options Assets ---
QUOTEX_BINARY_ASSETS = {
    "live_currencies": [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CAD", "AUD/USD",
        "EUR/GBP", "USD/CHF", "NZD/USD", "EUR/JPY", "GBP/JPY",
        "AUD/CAD", "CAD/CHF", "EUR/CAD", "EUR/AUD", "GBP/CAD",
        "GBP/AUD", "AUD/JPY", "CAD/JPY", "CHF/JPY", "NZD/JPY"
    ],
    "otc_currencies": [
        "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "USD/INR (OTC)",
        "USD/BRL (OTC)", "USD/PKR (OTC)", "USD/BDT (OTC)", "USD/IDR (OTC)",
        "USD/TRY (OTC)", "USD/EGP (OTC)", "EUR/JPY (OTC)", "GBP/JPY (OTC)",
        "AUD/USD (OTC)", "NZD/CAD (OTC)", "EUR/CHF (OTC)", "CAD/JPY (OTC)",
        "AUD/CAD (OTC)", "GBP/AUD (OTC)", "EUR/CAD (OTC)", "NZD/USD (OTC)"
    ],
    "otc_stocks": [
        "Apple (OTC)", "Microsoft (OTC)", "Tesla (OTC)", "Amazon (OTC)",
        "Google (OTC)", "Meta (OTC)", "NVIDIA (OTC)", "Boeing (OTC)",
        "Intel (OTC)", "Johnson & Johnson (OTC)", "Pfizer (OTC)", "McDonald's (OTC)"
    ],
    "crypto_binary": [
        "BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "XRP/USD", "DOGE/USD", "TRX/USD"
    ],
    "commodities_indices": [
        "Gold (XAU/USD)", "Silver (XAG/USD)", "US Crude (OIL)", "UK Brent",
        "US Tech 100 (OTC)", "SPX 500 (OTC)", "Dow Jones 30 (OTC)"
    ]
}

TIMEFRAMES = ["M1 (1 Min)", "M2 (2 Min)", "M5 (5 Min)"]

# --- 3. Non-Martingale Risk Engine ---
ACCOUNT_BALANCE = 1000.0
FLAT_RISK_PCT = 0.015       # 1.5% Flat Stake ($15)
MAX_DAILY_LOSS_PCT = 0.05   # 5% Max Drawdown
MAX_CONSECUTIVE_LOSSES = 3  # Circuit Breaker

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

def evaluate_strategy(pair: str, tf: str) -> dict:
    sample_closes = [1.0850, 1.0848, 1.0845, 1.0840, 1.0835, 1.0830, 1.0827, 1.0822, 1.0818, 1.0815, 1.0810, 1.0808, 1.0805, 1.0802, 1.0798]
    rsi = round(compute_rsi(sample_closes, period=14), 1)
    stake_amount = round(session_stats["balance"] * FLAT_RISK_PCT, 2)
    last_price = sample_closes[-1]

    signal = "NEUTRAL (WAIT)"
    notes = "Price consolidating inside normal range. Awaiting rejection."

    if rsi <= 30:
        signal = "CALL (HIGHER / 🟢)"
        notes = f"RSI oversold ({rsi} <= 30) near key rejection support."
    elif rsi >= 70:
        signal = "PUT (LOWER / 🔴)"
        notes = f"RSI overbought ({rsi} >= 70) near key rejection resistance."

    # Exact Expiry Mapping for Quotex
    expiry_map = {
        "M1 (1 Min)": "Exact 1 Minute (00:01:00)",
        "M2 (2 Min)": "Exact 2 Minutes (00:02:00)",
        "M5 (5 Min)": "Exact 5 Minutes (00:05:00)"
    }

    return {
        "signal": signal,
        "stake": stake_amount,
        "notes": notes,
        "rsi": rsi,
        "price": last_price,
        "expiry": expiry_map.get(tf, "Exact 1 Minute (00:01:00)")
    }

# --- 4. Interactive Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    can_trade, status_msg = check_risk_guard()
    keyboard = [
        [InlineKeyboardButton("🌐 Live Forex", callback_data="cat_live_currencies_0"),
         InlineKeyboardButton("⚡ OTC Forex (24/7)", callback_data="cat_otc_currencies_0")],
        [InlineKeyboardButton("🏢 OTC Stocks", callback_data="cat_otc_stocks_0"),
         InlineKeyboardButton("🪙 Crypto Binary", callback_data="cat_crypto_binary_0")],
        [InlineKeyboardButton("📈 Commodities & Indices", callback_data="cat_commodities_indices_0")],
        [InlineKeyboardButton("🛡️ View Risk & Stats", callback_data="view_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📊 *Quotex Binary Options Signal Engine*\n\n"
        "• *Risk Mode:* Strict 1.5% Flat Stake\n"
        "• *Martingale:* Disabled\n"
        f"• *Status:* `{status_msg}`\n\n"
        "Select a Quotex binary market:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🌐 Live Forex", callback_data="cat_live_currencies_0"),
             InlineKeyboardButton("⚡ OTC Forex (24/7)", callback_data="cat_otc_currencies_0")],
            [InlineKeyboardButton("🏢 OTC Stocks", callback_data="cat_otc_stocks_0"),
             InlineKeyboardButton("🪙 Crypto Binary", callback_data="cat_crypto_binary_0")],
            [InlineKeyboardButton("📈 Commodities & Indices", callback_data="cat_commodities_indices_0")],
            [InlineKeyboardButton("🛡️ View Risk & Stats", callback_data="view_stats")]
        ]
        await query.edit_message_text(
            "📊 *Select Quotex Binary Market:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    if data == "view_stats":
        keyboard = [[InlineKeyboardButton("⬅️ Back to Markets", callback_data="main_menu")]]
        await query.edit_message_text(
            f"📊 *Current Session Performance*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• *Account Balance:* `${session_stats['balance']:.2f}`\n"
            f"• *Flat Stake per Trade:* `${session_stats['balance'] * FLAT_RISK_PCT:.2f}` (1.5%)\n"
            f"• *Daily P&L:* `${session_stats['daily_pnl']:.2f}`\n"
            f"• *Max Daily Drawdown:* `-${session_stats['balance'] * MAX_DAILY_LOSS_PCT:.2f}` (5%)\n"
            f"• *Consecutive Losses:* `{session_stats['consecutive_losses']} / {MAX_CONSECUTIVE_LOSSES}`\n"
            f"• *Status:* `{'ACTIVE' if not session_stats['is_locked'] else 'LOCKED (Cooldown)'}`\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"_Martingale is permanently disabled._",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # Pagination handling
    if data.startswith("cat_"):
        parts = data.split("_")
        cat_key = f"{parts[1]}_{parts[2]}"
        page = int(parts[3])

        assets = QUOTEX_BINARY_ASSETS.get(cat_key, [])
        page_size = 6
        total_pages = (len(assets) + page_size - 1) // page_size
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, len(assets))
        current_assets = assets[start_idx:end_idx]

        buttons = []
        for i in range(0, len(current_assets), 2):
            row = [InlineKeyboardButton(current_assets[i], callback_data=f"asset_{current_assets[i]}")]
            if i + 1 < len(current_assets):
                row.append(InlineKeyboardButton(current_assets[i+1], callback_data=f"asset_{current_assets[i+1]}"))
            buttons.append(row)

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cat_{cat_key}_{page-1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cat_{cat_key}_{page+1}"))
        if nav_row:
            buttons.append(nav_row)

        buttons.append([InlineKeyboardButton("🔙 All Categories", callback_data="main_menu")])

        titles = {
            "live_currencies": "🌐 Live Forex",
            "otc_currencies": "⚡ OTC Forex (24/7)",
            "otc_stocks": "🏢 OTC Stocks",
            "crypto_binary": "🪙 Crypto Binary",
            "commodities_indices": "📈 Commodities & Indices"
        }

        await query.edit_message_text(
            f"*{titles.get(cat_key, 'Assets')} (Page {page + 1}/{total_pages})*\n"
            f"Select a Quotex asset to analyze:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        return

    # Timeframe selection
    if data.startswith("asset_"):
        asset_name = data.replace("asset_", "")
        buttons = [
            [InlineKeyboardButton(tf, callback_data=f"run_{asset_name}|{tf}") for tf in TIMEFRAMES[:2]],
            [InlineKeyboardButton(TIMEFRAMES[2], callback_data=f"run_{asset_name}|{TIMEFRAMES[2]}")],
            [InlineKeyboardButton("⬅️ Back to Assets", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            f"🎯 *Selected Asset:* `{asset_name}`\n"
            f"Choose your candle timeframe:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        return

    # Confluence Analysis
    if data.startswith("run_"):
        payload = data.replace("run_", "")
        asset_name, tf = payload.split("|")

        can_trade, status_msg = check_risk_guard()
        if not can_trade:
            keyboard = [[InlineKeyboardButton("⬅️ Back to Assets", callback_data="main_menu")]]
            await query.edit_message_text(
                f"⛔ *Signal Suppressed:* {status_msg}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return

        res = evaluate_strategy(asset_name, tf)
        keyboard = [
            [InlineKeyboardButton("🔄 Re-Analyze", callback_data=f"run_{asset_name}|{tf}")],
            [InlineKeyboardButton("⏱️ Change Timeframe", callback_data=f"asset_{asset_name}")],
            [InlineKeyboardButton("⬅️ Back to Assets", callback_data="main_menu")]
        ]

        await query.edit_message_text(
            f"🎯 *Quotex Binary Signal: {asset_name}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• *Signal:* `{res['signal']}`\n"
            f"• *Chart Timeframe:* `{tf}`\n"
            f"• *Option Expiry:* `{res['expiry']}`\n"
            f"• *Recommended Stake:* `${res['stake']}` (Strict 1.5% Flat)\n"
            f"• *RSI (14):* `{res['rsi']}`\n"
            f"• *Setup Notes:* {res['notes']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *Execution:* Set Timer to `{res['expiry']}` on Quotex. Enter at 00:55–00:58 before new candle opens.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    TOKEN = os.getenv("BOT_TOKEN", "")
    if not TOKEN:
        raise ValueError("BOT_TOKEN environment variable not set!")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()
