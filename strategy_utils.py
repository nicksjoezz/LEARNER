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

    # ema 1 of src
    df['ema1'] = ta.trend.ema_indicator(df['close'], window=1)

    # crossover
    df['above'] = (df['ema1'] > df['xATRTrailingStop']) & (df['ema1'].shift(1) <= df['xATRTrailingStop'].shift(1))
    df['below'] = (df['ema1'] < df['xATRTrailingStop']) & (df['ema1'].shift(1) >= df['xATRTrailingStop'].shift(1))

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

        if n <= self.exit_candles + 1:
            return pd.DataFrame()

        # Vectorized signal detection with 'elif' logic (buy has priority)
        mask_buy = df['buy'].values[:-(self.exit_candles + 1)]
        mask_sell = df['sell'].values[:-(self.exit_candles + 1)] & (~mask_buy)

        buy_indices = np.where(mask_buy)[0]
        sell_indices = np.where(mask_sell)[0]

        trades = []

        if len(buy_indices) > 0:
            entry_indices = buy_indices + 1
            exit_indices = buy_indices + self.exit_candles

            buy_trades = pd.DataFrame({
                'type': 'buy',
                'entry_time': df['epoch'].values[entry_indices],
                'entry_price': df['open'].values[entry_indices],
                'exit_time': df['epoch'].values[exit_indices],
                'exit_price': df['close'].values[exit_indices],
                'profit': df['close'].values[exit_indices] - df['open'].values[entry_indices],
                'win': (df['close'].values[exit_indices] - df['open'].values[entry_indices]) > 0
            })
            trades.append(buy_trades)

        if len(sell_indices) > 0:
            entry_indices = sell_indices + 1
            exit_indices = sell_indices + self.exit_candles

            sell_trades = pd.DataFrame({
                'type': 'sell',
                'entry_time': df['epoch'].values[entry_indices],
                'entry_price': df['open'].values[entry_indices],
                'exit_time': df['epoch'].values[exit_indices],
                'exit_price': df['close'].values[exit_indices],
                'profit': df['open'].values[entry_indices] - df['close'].values[exit_indices],
                'win': (df['open'].values[entry_indices] - df['close'].values[exit_indices]) > 0
            })
            trades.append(sell_trades)

        if not trades:
            return pd.DataFrame()

        # Sort by entry_time to maintain chronological order as in original loop
        return pd.concat(trades).sort_values('entry_time').reset_index(drop=True)

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

    # Vectorized approach to find max consecutive losses (False values)
    wins = np.array(wins_series, dtype=bool)

    # We want to find streaks of False, so we look at True values as separators
    # Pad with True at both ends to handle streaks at start/end
    is_win = np.concatenate(([True], wins, [True]))

    # Find indices where values change from True to False (start of loss streak) or vice versa
    # diff will be -1 for True->False, 1 for False->True, 0 otherwise
    diffs = np.diff(is_win.astype(int))

    # Indices where a streak of False starts (True -> False)
    starts = np.where(diffs == -1)[0]
    # Indices where a streak of False ends (False -> True)
    ends = np.where(diffs == 1)[0]

    if len(starts) == 0:
        return 0

    # The length of each streak is end_index - start_index
    return int(np.max(ends - starts))

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
