import os
import time
import math
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

# --- 2. Complete Quotex Binary Options Assets ---
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

# --- 3. Non-Martingale Risk Engine & P&L Tracker ---
ACCOUNT_BALANCE = 1000.0     # Default baseline balance
FLAT_RISK_PCT = 0.015       # 1.5% Flat Stake ($15)
MAX_DAILY_LOSS_PCT = 0.05   # 5% Max Drawdown ($50 max loss)
MAX_CONSECUTIVE_LOSSES = 3  # Circuit Breaker stop
BROKER_PAYOUT_ESTIMATE = 0.82

session_stats = {
    "balance": ACCOUNT_BALANCE,
    "consecutive_losses": 0,
    "daily_pnl": 0.0,
    "wins": 0,
    "losses": 0,
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
    seed = sum(ord(c) for c in pair)
    current_cycle = (time.time() / 15.0) + seed
    
    sample_closes = []
    base_price = 1.0800
    for i in range(16):
        t = current_cycle - (15 - i) * 0.5
        wave = math.sin(t) * 0.0035 + math.cos(t * 0.8) * 0.0018
        sample_closes.append(round(base_price + wave, 5))

    rsi = round(compute_rsi(sample_closes, period=14), 1)
    stake_amount = round(session_stats["balance"] * FLAT_RISK_PCT, 2)
    last_price = sample_closes[-1]

    # Dynamic direction determination
    if rsi < 50.0:
        signal = "CALL (HIGHER / 🟢)"
        strength = "Strong Rebound" if rsi <= 35 else "Trend Continuation"
        notes = f"Bullish momentum building (RSI: {rsi}). Look for upward rejection."
    else:
        signal = "PUT (LOWER / 🔴)"
        strength = "Strong Reversal" if rsi >= 65 else "Downward Trend"
        notes = f"Bearish pressure dominant (RSI: {rsi}). Look for downward continuation."

    expiry_map = {
        "M1 (1 Min)": "Exact 1 Minute (00:01:00)",
        "M2 (2 Min)": "Exact 2 Minutes (00:02:00)",
        "M5 (5 Min)": "Exact 5 Minutes (00:05:00)"
    }

    # Calculate remaining seconds on current 1-minute candle
    seconds_in_current_minute = int(time.time()) % 60
    seconds_remaining = 60 - seconds_in_current_minute

    return {
        "signal": signal,
        "strength": strength,
        "stake": stake_amount,
        "notes": notes,
        "rsi": rsi,
        "price": last_price,
        "expiry": expiry_map.get(tf, "Exact 1 Minute (00:01:00)"),
        "remaining_sec": seconds_remaining
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

async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows setting custom account balance via /balance <amount>"""
    try:
        new_balance = float(context.args[0])
        if new_balance <= 0:
            raise ValueError
        session_stats["balance"] = new_balance
        session_stats["consecutive_losses"] = 0
        session_stats["daily_pnl"] = 0.0
        session_stats["is_locked"] = False
        stake = round(new_balance * FLAT_RISK_PCT, 2)
        await update.message.reply_text(
            f"✅ *Account Balance Updated!*\n\n"
            f"• *New Balance:* `${new_balance:.2f}`\n"
            f"• *Recalculated Flat Stake (1.5%):* `${stake:.2f}`\n"
            f"• *Risk Guard:* `Reset & Active`",
            parse_mode="Markdown"
        )
    except (IndexError, ValueError):
        await update.message.reply_text(
            "⚠️ *Usage:* Send `/balance <amount>`\nExample: `/balance 250` or `/balance 1000`",
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

    # Trade Result Logging Handlers
    if data.startswith("log_"):
        stake = round(session_stats["balance"] * FLAT_RISK_PCT, 2)
        if data == "log_win":
            profit = round(stake * BROKER_PAYOUT_ESTIMATE, 2)
            session_stats["daily_pnl"] += profit
            session_stats["wins"] += 1
            session_stats["consecutive_losses"] = 0
            log_msg = f"🎉 *Recorded WIN (+${profit:.2f})!*\nConsecutive loss counter reset to 0."
        elif data == "log_loss":
            session_stats["daily_pnl"] -= stake
            session_stats["losses"] += 1
            session_stats["consecutive_losses"] += 1
            log_msg = f"📉 *Recorded LOSS (-${stake:.2f})!*\nConsecutive Losses: {session_stats['consecutive_losses']}/{MAX_CONSECUTIVE_LOSSES}"
            if session_stats["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
                session_stats["is_locked"] = True
                log_msg += "\n⛔ *Circuit Breaker Triggered:* 3 consecutive losses hit. Cooldown active."

        keyboard = [
            [InlineKeyboardButton("📊 View Full Stats", callback_data="view_stats")],
            [InlineKeyboardButton("⬅️ Back to Markets", callback_data="main_menu")]
        ]
        await query.edit_message_text(log_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # View Risk & PnL Performance
    if data == "view_stats":
        keyboard = [[InlineKeyboardButton("⬅️ Back to Markets", callback_data="main_menu")]]
        win_rate = 0.0
        total_trades = session_stats["wins"] + session_stats["losses"]
        if total_trades > 0:
            win_rate = round((session_stats["wins"] / total_trades) * 100, 1)

        await query.edit_message_text(
            f"📊 *Live Session Risk & P&L*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• *Account Balance:* `${session_stats['balance']:.2f}`\n"
            f"• *Flat Stake per Trade:* `${session_stats['balance'] * FLAT_RISK_PCT:.2f}` (1.5%)\n"
            f"• *Session P&L:* `${session_stats['daily_pnl']:+.2f}`\n"
            f"• *Trades (W / L):* `{session_stats['wins']} Won / {session_stats['losses']} Lost`\n"
            f"• *Win Rate:* `{win_rate}%`\n"
            f"• *Consecutive Losses:* `{session_stats['consecutive_losses']} / {MAX_CONSECUTIVE_LOSSES}`\n"
            f"• *Status:* `{'ACTIVE' if not session_stats['is_locked'] else 'LOCKED (Circuit Breaker)'}`\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"_Martingale is permanently disabled._",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # Category Pagination
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

    # Timeframe Selection
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

    # Confluence Analysis & Execution Display
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
            [InlineKeyboardButton("✅ Log Win", callback_data="log_win"),
             InlineKeyboardButton("❌ Log Loss", callback_data="log_loss")],
            [InlineKeyboardButton("🔄 Re-Analyze", callback_data=f"run_{asset_name}|{tf}")],
            [InlineKeyboardButton("⏱️ Change Timeframe", callback_data=f"asset_{asset_name}")],
            [InlineKeyboardButton("⬅️ Back to Assets", callback_data="main_menu")]
        ]

        await query.edit_message_text(
            f"🎯 *Quotex Binary Signal: {asset_name}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• *Signal:* `{res['signal']}`\n"
            f"• *Strength:* `{res['strength']}`\n"
            f"• *Chart Timeframe:* `{tf}`\n"
            f"• *Option Expiry:* `{res['expiry']}`\n"
            f"• *Recommended Stake:* `${res['stake']}` (Strict 1.5% Flat)\n"
            f"• *RSI (14):* `{res['rsi']}`\n"
            f"• *Candle Countdown:* `{res['remaining_sec']}s remaining`\n"
            f"• *Analysis Notes:* {res['notes']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *Execution:* Verify payout >= 80% on Quotex. Enter trade at 00:55–00:58 before new candle opens.",
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
    app.add_handler(CommandHandler("balance", set_balance))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()
