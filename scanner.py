"""
Daily stock scanner.

Pulls daily OHLCV data for the S&P 500, computes a handful of moving-average
and candlestick-based signals, and writes the results to docs/data.json for
the static dashboard to read.

This script does NOT place any trades. It only reads market data and writes
a JSON report. Designed to run on a schedule via GitHub Actions.
"""

import json
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
OUTPUT_PATH = "docs/data.json"
LOOKBACK_PERIOD = "14mo"  # need 200+ trading days for SMA200
BATCH_SIZE = 50
RSI_PERIOD = 14
PULLBACK_MIN_PCT = 3.0
PULLBACK_MAX_PCT = 8.0


def get_sp500_tickers() -> list[str]:
    """Fetch the current S&P 500 ticker list from a public dataset on GitHub."""
    df = pd.read_csv(SP500_URL)
    tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
    return sorted(set(tickers))


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add moving averages, MACD, and RSI columns to a price dataframe."""
    df = df.copy()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()

    df["EMA12"] = df["Close"].ewm(span=12, adjust=False).mean()
    df["EMA26"] = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = df["EMA12"] - df["EMA26"]
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI14"] = 100 - (100 / (1 + rs))
    df["RSI14"] = df["RSI14"].fillna(50)

    return df


def detect_candlestick_patterns(df: pd.DataFrame) -> list[str]:
    """Simple rule-based detection of a few common single/two-candle patterns
    on the most recent bar. Intentionally simple and transparent rather than
    relying on a compiled TA-Lib dependency."""
    if len(df) < 2:
        return []

    last = df.iloc[-1]
    prev = df.iloc[-2]

    body = abs(last["Close"] - last["Open"])
    rng = last["High"] - last["Low"]
    if rng <= 0:
        return []

    upper_wick = last["High"] - max(last["Close"], last["Open"])
    lower_wick = min(last["Close"], last["Open"]) - last["Low"]
    prev_body = abs(prev["Close"] - prev["Open"])

    patterns = []

    if body / rng < 0.1:
        patterns.append("Doji")

    if lower_wick > 2 * body and upper_wick < body and body / rng < 0.4:
        patterns.append("Hammer")

    if upper_wick > 2 * body and lower_wick < body and body / rng < 0.4:
        patterns.append("Shooting Star")

    bullish_engulf = (
        prev["Close"] < prev["Open"]
        and last["Close"] > last["Open"]
        and last["Close"] >= prev["Open"]
        and last["Open"] <= prev["Close"]
        and body > prev_body
    )
    if bullish_engulf:
        patterns.append("Bullish Engulfing")

    bearish_engulf = (
        prev["Close"] > prev["Open"]
        and last["Close"] < last["Open"]
        and last["Open"] >= prev["Close"]
        and last["Close"] <= prev["Open"]
        and body > prev_body
    )
    if bearish_engulf:
        patterns.append("Bearish Engulfing")

    return patterns


PATTERN_BIAS = {
    "Hammer": "bullish",
    "Bullish Engulfing": "bullish",
    "Shooting Star": "bearish",
    "Bearish Engulfing": "bearish",
    "Doji": "neutral",
}


def detect_signals(df: pd.DataFrame, ticker: str) -> list[dict]:
    """Look at the most recent bar (today vs. yesterday) and emit any
    signals that just triggered."""
    if len(df) < 205:
        return []

    last = df.iloc[-1]
    prev = df.iloc[-2]
    signals = []

    if prev["SMA50"] < prev["SMA200"] and last["SMA50"] >= last["SMA200"]:
        signals.append({"type": "Golden Cross", "detail": "SMA50 crossed above SMA200", "bias": "bullish"})

    if prev["SMA50"] > prev["SMA200"] and last["SMA50"] <= last["SMA200"]:
        signals.append({"type": "Death Cross", "detail": "SMA50 crossed below SMA200", "bias": "bearish"})

    if prev["MACD"] < prev["MACD_Signal"] and last["MACD"] >= last["MACD_Signal"]:
        signals.append({"type": "MACD Bullish Crossover", "detail": "MACD crossed above its signal line", "bias": "bullish"})

    if prev["MACD"] > prev["MACD_Signal"] and last["MACD"] <= last["MACD_Signal"]:
        signals.append({"type": "MACD Bearish Crossover", "detail": "MACD crossed below its signal line", "bias": "bearish"})

    if last["RSI14"] < 30:
        signals.append({"type": "RSI Oversold", "detail": f"RSI14 = {last['RSI14']:.1f}", "bias": "bullish"})

    if last["RSI14"] > 70:
        signals.append({"type": "RSI Overbought", "detail": f"RSI14 = {last['RSI14']:.1f}", "bias": "bearish"})

    prior_high = float(df["Close"].iloc[-31:-1].max())
    if float(last["Close"]) > prior_high:
        signals.append({"type": "30-Day High", "detail": f"Closed above 30-day high of ${prior_high:.2f}", "bias": "bullish"})

    peak_7d = float(df["Close"].iloc[-8:-1].max())
    pct_off = (peak_7d - float(last["Close"])) / peak_7d * 100
    if PULLBACK_MIN_PCT <= pct_off <= PULLBACK_MAX_PCT:
        signals.append({"type": "Pullback", "detail": f"{pct_off:.1f}% below 7-day high of ${peak_7d:.2f}", "bias": "bullish"})

    for pattern in detect_candlestick_patterns(df):
        signals.append({
            "type": pattern,
            "detail": f"{pattern} candlestick on the latest bar",
            "bias": PATTERN_BIAS.get(pattern, "neutral"),
        })

    for s in signals:
        s["ticker"] = ticker
        s["price"] = round(float(last["Close"]), 2)
        s["date"] = str(last.name.date())

    return signals


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def detect_setups(df: pd.DataFrame, ticker: str) -> dict | None:
    """Check whether today's bar completes a pullback-in-uptrend setup.

    Required sequence within the last 7 trading days:
    1. MACD Bullish Crossover (earliest occurrence used)
    2. 30-Day High on or after that crossover
    3. Today: Pullback (3-8% below 7-day peak)
    4. Today: Hammer or Bullish Engulfing
    """
    if len(df) < 205:
        return None

    n = len(df)

    # 1. Earliest MACD bullish crossover in the last 7 bars
    macd_cross_pos = None
    for i in range(n - 7, n):
        if df.iloc[i - 1]["MACD"] < df.iloc[i - 1]["MACD_Signal"] and df.iloc[i]["MACD"] >= df.iloc[i]["MACD_Signal"]:
            macd_cross_pos = i
            break

    if macd_cross_pos is None:
        return None

    # 2. 30-Day High on or after the crossover
    high_pos = None
    for i in range(macd_cross_pos, n):
        prior_high = float(df["Close"].iloc[i - 30:i].max())
        if float(df.iloc[i]["Close"]) > prior_high:
            high_pos = i
            break

    if high_pos is None:
        return None

    # 3. Pullback today
    peak_7d = float(df["Close"].iloc[-8:-1].max())
    today_close = float(df.iloc[-1]["Close"])
    pct_off = (peak_7d - today_close) / peak_7d * 100
    if not (PULLBACK_MIN_PCT <= pct_off <= PULLBACK_MAX_PCT):
        return None

    # 4. Reversal candle today
    patterns = detect_candlestick_patterns(df)
    reversal = [p for p in patterns if p in {"Hammer", "Bullish Engulfing"}]
    if not reversal:
        return None

    return {
        "ticker": ticker,
        "price": round(today_close, 2),
        "date": str(df.iloc[-1].name.date()),
        "macd_date": str(df.iloc[macd_cross_pos].name.date()),
        "high_date": str(df.iloc[high_pos].name.date()),
        "reversal_signal": reversal[0],
        "pullback_detail": f"{pct_off:.1f}% below 7-day high of ${peak_7d:.2f}",
    }


def main():
    tickers = get_sp500_tickers()
    print(f"Scanning {len(tickers)} tickers...")

    all_signals = []
    all_setups = []
    errors = []

    for batch in chunked(tickers, BATCH_SIZE):
        try:
            data = yf.download(
                batch,
                period=LOOKBACK_PERIOD,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as e:
            errors.append(f"batch download failed: {e}")
            continue

        for ticker in batch:
            try:
                if len(batch) == 1:
                    df = data
                else:
                    if ticker not in data.columns.get_level_values(0):
                        continue
                    df = data[ticker]
                df = df.dropna(how="all").dropna()
                if df.empty:
                    continue
                df = compute_indicators(df)
                all_signals.extend(detect_signals(df, ticker))
                setup = detect_setups(df, ticker)
                if setup:
                    all_setups.append(setup)
            except Exception as e:
                errors.append(f"{ticker}: {e}")

        time.sleep(1)  # be polite to the data source between batches

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker_count": len(tickers),
        "signal_count": len(all_signals),
        "setup_count": len(all_setups),
        "signals": sorted(all_signals, key=lambda s: s["ticker"]),
        "setups": sorted(all_setups, key=lambda s: s["ticker"]),
        "errors": errors[:50],
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done. {len(all_signals)} signals, {len(all_setups)} setups across {len(tickers)} tickers. {len(errors)} errors.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
