import pandas as pd
import numpy as np
import os
from crash_boom_strategy import crash_boom_mtf_strategy
import ta

def backtest_crash_boom(df_1m, symbol, exit_candles=5):
    """
    Backtests the strategy on 1m candles.
    Exit is fixed after X candles (scalping for a spike).
    If a spike/crash happens during those X candles, profit is high.
    Otherwise, we take a small loss due to the drift.
    """
    trades = []
    df = df_1m.copy().reset_index(drop=True)

    # Calculate returns for each candle to identify spikes/crashes
    df['candle_return'] = df['close'] - df['open']
    std_return = df['candle_return'].std()

    for i in range(len(df) - exit_candles - 1):
        # Entry Signal
        if df['buy'].iloc[i] or df['sell'].iloc[i]:
            entry_idx = i + 1
            exit_idx = i + exit_candles

            entry_price = df['open'].iloc[entry_idx]
            exit_price = df['close'].iloc[exit_idx]

            if df['buy'].iloc[i]:
                # Long for Boom spike
                profit = exit_price - entry_price
                # Check for spikes within the holding period
                in_trade_candles = df.iloc[entry_idx:exit_idx+1]
                spike_count = (in_trade_candles['candle_return'] > 3 * std_return).sum()
                win = profit > 0 or spike_count > 0
            else:
                # Short for Crash crash
                profit = entry_price - exit_price
                in_trade_candles = df.iloc[entry_idx:exit_idx+1]
                spike_count = (in_trade_candles['candle_return'] < -3 * std_return).sum()
                win = profit > 0 or spike_count > 0

            trades.append({
                'entry_time': df['epoch'].iloc[entry_idx],
                'entry_price': entry_price,
                'exit_time': df['epoch'].iloc[exit_idx],
                'exit_price': exit_price,
                'profit': profit,
                'win': win,
                'spikes_caught': spike_count
            })

    return pd.DataFrame(trades)

def run_full_analysis(symbol):
    tf_1m_path = f'data/{symbol}_60s_30d.csv'
    tf_15m_path = f'data/{symbol}_900s_30d.csv'

    df_1m = pd.read_csv(tf_1m_path)
    df_15m = pd.read_csv(tf_15m_path)

    # Apply strategy
    df_with_signals = crash_boom_mtf_strategy(df_1m, df_15m, symbol)

    # Run backtest
    trades = backtest_crash_boom(df_with_signals, symbol)

    if not trades.empty:
        win_rate = trades['win'].mean()
        total_profit = trades['profit'].sum()
        spikes_caught = trades['spikes_caught'].sum()

        print(f"\n--- Backtest Result for {symbol} ---")
        print(f"Total Trades: {len(trades)}")
        print(f"Win Rate: {win_rate:.2%}")
        print(f"Total Profit (Points): {total_profit:.2f}")
        print(f"Average Profit per Trade: {total_profit/len(trades):.4f}")
        print(f"Spikes/Crashes Caught: {spikes_caught}")
        print(f"Spike Capture Efficiency: {spikes_caught/len(trades):.2%}")
    else:
        print(f"No trades generated for {symbol}")

if __name__ == "__main__":
    run_full_analysis('BOOM500')
    run_full_analysis('CRASH500')
