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
    df['bb_mavg'] = bb.bollinger_mavg()
    df['bb_hband'] = bb.bollinger_hband()
    df['bb_lband'] = bb.bollinger_lband()
    df['bb_pct'] = (df['close'] - df['bb_lband']) / (df['bb_hband'] - df['bb_lband'] + 1e-9)
    df['bb_width'] = (df['bb_hband'] - df['bb_lband']) / df['close']
    df['bb_mid_dist'] = (df['close'] - df['bb_mavg']) / (df['bb_mavg'] + 1e-9)

    adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'])
    df['adx'] = adx.adx()

    # 3-Tier EMA Structure for 5m/15m Expiry
    df['ema_3'] = ta.trend.ema_indicator(df['close'], window=3)
    df['ema_8'] = ta.trend.ema_indicator(df['close'], window=8)
    df['ema_20'] = ta.trend.ema_indicator(df['close'], window=20)
    df['ema_50'] = ta.trend.ema_indicator(df['close'], window=50)

    # 1. EMA Alignment (Stacking)
    df['ema_bullish_stack'] = ((df['ema_3'] > df['ema_8']) & (df['ema_8'] > df['ema_20']) & (df['ema_20'] > df['ema_50'])).astype(int)
    df['ema_bearish_stack'] = ((df['ema_3'] < df['ema_8']) & (df['ema_8'] < df['ema_20']) & (df['ema_20'] < df['ema_50'])).astype(int)

    # 2. Price Position relative to EMAs
    df['price_vs_ema3'] = (df['close'] - df['ema_3']) / (df['close'] + 1e-9)
    df['price_vs_ema8'] = (df['close'] - df['ema_8']) / (df['close'] + 1e-9)
    df['price_vs_ema20'] = (df['close'] - df['ema_20']) / (df['close'] + 1e-9)
    df['price_vs_ema50'] = (df['close'] - df['ema_50']) / (df['close'] + 1e-9)

    # 3. Ribbon Width & EMA Slope
    df['ribbon_width'] = (df['ema_3'] - df['ema_50']) / (df['close'] + 1e-9)
    df['ema8_slope'] = df['ema_8'].diff(4) / (df['close'] + 1e-9) # 4 candles ago = 20min

    # 4. Recent EMA Cross Detection (Lookback 3 candles)
    ema3_prev = df['ema_3'].shift(3)
    ema8_prev = df['ema_8'].shift(3)
    df['recent_bull_cross'] = ((ema3_prev < ema8_prev) & (df['ema_3'] > df['ema_8'])).astype(int)
    df['recent_bear_cross'] = ((ema3_prev > ema8_prev) & (df['ema_3'] < df['ema_8'])).astype(int)

    # 5. Momentum Agreement (Price Change vs EMA3 Change over 3 candles)
    price_change = df['close'] - df['close'].shift(3)
    ema3_change = df['ema_3'] - df['ema_3'].shift(3)
    df['momentum_agrees'] = ((price_change > 0) == (ema3_change > 0)).astype(int)

    # --- ADVANCED ENGINEERED FEATURES ---

    # 1. Momentum Context
    df['rsi_slope'] = df['rsi'].diff(3)

    # RSI Zones: 0=Oversold, 1=Neutral, 2=Overbought
    df['rsi_zone'] = 1
    df.loc[df['rsi'] < 30, 'rsi_zone'] = 0
    df.loc[df['rsi'] > 70, 'rsi_zone'] = 2

    # 2. Candle Strength
    candle_range = (df['high'] - df['low']) + 1e-9
    df['candle_body_pct'] = np.abs(df['close'] - df['open']) / candle_range

    # Candle Streak (Consecutive same color)
    is_green = (df['close'] > df['open']).astype(int)
    is_red = (df['close'] < df['open']).astype(int)
    # Simple streak calculation
    green_streak = is_green.groupby((is_green != is_green.shift()).cumsum()).cumsum()
    red_streak = is_red.groupby((is_red != is_red.shift()).cumsum()).cumsum()
    df['candle_streak'] = green_streak - red_streak

    # 3. Volatility Context
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    # ATR Percentile (relative to last 100 periods) - Optimized vectorized implementation
    window = 100
    atr_vals = df['atr'].values
    if len(atr_vals) >= window:
        from numpy.lib.stride_tricks import sliding_window_view
        # Create sliding windows of size 100
        windows = sliding_window_view(atr_vals, window)
        # Current values are the last elements of each window
        current_vals = atr_vals[window-1:]
        # Count how many elements in each window are <= the current value
        counts = np.sum(windows <= current_vals[:, None], axis=1)
        ranks = counts / window
        df['atr_percentile'] = np.concatenate([np.full(window-1, np.nan), ranks])
    else:
        df['atr_percentile'] = np.nan

    # 4. Time Features (Market Context)
    dt = pd.to_datetime(df['epoch'], unit='s', utc=True)
    df['hour'] = dt.dt.hour
    df['day_of_week'] = dt.dt.dayofweek

    # Sessions: 1=Asian, 2=London, 3=New York, 0=Other
    df['session'] = 0
    df.loc[(df['hour'] >= 0) & (df['hour'] < 8), 'session'] = 1 # Asian
    df.loc[(df['hour'] >= 7) & (df['hour'] < 16), 'session'] = 2 # London
    df.loc[(df['hour'] >= 12) & (df['hour'] < 21), 'session'] = 3 # New York

    # 5. Lagged Memory (Reduced to 1 lag for cleaner feature set)
    df['rsi_lag_1'] = df['rsi'].shift(1)
    df['macd_lag_1'] = df['macd_diff'].shift(1)
    df['close_change_lag_1'] = df['close'].pct_change(1)

    return df
