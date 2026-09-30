"""
XAUUSD ALERT BOT  (alerts only - this script does NOT place any trades)
Rules:
  - H1 = trend bias, M15 = entry
  - Entry trigger: a "clean tap" on the M15 50-period moving average
    (candle touches the MA and closes back on the trend side, not through it)
  - Setup is a pending order: Buy Stop above the tap candle's high,
    or Sell Stop below the tap candle's low
  - Stop Loss: $25 beyond the tap candle's low/high
  - Take Profit: 1:3 risk-reward (change TP_RR below any time)
  - Spread check: the alert WARNS you if the typical spread you enter
    below is more than 15% of your stop loss
DATA: spot gold (XAU/USD) from Twelve Data, for BOTH H1 and M15.
There is NO fallback to futures. If the feed fails, no alert is sent.
Still confirm entry, SL and TP on your own chart before placing the order.
How to run:
  python xauusd_alert_bot.py test    -> sends one test message to Telegram
  python xauusd_alert_bot.py once    -> checks one time, then stops
  python xauusd_alert_bot.py         -> keeps checking
Install first:  pip install pandas requests
Set these values (environment variables / GitHub secrets):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TWELVE_DATA_KEY
"""
import os
import sys
import time
import pandas as pd
import requests
# ----------------------------- SETTINGS ------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
SYMBOL = "XAU/USD"          # spot gold on Twelve Data
SOURCE_LABEL = "XAU/USD spot (Twelve Data)"
MA_PERIOD = 50              # moving average length
SL_BUFFER_USD = 25.0        # stop loss distance beyond the tap candle, in $
TP_RR = 3.0                 # take profit as a multiple of the SL distance (1:3)
ENTRY_BUFFER_USD = 0.20     # pending order placed this far beyond the tap candle
TYPICAL_SPREAD_USD = 0.30   # <-- set this to your broker's typical XAUUSD spread
SPREAD_WARN_RATIO = 0.15    # warn if spread > 15% of the SL distance
MAX_ENTRY_DISTANCE_USD = 5.0  # skip the alert if entry is more than this far from current price
CHECK_EVERY_SECONDS = 300   # free plan allows 800 requests/day, so don't go faster
H1_REFRESH_SECONDS = 900    # H1 data is re-downloaded at most every 15 minutes
# ---------------------------------------------------------------------------
_h1_cache = {"df": None, "time": 0.0}
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
def get_data(interval):
    """Download spot gold candles from Twelve Data. Returns a dataframe or None."""
    if not TWELVE_DATA_KEY:
        print("Missing TWELVE_DATA_KEY. Add it as a GitHub secret and to the workflow.")
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": SYMBOL,
                "interval": interval,
                "outputsize": 200,
                "order": "ASC",
                "timezone": "UTC",
                "apikey": TWELVE_DATA_KEY,
            },
            timeout=20,
        )
        data = r.json()
    except Exception as e:
        print(f"Download failed ({interval}): {e}")
        return None
    if data.get("status") == "error" or "values" not in data:
        print(f"Twelve Data error ({interval}): {data.get('message', data)}")
        return None
    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df = df.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}
    )
    df = df.dropna(subset=["Close", "High", "Low"])
    if len(df) > MA_PERIOD + 5:
        return df
    print(f"Not enough candles returned ({interval}).")
    return None
def get_h1():
    """H1 data, re-downloaded only every H1_REFRESH_SECONDS to save requests."""
    now = time.time()
    if _h1_cache["df"] is None or now - _h1_cache["time"] > H1_REFRESH_SECONDS:
        df = get_data("1h")
        if df is not None:
            _h1_cache["df"] = df
            _h1_cache["time"] = now
    return _h1_cache["df"]
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
    h1 = get_h1()
    m15 = get_data("15min")
    if h1 is None or m15 is None:
        print("Could not get price data right now.")
        return None
    h1 = h1.copy()
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
    # Debug: how stale is this data? Helps spot feed-lag issues.
    candle_close_time = to_utc(candle_time) + pd.Timedelta(minutes=15)
    data_age_min = (pd.Timestamp.now(tz="UTC") - candle_close_time).total_seconds() / 60
    print(f"[debug] tap candle closed {data_age_min:.1f} min ago, "
          f"current_price={current_price:.2f}")
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
    entry_distance = abs(entry - current_price)
    if entry_distance > MAX_ENTRY_DISTANCE_USD:
        print(f"[debug] setup found but skipped - entry is ${entry_distance:.2f} "
              f"from current price (limit ${MAX_ENTRY_DISTANCE_USD:.2f})")
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
        f"Entry: {entry:.2f}  (${entry_distance:.2f} from current price)\n"
        f"Stop Loss: {sl:.2f}\n"
        f"Take Profit: {tp:.2f}\n"
        f"Reasoning: M15 candle at {candle_time} UTC tapped the 50 MA "
        f"({ma:.2f}) and closed back on the {bias.lower()} side "
        f"(H1 close {h1_close:.2f} vs H1 50 MA {h1_ma:.2f}). "
        f"Entry set {ENTRY_BUFFER_USD:.2f} beyond the tap candle, "
        f"SL {SL_BUFFER_USD:.0f} beyond it, TP at 1:{TP_RR:.0f}."
        f"{spread_note}\n"
        f"(Data source: {SOURCE_LABEL} - confirm on your own chart "
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
