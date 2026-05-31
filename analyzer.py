import requests
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands
from config import BINANCE_API

def get_bnb_klines(interval="1m", limit=100):
    try:
        url = f"{BINANCE_API}/klines"
        params = {"symbol": "BNBUSDT", "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        df["close"] = pd.to_numeric(df["close"])
        df["volume"] = pd.to_numeric(df["volume"])
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        return df
    except:
        return None

def get_btc_trend():
    try:
        url = f"{BINANCE_API}/klines"
        params = {"symbol": "BTCUSDT", "interval": "5m", "limit": 10}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        closes = [float(d[4]) for d in data]
        change = (closes[-1] - closes[0]) / closes[0] * 100
        return {
            "price": closes[-1],
            "change_5m": round(change, 2),
            "trend": "UP" if change > 0 else "DOWN"
        }
    except:
        return None

def get_chainlink_price(w3, chainlink_contract):
    try:
        latest = chainlink_contract.functions.latestRoundData().call()
        price = latest[1] / 1e8
        return round(price, 2)
    except:
        return None

def get_binance_price():
    try:
        url = f"{BINANCE_API}/ticker/price"
        r = requests.get(url, params={"symbol": "BNBUSDT"}, timeout=5)
        return float(r.json()["price"])
    except:
        return None

def analyze_signals(df):
    if df is None or len(df) < 30:
        return None

    close = df["close"]

    # EMA
    ema9 = EMAIndicator(close, window=9).ema_indicator()
    ema21 = EMAIndicator(close, window=21).ema_indicator()
    ema_signal = "UP" if ema9.iloc[-1] > ema21.iloc[-1] else "DOWN"

    # RSI
    rsi = RSIIndicator(close, window=14).rsi()
    rsi_val = round(rsi.iloc[-1], 1)
    if rsi_val > 60:
        rsi_signal = "UP"
    elif rsi_val < 40:
        rsi_signal = "DOWN"
    else:
        rsi_signal = "UP" if rsi_val > 50 else "DOWN"

    # MACD
    macd = MACD(close)
    macd_diff = macd.macd_diff().iloc[-1]
    macd_signal = "UP" if macd_diff > 0 else "DOWN"

    # Bollinger Bands
    bb = BollingerBands(close)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]
    bb_mid = bb.bollinger_mavg().iloc[-1]
    current_price = close.iloc[-1]
    bb_signal = "UP" if current_price > bb_mid else "DOWN"

    # Volume trend
    vol_avg = df["volume"].tail(10).mean()
    vol_current = df["volume"].iloc[-1]
    vol_signal = "UP" if vol_current > vol_avg else "DOWN"

    # Price momentum
    price_change = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100
    momentum_signal = "UP" if price_change > 0 else "DOWN"

    signals = {
        "ema": ema_signal,
        "rsi": rsi_signal,
        "macd": macd_signal,
        "bb": bb_signal,
        "volume": vol_signal,
        "momentum": momentum_signal,
    }

    up_count = sum(1 for v in signals.values() if v == "UP")
    down_count = len(signals) - up_count
    prediction = "UP" if up_count >= down_count else "DOWN"
    confidence = max(up_count, down_count) / len(signals) * 100

    return {
        "signals": signals,
        "prediction": prediction,
        "confidence": round(confidence, 1),
        "up_count": up_count,
        "down_count": down_count,
        "rsi_value": rsi_val,
        "ema9": round(ema9.iloc[-1], 4),
        "ema21": round(ema21.iloc[-1], 4),
        "macd_diff": round(macd_diff, 4),
        "current_price": round(current_price, 4),
    }

def get_round_pool_info(prediction_contract, epoch):
    try:
        round_data = prediction_contract.functions.rounds(epoch).call()
        total = round_data[8] / 1e18
        bull = round_data[9] / 1e18
        bear = round_data[10] / 1e18
        bull_pct = (bull / total * 100) if total > 0 else 50
        bear_pct = (bear / total * 100) if total > 0 else 50
        bull_payout = (total / bull) if bull > 0 else 0
        bear_payout = (total / bear) if bear > 0 else 0
        return {
            "total": round(total, 4),
            "bull": round(bull, 4),
            "bear": round(bear, 4),
            "bull_pct": round(bull_pct, 1),
            "bear_pct": round(bear_pct, 1),
            "bull_payout": round(bull_payout, 2),
            "bear_payout": round(bear_payout, 2),
        }
    except:
        return None
