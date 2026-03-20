import pandas as pd
import numpy as np
import os
import ta

def analyze_mtf(symbol, tf_1m_path, tf_15m_path):
    df_1m = pd.read_csv(tf_1m_path)
    df_15m = pd.read_csv(tf_15m_path)

    # 15m trend (EMA 20)
    df_15m['ema_20'] = ta.trend.ema_indicator(df_15m['close'], window=20)
    df_15m['trend_up'] = df_15m['close'] > df_15m['ema_20']

    # Align 15m trend to 1m data
    # Shift trend by 1 to use PREVIOUS candle's close (avoid look-ahead bias)
    df_15m['prev_trend_up'] = df_15m['trend_up'].shift(1)
    trend_map = df_15m.set_index('epoch')['prev_trend_up'].to_dict()

    def get_trend(epoch):
        # Round epoch down to nearest 900 to get the START of the current 15m candle
        epoch_15m = (epoch // 900) * 900
        # Return the trend from the candle that CLOSED before this epoch
        return trend_map.get(epoch_15m, False)

    df_1m['15m_trend_up'] = df_1m['epoch'].apply(get_trend)

    # Identify spikes/crashes
    df_1m['returns'] = df_1m['close'] - df_1m['open']
    std_return = df_1m['returns'].std()

    if 'BOOM' in symbol:
        df_1m['is_spike'] = df_1m['returns'] > 3 * std_return
        # A good trend for Boom is when 15m is UP
        success_trend = df_1m[df_1m['is_spike']]['15m_trend_up'].mean()
    else:
        df_1m['is_spike'] = df_1m['returns'] < -3 * std_return
        # A good trend for Crash is when 15m is DOWN (trend_up is false)
        success_trend = 1 - df_1m[df_1m['is_spike']]['15m_trend_up'].mean()

    print(f"\n--- MTF Analysis for {symbol} ---")
    print(f"Percentage of spikes occurring within the 15m trend: {success_trend:.2%}")
    print(f"Total Spikes: {df_1m['is_spike'].sum()}")
    print(f"Spikes in Trend: {df_1m[df_1m['is_spike']]['15m_trend_up'].sum() if 'BOOM' in symbol else len(df_1m[df_1m['is_spike']]) - df_1m[df_1m['is_spike']]['15m_trend_up'].sum()}")

if __name__ == "__main__":
    analyze_mtf('BOOM500', 'data/BOOM500_60s_30d.csv', 'data/BOOM500_900s_30d.csv')
    analyze_mtf('CRASH500', 'data/CRASH500_60s_30d.csv', 'data/CRASH500_900s_30d.csv')
