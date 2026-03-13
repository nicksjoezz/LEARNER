import pandas as pd
import numpy as np
import ta
from datetime import timedelta

def ut_bot(df, a=1, c=10):
    """
    UT Bot Alerts implementation in Python
    a: Key Value (Sensitivity)
    c: ATR Period
    Optimized for performance with large datasets.
    """
    df = df.copy()

    # ATR calculation is already somewhat efficient in 'ta'
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=c)
    df['nLoss'] = a * df['atr']

    close_vals = df['close'].values
    nloss_vals = df['nLoss'].values
    size = len(df)
    xATRTrailingStop = np.zeros(size)

    # Optimized trailing stop loop
    prev_stop = 0.0
    for i in range(1, size):
        curr_close = close_vals[i]
        prev_close = close_vals[i-1]
        nloss = nloss_vals[i]

        if curr_close > prev_stop and prev_close > prev_stop:
            val = curr_close - nloss
            if prev_stop < val: prev_stop = val
        elif curr_close < prev_stop and prev_close < prev_stop:
            val = curr_close + nloss
            if prev_stop > val: prev_stop = val
        elif curr_close > prev_stop:
            prev_stop = curr_close - nloss
        else:
            prev_stop = curr_close + nloss
        xATRTrailingStop[i] = prev_stop

    df['xATRTrailingStop'] = xATRTrailingStop

    # Vectorized position tracking
    # We can't easily vectorize the whole thing because of dependencies,
    # but we can improve the loop.
    pos = np.zeros(size)
    curr_pos = 0.0
    for i in range(1, size):
        if close_vals[i-1] < xATRTrailingStop[i-1] and close_vals[i] > xATRTrailingStop[i-1]:
            curr_pos = 1.0
        elif close_vals[i-1] > xATRTrailingStop[i-1] and close_vals[i] < xATRTrailingStop[i-1]:
            curr_pos = -1.0
        pos[i] = curr_pos

    df['pos'] = pos

    # ema 1 of src (simply the close price for ema window 1)
    df['ema1'] = close_vals

    # Vectorized signals
    df['prev_xATR'] = df['xATRTrailingStop'].shift(1)
    df['prev_ema1'] = df['ema1'].shift(1)

    df['above'] = (df['ema1'] > df['xATRTrailingStop']) & (df['prev_ema1'] <= df['prev_xATR'])
    df['below'] = (df['ema1'] < df['xATRTrailingStop']) & (df['prev_ema1'] >= df['prev_xATR'])

    df['buy'] = (df['close'] > df['xATRTrailingStop']) & df['above']
    df['sell'] = (df['close'] < df['xATRTrailingStop']) & df['below']

    # Cleanup temporary columns
    df.drop(columns=['prev_xATR', 'prev_ema1'], inplace=True)

    return df

class Backtester:
    def __init__(self, df, exit_candles=3):
        self.df = df
        self.exit_candles = exit_candles

    def run(self):
        """Vectorized trade execution simulation for performance."""
        df = self.df.reset_index(drop=True)
        limit = len(df) - self.exit_candles - 1

        # Identify signal indices
        buy_indices = df.index[df['buy']].values
        buy_indices = buy_indices[buy_indices < limit]

        sell_indices = df.index[df['sell']].values
        sell_indices = sell_indices[sell_indices < limit]

        # Pre-extract arrays for faster access
        epochs = df['epoch'].values
        opens = df['open'].values
        closes = df['close'].values

        trades = []
        # Process BUYS
        for i in buy_indices:
            entry_idx = i + 1
            exit_idx = i + self.exit_candles
            entry_price = opens[entry_idx]
            exit_price = closes[exit_idx]
            profit = exit_price - entry_price
            trades.append({
                'type': 'buy',
                'entry_time': epochs[entry_idx],
                'entry_price': entry_price,
                'exit_time': epochs[exit_idx],
                'exit_price': exit_price,
                'profit': profit,
                'win': profit > 0
            })

        # Process SELLS
        for i in sell_indices:
            entry_idx = i + 1
            exit_idx = i + self.exit_candles
            entry_price = opens[entry_idx]
            exit_price = closes[exit_idx]
            profit = entry_price - exit_price
            trades.append({
                'type': 'sell',
                'entry_time': epochs[entry_idx],
                'entry_price': entry_price,
                'exit_time': epochs[exit_idx],
                'exit_price': exit_price,
                'profit': profit,
                'win': profit > 0
            })

        return pd.DataFrame(trades)

def analyze_performance(trades_df, interval_days=60):
    if trades_df.empty:
        return pd.DataFrame()

    trades_df['entry_time'] = pd.to_datetime(trades_df['entry_time'], unit='s')
    trades_df = trades_df.sort_values('entry_time')

    start_date = trades_df['entry_time'].min()
    end_date = trades_df['entry_time'].max()

    results = []
    current_start = start_date
    while current_start < end_date:
        current_end = current_start + timedelta(days=interval_days)
        period_trades = trades_df[(trades_df['entry_time'] >= current_start) & (trades_df['entry_time'] < current_end)]

        if not period_trades.empty:
            win_rate = period_trades['win'].mean()
            total_trades = len(period_trades)
            consecutive_losses = calculate_max_consecutive_losses(period_trades['win'])

            results.append({
                'start': current_start,
                'end': current_end,
                'win_rate': win_rate,
                'total_trades': total_trades,
                'max_consecutive_losses': consecutive_losses
            })

        current_start = current_end

    return pd.DataFrame(results)

def calculate_max_consecutive_losses(wins_series):
    max_losses = 0
    current_losses = 0
    for win in wins_series:
        if not win:
            current_losses += 1
            max_losses = max(max_losses, current_losses)
        else:
            current_losses = 0
    return max_losses

def simulate_financials(trades_df, initial_balance=1000, risk_pc=1, win_payout=0.95):
    """
    Simulates account growth based on trades.
    Using dynamic compounding stake based on CURRENT balance.
    winners gain +95% while losses -100% of risk per trade
    """
    if trades_df.empty:
        return initial_balance, 0, 0

    balance = initial_balance
    max_consec_losses = calculate_max_consecutive_losses(trades_df['win'])

    for win in trades_df['win']:
        # Dynamic stake based on current balance
        stake = balance * (float(risk_pc) / 100.0)
        # Minimum stake check (Deriv min is 0.35)
        if stake < 0.35: stake = 0.35

        if win:
            balance += stake * win_payout
        else:
            balance -= stake

        # Avoid account going below zero
        if balance < 0:
            balance = 0
            break

    total_profit = balance - initial_balance
    return balance, total_profit, max_consec_losses
