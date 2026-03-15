import pandas as pd
import ta
import numpy as np

def add_indicators(df):
    df = df.copy()

    # Core Indicators
    df['rsi'] = ta.momentum.rsi(df['close'], window=14)

    macd = ta.trend.MACD(df['close'])
    df['macd_diff'] = macd.macd_diff()

    bb = ta.volatility.BollingerBands(df['close'])
    df['bb_hband'] = bb.bollinger_hband()
    df['bb_lband'] = bb.bollinger_lband()
    df['bb_pct'] = (df['close'] - df['bb_lband']) / (df['bb_hband'] - df['bb_lband'] + 1e-9)

    adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'])
    df['adx'] = adx.adx()

    df['ema_200'] = ta.trend.ema_indicator(df['close'], window=200)
    df['ema_dist'] = (df['close'] - df['ema_200']) / df['close']

    # --- ADVANCED FEATURES FOR ML ---

    # 1. Momentum Slopes
    df['rsi_slope'] = df['rsi'].diff(3)
    df['macd_slope'] = df['macd_diff'].diff(3)

    # 2. Lagged Features
    for lag in range(1, 4):
        df[f'rsi_lag_{lag}'] = df['rsi'].shift(lag)
        df[f'macd_lag_{lag}'] = df['macd_diff'].shift(lag)
        df[f'close_change_lag_{lag}'] = df['close'].pct_change(lag)

    # 3. Volatility Regime
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    df['atr_ma'] = df['atr'].rolling(window=50).mean()
    df['vol_regime'] = df['atr'] / (df['atr_ma'] + 1e-9)

    # 4. Stochastic (Finding range-bound similarities)
    stoch = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'], window=14, smooth_window=3)
    df['stoch_k'] = stoch.stoch()
    df['stoch_d'] = stoch.stoch_signal()

    # 5. Rate of Change (ROC)
    df['roc'] = ta.momentum.roc(df['close'], window=12)

    # 6. Distance from BB bands (Similarity in overextension)
    df['dist_bb_upper'] = (df['bb_hband'] - df['close']) / df['close']
    df['dist_bb_lower'] = (df['close'] - df['bb_lband']) / df['close']

    return df
