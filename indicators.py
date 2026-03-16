import pandas as pd
import ta
import numpy as np

def add_indicators(df):
    df = df.copy()
    from numpy.lib.stride_tricks import sliding_window_view

    def get_percentile(values, window_size=100):
        if len(values) < window_size:
            return np.full(len(values), np.nan)
        windows = sliding_window_view(values, window_size)
        current_vals = values[window_size-1:]
        counts = np.sum(windows <= current_vals[:, None], axis=1)
        ranks = counts / window_size
        return np.concatenate([np.full(window_size-1, np.nan), ranks])

    # 1. RSI (7, 14)
    df['rsi7'] = ta.momentum.rsi(df['close'], window=7)
    df['rsi14'] = ta.momentum.rsi(df['close'], window=14)

    df['rsi7_slope'] = df['rsi7'].diff(3)
    df['rsi7_strength'] = np.abs(df['rsi7'] - 50) / 50
    df['rsi_agreement'] = ((df['rsi7'] > 50) == (df['rsi14'] > 50)).astype(int)

    price_up = df['close'] > df['close'].shift(3)
    rsi_up = df['rsi7'] > df['rsi7'].shift(3)
    df['bull_divergence'] = ((~price_up) & rsi_up).astype(int)
    df['bear_divergence'] = (price_up & (~rsi_up)).astype(int)

    df['rsi7_overbought'] = (df['rsi7'] > 70).astype(int)
    df['rsi7_oversold'] = (df['rsi7'] < 30).astype(int)

    # 2. MACD (3, 8, 5) - Fast MACD
    ema3 = ta.trend.ema_indicator(df['close'], window=3)
    ema8 = ta.trend.ema_indicator(df['close'], window=8)
    macd_line = ema3 - ema8
    signal_line = macd_line.ewm(span=5).mean()
    df['macd_hist'] = macd_line - signal_line

    df['macd_hist_rising'] = (df['macd_hist'] > df['macd_hist'].shift(3)).astype(int)
    df['macd_hist_strength'] = np.abs(df['macd_hist']) / (df['close'] + 1e-9)
    df['macd_above_zero'] = (macd_line > 0).astype(int)

    macd_prev = macd_line.shift(3)
    signal_prev = signal_line.shift(3)
    df['macd_cross_up'] = ((macd_prev < signal_prev) & (macd_line > signal_line)).astype(int)
    df['macd_cross_down'] = ((macd_prev > signal_prev) & (macd_line < signal_line)).astype(int)
    df['hist_acceleration'] = df['macd_hist'].diff(1)

    # 3. Bollinger Bands (20, 2.0) and (10, 1.5)
    bb20 = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2.0)
    df['bb20_upper'] = bb20.bollinger_hband()
    df['bb20_mid'] = bb20.bollinger_mavg()
    df['bb20_lower'] = bb20.bollinger_lband()

    bb10 = ta.volatility.BollingerBands(df['close'], window=10, window_dev=1.5)
    df['bb10_upper'] = bb10.bollinger_hband()
    df['bb10_mid'] = bb10.bollinger_mavg()
    df['bb10_lower'] = bb10.bollinger_lband()

    df['bb20_position'] = (df['close'] - df['bb20_lower']) / (df['bb20_upper'] - df['bb20_lower'] + 1e-9)
    df['bb10_position'] = (df['close'] - df['bb10_lower']) / (df['bb10_upper'] - df['bb10_lower'] + 1e-9)

    df['bb20_width'] = (df['bb20_upper'] - df['bb20_lower']) / (df['bb20_mid'] + 1e-9)
    df['volatility_expanding'] = (df['bb20_width'] > df['bb20_width'].shift(3)).astype(int)

    # BB Squeeze (Bottom 20% of width history)
    df['bb20_width_percentile'] = get_percentile(df['bb20_width'].values, 50)
    df['bb_squeeze'] = (df['bb20_width_percentile'] < 0.2).astype(int)

    df['above_bb20_mid'] = (df['close'] > df['bb20_mid']).astype(int)
    df['bb_outside_upper'] = (df['close'] > df['bb20_upper']).astype(int)
    df['bb_outside_lower'] = (df['close'] < df['bb20_lower']).astype(int)

    # 4. ATR (7, 14)
    df['atr7'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=7)
    df['atr14'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)

    df['atr_percentile'] = get_percentile(df['atr14'].values, 50)
    df['atr_ratio'] = df['atr7'] / (df['atr14'] + 1e-9)

    candle_range = df['high'] - df['low']
    df['candle_vs_atr'] = candle_range / (df['atr14'] + 1e-9)
    df['atr_expanding'] = (df['atr14'] > df['atr14'].shift(3)).astype(int)

    df['vol_dead'] = (df['atr_percentile'] < 0.2).astype(int)
    df['vol_extreme'] = (df['atr_percentile'] > 0.9).astype(int)
    df['vol_normal'] = ((df['atr_percentile'] > 0.3) & (df['atr_percentile'] < 0.7)).astype(int)

    # 5. Stochastic (5, 3, 3) and (14, 3, 3)
    stoch5 = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'], window=5, smooth_window=3)
    df['stoch_k_fast'] = stoch5.stoch()
    df['stoch_d_fast'] = stoch5.stoch_signal()

    stoch14 = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'], window=14, smooth_window=3)
    df['stoch_k_slow'] = stoch14.stoch()

    df['stoch_fast_bull'] = ((df['stoch_k_fast'].shift(3) < df['stoch_d_fast'].shift(3)) & (df['stoch_k_fast'] > df['stoch_d_fast'])).astype(int)
    df['stoch_fast_bear'] = ((df['stoch_k_fast'].shift(3) > df['stoch_d_fast'].shift(3)) & (df['stoch_k_fast'] < df['stoch_d_fast'])).astype(int)

    df['stoch_overbought'] = (df['stoch_k_fast'] > 80).astype(int)
    df['stoch_oversold'] = (df['stoch_k_fast'] < 20).astype(int)
    df['stoch_agreement'] = ((df['stoch_k_fast'] > 50) == (df['stoch_k_slow'] > 50)).astype(int)
    df['stoch_slope'] = df['stoch_k_fast'].diff(3)

    # 6. CCI (7, 14)
    df['cci7'] = ta.trend.cci(df['high'], df['low'], df['close'], window=7)
    df['cci14'] = ta.trend.cci(df['high'], df['low'], df['close'], window=14)

    cci14_prev = df['cci14'].shift(3)
    df['cci_cross_up'] = ((cci14_prev < 0) & (df['cci14'] > 0)).astype(int)
    df['cci_cross_down'] = ((cci14_prev > 0) & (df['cci14'] < 0)).astype(int)
    df['cci_break_up'] = ((cci14_prev < 100) & (df['cci14'] > 100)).astype(int)
    df['cci_break_down'] = ((cci14_prev > -100) & (df['cci14'] < -100)).astype(int)
    df['cci_agreement'] = ((df['cci7'] > 0) == (df['cci14'] > 0)).astype(int)
    df['cci_extreme_up'] = (df['cci14'] > 200).astype(int)
    df['cci_extreme_dn'] = (df['cci14'] < -200).astype(int)

    # 7. EMA Ribbon (3, 8, 20, 50) - Same as previous v6 but integrated
    df['ema_3'] = ta.trend.ema_indicator(df['close'], window=3)
    df['ema_8'] = ta.trend.ema_indicator(df['close'], window=8)
    df['ema_20'] = ta.trend.ema_indicator(df['close'], window=20)
    df['ema_50'] = ta.trend.ema_indicator(df['close'], window=50)

    df['ema_bullish_stack'] = ((df['ema_3'] > df['ema_8']) & (df['ema_8'] > df['ema_20']) & (df['ema_20'] > df['ema_50'])).astype(int)
    df['ema_bearish_stack'] = ((df['ema_3'] < df['ema_8']) & (df['ema_8'] < df['ema_20']) & (df['ema_20'] < df['ema_50'])).astype(int)

    df['price_vs_ema3'] = (df['close'] - df['ema_3']) / (df['close'] + 1e-9)
    df['price_vs_ema8'] = (df['close'] - df['ema_8']) / (df['close'] + 1e-9)
    df['price_vs_ema20'] = (df['close'] - df['ema_20']) / (df['close'] + 1e-9)
    df['price_vs_ema50'] = (df['close'] - df['ema_50']) / (df['close'] + 1e-9)
    df['ribbon_width'] = (df['ema_3'] - df['ema_50']) / (df['close'] + 1e-9)
    df['ema8_slope'] = df['ema_8'].diff(4) / (df['close'] + 1e-9)

    df['momentum_agrees'] = (((df['close'] - df['close'].shift(3)) > 0) == ((df['ema_3'] - df['ema_3'].shift(3)) > 0)).astype(int)

    # 8. Candle Pattern Features
    df['body_size'] = np.abs(df['close'] - df['open'])
    df['total_range'] = df['high'] - df['low'] + 1e-9
    df['body_ratio'] = df['body_size'] / df['total_range']

    upper_wick = df['high'] - np.maximum(df['close'], df['open'])
    lower_wick = np.minimum(df['close'], df['open']) - df['low']
    df['wick_ratio'] = upper_wick / (lower_wick + 1e-4)

    df['is_bullish'] = (df['close'] > df['open']).astype(int)

    # Streak Calculation
    is_up = (df['close'] > df['open']).astype(int)
    is_down = (df['close'] < df['open']).astype(int)
    df['candle_streak'] = is_up.groupby((is_up != is_up.shift()).cumsum()).cumsum() - \
                          is_down.groupby((is_down != is_down.shift()).cumsum()).cumsum()

    df['bull_engulf'] = ((df['close'] > df['open'].shift(1)) & (df['open'] < df['close'].shift(1)) & (df['close'].shift(1) < df['open'].shift(1))).astype(int)
    df['bear_engulf'] = ((df['close'] < df['open'].shift(1)) & (df['open'] > df['close'].shift(1)) & (df['close'].shift(1) > df['open'].shift(1))).astype(int)
    df['is_doji'] = (df['body_ratio'] < 0.1).astype(int)
    df['gap'] = (df['open'] - df['close'].shift(1)) / (df['close'].shift(1) + 1e-9)

    # 9. Time Features
    dt = pd.to_datetime(df['epoch'], unit='s', utc=True)
    df['hour'] = dt.dt.hour
    df['day_of_week'] = dt.dt.dayofweek
    df['session'] = 0
    df.loc[(df['hour'] >= 0) & (df['hour'] < 8), 'session'] = 1 # Asian
    df.loc[(df['hour'] >= 7) & (df['hour'] < 16), 'session'] = 2 # London
    df.loc[(df['hour'] >= 12) & (df['hour'] < 21), 'session'] = 3 # New York

    # Lagged Memory
    df['rsi7_lag_1'] = df['rsi7'].shift(1)
    df['macd_lag_1'] = df['macd_hist'].shift(1)
    df['close_change_lag_1'] = df['close'].pct_change(1)

    return df
