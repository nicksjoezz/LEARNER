import pandas as pd
import numpy as np
import os
import ta
from sklearn.ensemble import RandomForestClassifier
from crash_boom_strategy import crash_boom_mtf_strategy
import joblib

def train_ml_filter_crash_boom(symbol, tf_1m_path, tf_15m_path):
    df_1m = pd.read_csv(tf_1m_path)
    df_15m = pd.read_csv(tf_15m_path)

    # Apply MTF strategy to get signals
    df_with_signals = crash_boom_mtf_strategy(df_1m, df_15m, symbol)

    # Prepare features for ML
    df_with_signals['rsi_15m'] = ta.momentum.rsi(df_15m['close'], window=14).reindex(df_15m.index).ffill()
    # A simple way to get 15m RSI onto 1m
    # Use shift(1) to avoid look-ahead bias (only use data from the PREVIOUS closed 15m candle)
    rsi_15m_series = ta.momentum.rsi(df_15m['close'], window=14).shift(1)
    epoch_to_rsi_15m = {df_15m['epoch'].iloc[i]: rsi_15m_series.iloc[i] for i in range(len(df_15m))}
    def get_rsi_15m(epoch):
        # Round down to get the start of the current 15m window
        epoch_15m = (epoch // 900) * 900
        # Return the RSI of the candle that closed before this window
        return epoch_to_rsi_15m.get(epoch_15m, 50)

    df_with_signals['rsi_15m'] = df_with_signals['epoch'].apply(get_rsi_15m)
    df_with_signals['macd_diff'] = ta.trend.macd_diff(df_with_signals['close'])
    df_with_signals['adx'] = ta.trend.adx(df_with_signals['high'], df_with_signals['low'], df_with_signals['close'])

    feature_cols = ['rsi', 'rsi_15m', 'macd_diff', 'adx']
    df_with_signals = df_with_signals.fillna(0)

    # Identify outcomes (win or loss)
    # Using 5-candle exit as in backtest
    df_with_signals['outcome'] = 0 # 1 for win, 0 for loss
    exit_candles = 5
    std_return = df_with_signals['close'].diff().std()

    for side in ['buy', 'sell']:
        indices = df_with_signals.index[df_with_signals[side]].tolist()
        for idx in indices:
            if idx + exit_candles >= len(df_with_signals): continue
            entry_price = df_with_signals['open'].iloc[idx + 1]
            exit_price = df_with_signals['close'].iloc[idx + exit_candles]

            in_trade_candles = df_with_signals.iloc[idx+1:idx+exit_candles+1]
            if side == 'buy':
                profit = exit_price - entry_price
                win = profit > 0 or (in_trade_candles['close'] - in_trade_candles['open'] > 3 * std_return).any()
            else:
                profit = entry_price - exit_price
                win = profit > 0 or (in_trade_candles['close'] - in_trade_candles['open'] < -3 * std_return).any()

            df_with_signals.at[idx, 'outcome'] = 1 if win else 0

    # Train-Test Split (Chronological)
    signals = df_with_signals[df_with_signals['buy'] | df_with_signals['sell']]
    if len(signals) < 100:
        print(f"Not enough signals to train ML for {symbol}")
        return

    split_idx = int(len(signals) * 0.7)
    train_signals = signals.iloc[:split_idx]
    test_signals = signals.iloc[split_idx:]

    X_train = train_signals[feature_cols]
    y_train = train_signals['outcome']
    X_test = test_signals[feature_cols]
    y_test = test_signals['outcome']

    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    model.fit(X_train, y_train)

    train_score = model.score(X_train, y_train)
    test_score = model.score(X_test, y_test)

    print(f"\n--- ML Filter Analysis for {symbol} ---")
    print(f"Training Accuracy: {train_score:.2%}")
    print(f"Test Accuracy: {test_score:.2%}")

    # If test accuracy > 55%, save the model
    if test_score > 0.55:
        os.makedirs('models', exist_ok=True)
        joblib.dump(model, f'models/{symbol}_ml_filter.joblib')
        print(f"Model saved to models/{symbol}_ml_filter.joblib")
    else:
        print("Model accuracy too low to save.")

if __name__ == "__main__":
    train_ml_filter_crash_boom('BOOM500', 'data/BOOM500_60s_30d.csv', 'data/BOOM500_900s_30d.csv')
    train_ml_filter_crash_boom('CRASH500', 'data/CRASH500_60s_30d.csv', 'data/CRASH500_900s_30d.csv')
