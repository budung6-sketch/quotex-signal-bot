import time
import math

def evaluate_strategy(pair: str, tf: str) -> dict:
    # Generates a dynamic candle cycle based on time and asset hash
    # This alternates between oversold, neutral, and overbought
    seed = sum(ord(c) for c in pair)
    current_cycle = (time.time() / 60.0) + seed
    
    # Generate 15 simulated dynamic candle closes
    sample_closes = []
    base_price = 1.0800
    for i in range(15):
        t = current_cycle - (14 - i) * 0.5
        # Sine-wave price oscillation: oscillates up and down
        wave = math.sin(t) * 0.0025 + math.cos(t * 0.5) * 0.0010
        sample_closes.append(round(base_price + wave, 5))

    rsi = round(compute_rsi(sample_closes, period=14), 1)
    stake_amount = round(session_stats["balance"] * FLAT_RISK_PCT, 2)
    last_price = sample_closes[-1]

    signal = "NEUTRAL (WAIT ⏸️)"
    notes = "Price consolidating inside mid-range (35–65). Await boundary touch."

    # Bullish Confluence
    if rsi <= 32:
        signal = "CALL (HIGHER / 🟢)"
        notes = f"RSI oversold ({rsi} <= 32). Buyer rejection from lower boundary."
    # Bearish Confluence
    elif rsi >= 68:
        signal = "PUT (LOWER / 🔴)"
        notes = f"RSI overbought ({rsi} >= 68). Seller rejection from upper boundary."

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
