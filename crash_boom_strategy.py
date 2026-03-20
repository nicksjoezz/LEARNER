import pandas as pd
import numpy as np
import ta

def crash_boom_mtf_strategy(df_1m, df_15m, symbol):
    """
    MTF Strategy for Crash and Boom 500
    - 15m Trend: EMA 20
    - 1m Entry: RSI (14) Pullback and Trend Alignment
    """
    # 1. 15m Trend Calculation
    df_15m = df_15m.copy()
    df_15m['ema_20'] = ta.trend.ema_indicator(df_15m['close'], window=20)
    df_15m['trend_up'] = df_15m['close'] > df_15m['ema_20']

    # Map 15m trend to 1m
    # To avoid look-ahead bias, we use the trend of the PREVIOUS 15m candle.
    # We shift the trend by 1 (pos 15m bar trend for future 1m bars).
    df_15m['prev_trend_up'] = df_15m['trend_up'].shift(1)
    trend_map = df_15m.set_index('epoch')['prev_trend_up'].to_dict()
    def get_trend(epoch):
        # Round epoch down to nearest 900 to find the START of the current 15m candle
        epoch_15m = (epoch // 900) * 900
        # return the trend of the candle that CLOSED just before this epoch
        return trend_map.get(epoch_15m, False)

    df_1m = df_1m.copy()
    df_1m['15m_trend_up'] = df_1m['epoch'].apply(get_trend)

    # 2. 1m Technical Indicators
    df_1m['rsi'] = ta.momentum.rsi(df_1m['close'], window=14)
    df_1m['ema_5'] = ta.trend.ema_indicator(df_1m['close'], window=5)

    # 3. Strategy Logic
    df_1m['buy'] = False
    df_1m['sell'] = False

    if 'BOOM' in symbol:
        # Catching the Boom Spike:
        # Scalping strategy for Multipliers
        # Trend is UP (15m) + Deep Pullback (1m RSI < 35)
        df_1m.loc[(df_1m['15m_trend_up'] == True) &
                  (df_1m['rsi'] < 35), 'buy'] = True
    else:
        # Catching the Crash:
        df_1m.loc[(df_1m['15m_trend_up'] == False) &
                  (df_1m['rsi'] > 65), 'sell'] = True

    return df_1m
