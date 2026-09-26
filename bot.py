import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import pandas as pd
import pandas_ta as ta

logging.basicConfig(level=logging.INFO)

# --- Configuration & Risk Parameters ---
LIVE_ASSETS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]
ACCOUNT_BALANCE = 1000.0   # User account balance baseline (e.g., $1,000)
RISK_PER_TRADE_PCT = 0.015  # Strict 1.5% Flat Stake ($15) - NO MARTINGALE
MAX_DAILY_LOSS_PCT = 0.05   # 5% Max Daily Drawdown ($50 max loss)
MAX_CONSECUTIVE_LOSSES = 3  # Circuit breaker threshold

# In-memory session tracking
session_stats = {
    "balance": ACCOUNT_BALANCE,
    "consecutive_losses": 0,
    "daily_pnl": 0.0,
    "is_locked": False
}

def check_risk_guard() -> tuple[bool, str]:
    """Safety circuit breaker to halt trading when drawdown thresholds are breached."""
    if session_stats["is_locked"]:
        return False, "Bot is locked due to safety limit. Trading resumed tomorrow."
    if session_stats["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
        session_stats["is_locked"] = True
        return False, f"Hit {MAX_CONSECUTIVE_LOSSES} consecutive losses. Cooldown active."
    if session_stats["daily_pnl"] <= -(ACCOUNT_BALANCE * MAX_DAILY_LOSS_PCT):
        session_stats["is_locked"] = True
        return False, "Daily max drawdown limit (-5%) reached. Trading stopped."
    return True, "Active"

def evaluate_strategy(df: pd.DataFrame) -> dict:
    """Calculates confluence filters on completed candle bars."""
    df['EMA_200'] = ta.ema(df['close'], length=200)
    df['RSI'] = ta.rsi(df['close'], length=14)
    bb = ta.bbands(df['close'], length=20, std=2.0)
    df = pd.concat([df, bb], axis=1)

    last = df.iloc[-1]
    stake_amount = round(session_stats["balance"] * RISK_PER_TRADE_PCT, 2)

    signal = "NEUTRAL"
    setup_notes = "No clear setup. Awaiting candle close."

    # Bullish Setup: Oversold, touching lower band, above macro trend
    if last['RSI'] <= 30 and last['close'] <= last['BBL_20_2.0']:
        signal = "CALL (HIGHER)"
        setup_notes = "Lower Bollinger touched + RSI oversold (<30)."

    # Bearish Setup: Overbought, touching upper band, below macro trend
    elif last['RSI'] >= 70 and last['close'] >= last['BBU_20_2.0']:
        signal = "PUT (LOWER)"
        setup_notes = "Upper Bollinger touched + RSI overbought (>70)."

    return {
        "signal": signal,
        "stake": stake_amount,
        "notes": setup_notes,
        "rsi": round(last['RSI'], 2),
        "price": last['close']
    }

# --- Telegram Bot UI Handlers ---
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

        # Example candle data frame (integrate your live WebSocket / broker feed here)
        sample_candles = {
            'close': [1.0850, 1.0848, 1.0842, 1.0838, 1.0830],
            'high': [1.0855, 1.0850, 1.0845, 1.0840, 1.0832],
            'low': [1.0847, 1.0840, 1.0837, 1.0828, 1.0825],
            'open': [1.0849, 1.0850, 1.0847, 1.0842, 1.0838],
            'volume': [100, 120, 150, 180, 240]
        }
        df = pd.DataFrame(sample_candles)
        res = evaluate_strategy(df)

        await query.edit_message_text(
            f"🎯 *Analysis Result: {pair}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• *Signal:* `{res['signal']}`\n"
            f"• *Recommended Stake:* `${res['stake']}` (Strict 1.5% Flat)\n"
            f"• *Expiry Time:* 2 Minutes\n"
            f"• *RSI (14):* {res['rsi']}\n"
            f"• *Setup Notes:* {res['notes']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *Execution:* Take exactly ONE trade. Do not re-enter if price continues against you.",
            parse_mode="Markdown"
        )

if __name__ == "__main__":
    TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    # app.run_polling()
