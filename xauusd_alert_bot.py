"""
XAUUSD ALERT BOT  (alerts only - this script does NOT place any trades)

Your rules, as told to Claude:
  - H1 = trend bias, M15 = entry
  - Entry trigger: a "clean tap" on the M15 50-period moving average
    (candle touches the MA and closes back on the trend side, not through it)
  - Setup is a pending order: Buy Stop above the tap candle's high,
    or Sell Stop below the tap candle's low
  - Stop Loss: $25 beyond the tap candle's low/high
  - Take Profit: 1:3 risk-reward (change TP_RR below any time you want a
    different ratio, e.g. 1:2)
  - Spread check: the alert will WARN you if the typical spread you enter
    below is more than 15% of your stop loss, since a wide spread can
    trigger your SL almost immediately after entry

IMPORTANT: This is a free data feed (Yahoo Finance), not your broker's own
price feed, and it has NO live spread information. Always confirm the exact
entry, SL and TP on your own MT5/Match-Trader chart before placing the
pending order. Treat every alert as a heads-up, not a final instruction.

How to run:
  python xauusd_alert_bot.py test    -> sends one test message to Telegram
  python xauusd_alert_bot.py once    -> checks one time, then stops
  python xauusd_alert_bot.py         -> keeps checking every minute

Install first:  pip install yfinance pandas requests
Set these two values (environment variables) before running:
  TELEGRAM_TOKEN    = the token from @BotFather
  TELEGRAM_CHAT_ID  = your chat id
"""

import os
import sys
import time

import pandas as pd
import requests
import yfinance as yf

# ----------------------------- SETTINGS ------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Free price sources, tried in order. XAUUSD=X is spot gold.
# GC=F is gold futures (price is a little different from your broker).
SYMBOLS = ["XAUUSD=X", "GC=F"]

MA_PERIOD = 50              # moving average length
SL_BUFFER_USD = 25.0        # stop loss distance beyond the tap candle, in $
TP_RR = 3.0                 # take profit as a multiple of the SL distance (1:3)
ENTRY_BUFFER_USD = 0.20     # pending order placed this far beyond the tap candle
TYPICAL_SPREAD_USD = 0.30   # <-- set this to your broker's typical XAUUSD spread
SPREAD_WARN_RATIO = 0.15    # warn if spread > 15% of the SL distance
CHECK_EVERY_SECONDS = 60
# ---------------------------------------------------------------------------


def send_telegram(text):
    """Send a message to your Telegram chat."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID. Message was:\n" + text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15
        )
        if r.status_code != 200:
            print("Telegram error:", r.text)
    except Exception as e:
        print("Could not reach Telegram:", e)


def get_data(interval, period):
    """Download candles. Returns (dataframe, symbol_used) or (None, None)."""
    for symbol in SYMBOLS:
        try:
            df = yf.download(
                symbol,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
        except Exception as e:
            print(f"Download failed for {symbol}: {e}")
            continue
        if df is None or df.empty:
            continue
        # Newer yfinance versions return two-level column names; flatten them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close", "High", "Low"])
        if len(df) > MA_PERIOD + 5:
            return df, symbol
    return None, None


def to_utc(ts):
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def check_for_setup(max_age_minutes=None):
    """
    Look for a clean-tap BUY STOP / SELL STOP setup on the last CLOSED
    M15 candle. Returns (side, message, candle_time) or None.
    max_age_minutes: ignore setups older than this (used in 'once' mode).
    """
    h1, symbol = get_data("1h", "30d")
    m15, _ = get_data("15m", "5d")
    if h1 is None or m15 is None:
        print("Could not get price data right now.")
        return None

    # The very last row is the candle still forming, so use the one before it.
    h1["ma"] = h1["Close"].rolling(MA_PERIOD).mean()
    h1_close = float(h1["Close"].iloc[-2])
    h1_ma = float(h1["ma"].iloc[-2])
    if h1_close > h1_ma:
        bias = "BULLISH"
    elif h1_close < h1_ma:
        bias = "BEARISH"
    else:
        return None

    m15["ma"] = m15["Close"].rolling(MA_PERIOD).mean()
    tap = m15.iloc[-2]          # last closed M15 candle (candidate tap candle)
    candle_time = m15.index[-2]

    if max_age_minutes is not None:
        candle_close = to_utc(candle_time) + pd.Timedelta(minutes=15)
        age = (pd.Timestamp.now(tz="UTC") - candle_close).total_seconds() / 60
        if age > max_age_minutes:
            return None

    close = float(tap["Close"])
    high = float(tap["High"])
    low = float(tap["Low"])
    ma = float(tap["ma"])
    current_price = float(m15["Close"].iloc[-1])  # latest available price

    side = None
    if bias == "BULLISH" and low <= ma <= close:
        # candle dipped to/through the MA but closed back above it
        side = "BUY STOP"
        entry = high + ENTRY_BUFFER_USD
        sl = low - SL_BUFFER_USD
        risk = entry - sl
        tp = entry + risk * TP_RR
        condition = "BULLISH (H1 above 50 MA, M15 clean tap on the 50 MA)"
    elif bias == "BEARISH" and close <= ma <= high:
        # candle poked to/through the MA but closed back below it
        side = "SELL STOP"
        entry = low - ENTRY_BUFFER_USD
        sl = high + SL_BUFFER_USD
        risk = sl - entry
        tp = entry - risk * TP_RR
        condition = "BEARISH (H1 below 50 MA, M15 clean tap on the 50 MA)"
    else:
        return None

    spread_note = ""
    if TYPICAL_SPREAD_USD > risk * SPREAD_WARN_RATIO:
        spread_note = (
            f"\nWARNING: your set spread (${TYPICAL_SPREAD_USD:.2f}) is more than "
            f"{int(SPREAD_WARN_RATIO*100)}% of this stop loss distance "
            f"(${risk:.2f}). A wide spread could take you out right after entry - "
            f"double-check live spread on your platform before placing this."
        )

    message = (
        f"Market Condition: {condition}\n"
        f"Current Price: {current_price:.2f}\n"
        f"Trade Setup: {side}\n"
        f"Entry: {entry:.2f}\n"
        f"Stop Loss: {sl:.2f}\n"
        f"Take Profit: {tp:.2f}\n"
        f"Reasoning: M15 candle at {candle_time} tapped the 50 MA "
        f"({ma:.2f}) and closed back on the {bias.lower()} side "
        f"(H1 close {h1_close:.2f} vs H1 50 MA {h1_ma:.2f}). "
        f"Entry set {ENTRY_BUFFER_USD:.2f} beyond the tap candle, "
        f"SL {SL_BUFFER_USD:.0f} beyond it, TP at 1:{TP_RR:.0f}."
        f"{spread_note}\n"
        f"(Data source: {symbol}, free feed - confirm on your own chart "
        f"before placing the order.)"
    )
    return side, message, candle_time


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"

    if mode == "test":
        send_telegram("Test message: your XAUUSD alert bot is connected.")
        print("Test message sent (if your token and chat id are correct).")
        return

    if mode == "once":
        result = check_for_setup(max_age_minutes=20)
        if result:
            send_telegram(result[1])
            print("Alert sent.")
        else:
            print("No setup right now.")
        return

    # Default: keep running
    print("Bot running. Press Ctrl+C to stop.")
    last_alert = None
    while True:
        try:
            result = check_for_setup()
            if result:
                side, message, candle_time = result
                if last_alert != (candle_time, side):   # don't repeat the alert
                    send_telegram(message)
                    last_alert = (candle_time, side)
                    print("Alert sent:", side, candle_time)
        except Exception as e:
            print("Error (will try again):", e)
        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main()
