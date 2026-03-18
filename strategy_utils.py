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
        """Vectorized execution of backtest logic for performance."""
        df = self.df.reset_index(drop=True)
        n = len(df)
        if n <= self.exit_candles + 1:
            return pd.DataFrame()

        # Extract values for fast NumPy processing
        buys = df['buy'].values[:-self.exit_candles - 1]
        sells = df['sell'].values[:-self.exit_candles - 1]
        opens = df['open'].values
        closes = df['close'].values
        epochs = df['epoch'].values

        # Find signal indices
        buy_indices = np.where(buys)[0]
        sell_indices = np.where(sells)[0]

        all_trades = []

        if len(buy_indices) > 0:
            entry_indices = buy_indices + 1
            exit_indices = buy_indices + self.exit_candles
            entry_prices = opens[entry_indices]
            exit_prices = closes[exit_indices]
            profits = exit_prices - entry_prices
            wins = profits > 0

            all_trades.append(pd.DataFrame({
                'type': 'buy',
                'entry_time': epochs[entry_indices],
                'entry_price': entry_prices,
                'exit_time': epochs[exit_indices],
                'exit_price': exit_prices,
                'profit': profits,
                'win': wins
            }))

        if len(sell_indices) > 0:
            entry_indices = sell_indices + 1
            exit_indices = sell_indices + self.exit_candles
            entry_prices = opens[entry_indices]
            exit_prices = closes[exit_indices]
            profits = entry_prices - exit_prices
            wins = profits > 0

            all_trades.append(pd.DataFrame({
                'type': 'sell',
                'entry_time': epochs[entry_indices],
                'entry_price': entry_prices,
                'exit_time': epochs[exit_indices],
                'exit_price': exit_prices,
                'profit': profits,
                'win': wins
            }))

        if not all_trades:
            return pd.DataFrame()

        # Combine results and maintain chronological order
        # Backtester behavior: only one trade can be active at a time.
        # But wait, original code:
        # for i in range(len(df) - self.exit_candles - 1):
        #     if df['buy'].iloc[i]:
        #         ...
        #     elif df['sell'].iloc[i]:
        #         ...
        #
        # It processes every candle. If both 'buy' and 'sell' are true, it takes buy.
        # But it doesn't have an 'active_trade' flag to skip candles.
        # So multiple trades CAN overlap in the original code.

        # Let's re-examine original:
        # for i in range(len(df) - self.exit_candles - 1):
        #    if df['buy'].iloc[i]:
        #        ...
        #    elif df['sell'].iloc[i]:
        #        ...
        # This means for a given index 'i', it can ONLY be a buy OR a sell, but NOT both.
        # My vectorized version handles them separately then concats.
        # If df['buy'][i] and df['sell'][i] are both True, original only takes buy.

        combined = pd.concat(all_trades).sort_values(['entry_time', 'type'], ascending=[True, True])
        # If multiple signals at same entry_time (which means same signal index i),
        # keep only the one that original would have taken (buy has priority in if/elif)
        return combined.drop_duplicates(subset=['entry_time'], keep='first').reset_index(drop=True)

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
    """Vectorized calculation of maximum consecutive losses using NumPy."""
    if len(wins_series) == 0:
        return 0

    # Convert to NumPy array and identify losses
    losses = ~np.array(wins_series).astype(bool)
    if not np.any(losses):
        return 0

    # Identify sequences of True values
    padded = np.pad(losses, (1, 1), mode='constant')
    diff = np.diff(padded.astype(int))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]

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
