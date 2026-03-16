import pandas as pd
import numpy as np
import ta
from datetime import timedelta

def ut_bot(df, a=1, c=10):
    """
    UT Bot Alerts implementation in Python
    a: Key Value (Sensitivity)
    c: ATR Period
    """
    df = df.copy()

    # ATR
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=c)
    df['nLoss'] = a * df['atr']

    # src = close
    src = df['close']

    # ATR Trailing Stop calculation using numpy for safety and speed
    close_vals = df['close'].values
    nloss_vals = df['nLoss'].values
    xATRTrailingStop = np.zeros(len(df))

    for i in range(1, len(df)):
        if i == 0: continue
        if close_vals[i] > xATRTrailingStop[i-1] and close_vals[i-1] > xATRTrailingStop[i-1]:
            xATRTrailingStop[i] = max(xATRTrailingStop[i-1], close_vals[i] - nloss_vals[i])
        elif close_vals[i] < xATRTrailingStop[i-1] and close_vals[i-1] < xATRTrailingStop[i-1]:
            xATRTrailingStop[i] = min(xATRTrailingStop[i-1], close_vals[i] + nloss_vals[i])
        elif close_vals[i] > xATRTrailingStop[i-1]:
            xATRTrailingStop[i] = close_vals[i] - nloss_vals[i]
        else:
            xATRTrailingStop[i] = close_vals[i] + nloss_vals[i]

    df['xATRTrailingStop'] = xATRTrailingStop

    # Position tracking
    pos = np.zeros(len(df))
    for i in range(1, len(df)):
        if close_vals[i-1] < xATRTrailingStop[i-1] and close_vals[i] > xATRTrailingStop[i-1]:
            pos[i] = 1
        elif close_vals[i-1] > xATRTrailingStop[i-1] and close_vals[i] < xATRTrailingStop[i-1]:
            pos[i] = -1
        else:
            pos[i] = pos[i-1]

    df['pos'] = pos

    # crossover (ema 1 of close is just close)
    df['above'] = (df['close'] > df['xATRTrailingStop']) & (df['close'].shift(1) <= df['xATRTrailingStop'].shift(1))
    df['below'] = (df['close'] < df['xATRTrailingStop']) & (df['close'].shift(1) >= df['xATRTrailingStop'].shift(1))

    df['buy'] = (df['close'] > df['xATRTrailingStop']) & df['above']
    df['sell'] = (df['close'] < df['xATRTrailingStop']) & df['below']

    return df

class Backtester:
    def __init__(self, df, exit_candles=3):
        self.df = df
        self.exit_candles = exit_candles

    def run(self):
        df = self.df.reset_index(drop=True)
        n = len(df)
        limit = n - self.exit_candles - 1

        # Identify signals
        buy_mask = df['buy'].values[:limit]
        sell_mask = df['sell'].values[:limit]

        # To handle both buy and sell signals at the same index (if any, though strategy usually prevents it)
        # and maintain priority/order, we can use a more robust vectorized approach

        # Original logic prioritized buy over sell at the same index:
        # if buy: ... elif sell: ...

        combined_mask = buy_mask | sell_mask
        signal_indices = np.where(combined_mask)[0]

        if len(signal_indices) == 0:
            return pd.DataFrame()

        # Determine type for each signal index
        is_buy = df['buy'].values[signal_indices]
        # For indices where both are true, buy wins (simulating the original elif)
        types = np.where(is_buy, 'buy', 'sell')

        entry_indices = signal_indices + 1
        exit_indices = signal_indices + self.exit_candles

        # Extract data in bulk
        epochs = df['epoch'].values
        opens = df['open'].values
        closes = df['close'].values

        res = pd.DataFrame({
            'type': types,
            'entry_time': epochs[entry_indices],
            'entry_price': opens[entry_indices],
            'exit_time': epochs[exit_indices],
            'exit_price': closes[exit_indices]
        })

        # Calculate profit based on type
        res['profit'] = np.where(
            res['type'] == 'buy',
            res['exit_price'] - res['entry_price'],
            res['entry_price'] - res['exit_price']
        )

        res['win'] = res['profit'] > 0
        return res

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
    if len(wins_series) == 0:
        return 0

    # Convert to boolean numpy array
    wins = np.array(wins_series)

    # We want to find streaks of False (losses)
    # prepend/append True to identify starts and ends of loss streaks
    is_loss = ~wins
    is_loss_extended = np.concatenate([[False], is_loss, [False]])

    # Find where streaks start and end
    idx = np.where(np.diff(is_loss_extended.astype(int)))[0]

    # Streaks are pairs of (start, end)
    # streak_lengths = ends - starts
    if len(idx) < 2:
        return 0

    streak_lengths = idx[1::2] - idx[::2]
    return int(np.max(streak_lengths)) if len(streak_lengths) > 0 else 0

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
        stake = max(stake, 0.35)

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
