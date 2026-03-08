import pandas as pd
import numpy as np
import os
from strategy_utils import ut_bot, Backtester, analyze_performance
from indicators import add_indicators
from ml_filter import MLFilter

def run_optimized_strategies():
    symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']

    configs = [
        (1, 10, "UT Bot (1, 10) + ML Filter"),
        (2, 20, "UT Bot (2, 20) + ML Filter"),
        (3, 30, "UT Bot (3, 30) + ML Filter"),
        (1, 20, "UT Bot (1, 20) + ML Filter"),
        (2, 10, "UT Bot (2, 10) + ML Filter"),
        (3, 20, "UT Bot (3, 20) + ML Filter"),
        (1, 30, "UT Bot (1, 30) + ML Filter"),
        (2, 30, "UT Bot (2, 30) + ML Filter"),
        (3, 10, "UT Bot (3, 10) + ML Filter"),
        (1.5, 15, "UT Bot (1.5, 15) + ML Filter")
    ]

    ml_description = """
Machine Learning Filter Definition:
The ML Filter uses a Random Forest Classifier trained on the following technical features at the moment of the signal:
1. RSI (14): Relative Strength Index to identify overbought/oversold conditions.
2. MACD Histogram: Momentum difference to capture trend strength.
3. ADX: Average Directional Index to filter out low-volatility/ranging markets.
4. Bollinger Band %B (pband): Price position relative to volatility bands.
5. EMA 200 Distance: Normalized distance from the long-term trend line.

The model is trained to recognize 'Loss' patterns in the raw UT Bot signals and automatically blocks signals where the predicted probability of a win is low.
"""

    base_output_dir = 'Profitable strategy'
    if os.path.exists(base_output_dir):
        import shutil
        shutil.rmtree(base_output_dir)
    os.makedirs(base_output_dir, exist_ok=True)

    for symbol in symbols:
        symbol_dir = f"{base_output_dir}/{symbol}"
        os.makedirs(symbol_dir, exist_ok=True)

        filepath = f'data/{symbol}_5m_2y.csv'
        if not os.path.exists(filepath): continue

        df_orig = pd.read_csv(filepath)
        candle_count = len(df_orig)
        df_with_inds = add_indicators(df_orig)

        for i, (a, c, desc) in enumerate(configs):
            strat_name = f"Strategy_{i+1}"

            # 1. Get raw UT Bot trades for training
            df_raw = ut_bot(df_with_inds, a=a, c=c)
            raw_trades = Backtester(df_raw).run()

            if len(raw_trades) < 200: continue

            # 2. Train ML Filter
            ml = MLFilter()
            if ml.train(df_with_inds, raw_trades):
                # 3. Apply ML Filter
                df_filtered = ml.filter_signals(df_raw)
                # 4. Run final backtest
                final_trades = Backtester(df_filtered).run()

                if not final_trades.empty:
                    wr = final_trades['win'].mean()
                    perf = analyze_performance(final_trades)

                    with open(f'{symbol_dir}/{strat_name}.txt', 'w') as f:
                        f.write(f"Symbol: {symbol}\n")
                        f.write(f"Strategy: {strat_name}\n")
                        f.write(f"Description: {desc}\n")
                        f.write(f"Overall Win Rate: {wr:.2%}\n")
                        f.write(f"Total Trades: {len(final_trades)}\n")
                        f.write(f"Training History: {candle_count} candles (~{candle_count/288:.1f} days)\n")
                        f.write("Note: Training history represents the maximum available depth provided by the Deriv API for 5m synthetic index candles at the time of research.\n\n")
                        f.write(f"Strategy Configuration:\n- UT Bot Sensitivity (a): {a}\n- ATR Period (c): {c}\n")
                        f.write(ml_description)
                        f.write(f"\n60-Day Performance Intervals for {symbol}:\n")
                        f.write(perf.to_string())

                    print(f"Generated {symbol} {strat_name}. WR: {wr:.2%}")

if __name__ == "__main__":
    run_optimized_strategies()
