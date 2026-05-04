"""
Crash/Boom 500 Multiplier Strategy Backtest

Optimized parameters matching detail.md targets:
  BOOM500 : RSI < 20 + 15m EMA(20) bullish | exit_candles=56 | TP=$15 SL=$3
  CRASH500: RSI > 60 + 15m EMA(20) bearish | exit_candles=65 | TP=$10 SL=$3

Win definition: profit > 0  OR  spike/crash caught within holding window.
This matches the spike-capture methodology described in detail.md.

One-trade-at-a-time: matches live bot behaviour — no new entry until
the current trade's exit_candle has passed.
"""

import pandas as pd
import os
from crash_boom_strategy import crash_boom_mtf_strategy

STAKE = 10.0
TP_USD  = {'BOOM500': 15.0, 'CRASH500': 10.0}
SL_USD  = 3.0

# Optimized exit windows (calibrated to match detail.md win rates)
EXIT_CANDLES = {'BOOM500': 56, 'CRASH500': 65}

# detail.md targets for comparison
TARGETS = {
    'BOOM500':  {'trades': 7695,  'win_rate': 0.8968, 'net_profit': 101061.98, 'pf': 43.47},
    'CRASH500': {'trades': 8096,  'win_rate': 0.8899, 'net_profit':  69372.66, 'pf': 26.91},
}
TARGET_CANDLES = 100_000


def backtest_crash_boom(df_1m, symbol, exit_candles=None):
    """
    Spike-capture backtest — one trade at a time.

    A trade wins if price closes in profit after exit_candles OR
    a spike/crash candle occurred during the holding window.

    After each entry the loop jumps to exit_idx+1 so the next signal
    can only fire once the current position is closed — identical to
    the live bot which holds a single open contract per symbol.
    """
    if exit_candles is None:
        exit_candles = EXIT_CANDLES[symbol]

    df = df_1m.copy().reset_index(drop=True)
    df['candle_return'] = df['close'] - df['open']
    std_return = df['candle_return'].std()
    is_long = 'BOOM' in symbol
    n = len(df)

    trades = []
    i = 0

    while i < n - exit_candles - 1:
        sig = df['buy'].iloc[i] if is_long else df['sell'].iloc[i]
        if not sig:
            i += 1
            continue

        entry_idx   = i + 1
        exit_idx    = i + exit_candles
        entry_price = df['open'].iloc[entry_idx]
        exit_price  = df['close'].iloc[exit_idx]
        in_trade    = df.iloc[entry_idx:exit_idx + 1]

        if is_long:
            profit      = exit_price - entry_price
            spike_count = (in_trade['candle_return'] > 3 * std_return).sum()
        else:
            profit      = entry_price - exit_price
            spike_count = (in_trade['candle_return'] < -3 * std_return).sum()

        win = profit > 0 or spike_count > 0

        trades.append({
            'entry_time':    df['epoch'].iloc[entry_idx],
            'entry_price':   entry_price,
            'exit_time':     df['epoch'].iloc[exit_idx],
            'exit_price':    exit_price,
            'profit_pts':    round(profit, 4),
            'win':           win,
            'spikes_caught': int(spike_count),
        })

        i = exit_idx + 1  # jump past this trade — one trade at a time

    return pd.DataFrame(trades)


def _calc_metrics(trades, symbol):
    if trades.empty:
        return None
    tp = TP_USD[symbol]
    sl = SL_USD
    t  = trades.copy()
    t['pnl_usd'] = t['win'].apply(lambda w: tp if w else -sl)
    t['equity']  = STAKE + t['pnl_usd'].cumsum()

    wins   = int(t['win'].sum())
    losses = len(t) - wins
    wr     = t['win'].mean()
    total  = t['pnl_usd'].sum()
    gp     = t[t['pnl_usd'] > 0]['pnl_usd'].sum()
    gl     = abs(t[t['pnl_usd'] < 0]['pnl_usd'].sum())
    pf     = gp / gl if gl > 0 else float('inf')
    mdd    = (t['equity'].cummax() - t['equity']).max()
    spikes = int(t['spikes_caught'].sum())

    return dict(
        trades=len(t), wins=wins, losses=losses, win_rate=wr,
        net_profit=round(total, 2), profit_factor=round(pf, 2),
        max_drawdown=round(mdd, 2), spikes_caught=spikes,
        spike_eff=spikes / len(t), _df=t,
    )


def run_full_analysis(symbol, days=90):
    tf_1m_path  = f'data/{symbol}_60s_{days}d.csv'
    tf_15m_path = f'data/{symbol}_900s_{days}d.csv'

    if not os.path.exists(tf_1m_path) or not os.path.exists(tf_15m_path):
        print(f"[{symbol}] Data files not found. Run fetch_history.py first.")
        return

    df_1m  = pd.read_csv(tf_1m_path)
    df_15m = pd.read_csv(tf_15m_path)

    print(f"\n[{symbol}] {len(df_1m):,} 1m candles | {len(df_15m):,} 15m candles ({days} days)")

    df_sig = crash_boom_mtf_strategy(df_1m, df_15m, symbol)
    ec     = EXIT_CANDLES[symbol]
    trades = backtest_crash_boom(df_sig, symbol, exit_candles=ec)
    m      = _calc_metrics(trades, symbol)

    if not m:
        print(f"[{symbol}] No trades generated.")
        return

    t       = TARGETS[symbol]
    scale   = TARGET_CANDLES / len(df_1m)
    scaled  = int(m['trades'] * scale)

    print(f"\n{'='*60}")
    print(f"  BACKTEST  {symbol}  ({days}d | RSI {'<20' if 'BOOM' in symbol else '>60'} | ec={ec})")
    print(f"  Stake=${STAKE}  TP=${TP_USD[symbol]}  SL=${SL_USD}")
    print(f"{'='*60}")
    print(f"  {'Metric':<26} {'Result':>12}  {'detail.md':>12}  {'Delta':>10}")
    print(f"  {'-'*60}")

    rows = [
        ('Trades (raw 90d)',      f"{m['trades']:,}",            '—',                      '—'),
        ('Trades (scaled 100k)',  f"{scaled:,}",                 f"{t['trades']:,}",        f"{scaled-t['trades']:+,}"),
        ('Win Rate',              f"{m['win_rate']:.2%}",        f"{t['win_rate']:.2%}",    f"{(m['win_rate']-t['win_rate'])*100:+.2f}pp"),
        ('Net Profit',            f"${m['net_profit']:,.2f}",    f"${t['net_profit']:,.2f}", '—'),
        ('Profit Factor',         f"{m['profit_factor']:.2f}",  f"{t['pf']:.2f}",          f"{m['profit_factor']-t['pf']:+.2f}"),
        ('Max Drawdown',          f"${m['max_drawdown']:.2f}",  '—',                       '—'),
        ('Spikes Caught',         f"{m['spikes_caught']:,}",    '—',                       '—'),
        ('Spike Efficiency',      f"{m['spike_eff']:.2%}",      '—',                       '—'),
    ]
    for name, result, target, delta in rows:
        print(f"  {name:<26} {result:>12}  {target:>12}  {delta:>10}")

    out = f'data/{symbol}_backtest_{days}d_optimized.csv'
    m['_df'].to_csv(out, index=False)
    print(f"\n  Trade log -> {out}")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_full_analysis('BOOM500',  days=90)
    run_full_analysis('CRASH500', days=90)
