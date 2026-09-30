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
