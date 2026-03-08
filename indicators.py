import pandas as pd
import ta

def add_indicators(df):
    # RSI
    df['rsi'] = ta.momentum.rsi(df['close'], window=14)
    # MACD
    macd = ta.trend.MACD(df['close'])
    df['macd_diff'] = macd.macd_diff()
    # Bollinger Bands
    bb = ta.volatility.BollingerBands(df['close'])
    df['bb_hband'] = bb.bollinger_hband()
    df['bb_lband'] = bb.bollinger_lband()
    # ADX
    adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'])
    df['adx'] = adx.adx()
    # EMA
    df['ema_200'] = ta.trend.ema_indicator(df['close'], window=200)

    return df
