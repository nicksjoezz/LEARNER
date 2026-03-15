import pandas as pd
import numpy as np
from ml_filter import MLFilter
from strategy_utils import ut_bot, Backtester
from indicators import add_indicators

def test_pipeline():
    print("Generating synthetic trend data with time features...")
    n = 20000
    df = pd.DataFrame({
        'epoch': np.arange(n) * 300,
        'open': np.sin(np.linspace(0, 50, n)) * 10 + 100,
        'high': np.sin(np.linspace(0, 50, n)) * 10 + 102,
        'low': np.sin(np.linspace(0, 50, n)) * 10 + 98,
        'close': np.sin(np.linspace(0, 50, n)) * 10 + 101
    })
    df['close'] += np.random.randn(n) * 1.5

    print("Running Restructured Indicators...")
    df = add_indicators(df)

    print("Generating Signals...")
    df_sig = ut_bot(df, a=1.5, c=10)
    trades = Backtester(df_sig).run()

    ml = MLFilter()
    print(f"Training on {len(trades)} trades with engineered features...")
    success = ml.train(df, trades)

    if success:
        print(f"Pipeline Successful. Best Threshold: {ml.best_threshold:.4f}")
        filtered = ml.filter_signals(df_sig)
        orig = df_sig['buy'].sum() + df_sig['sell'].sum()
        filt = filtered['buy'].sum() + filtered['sell'].sum()
        print(f"Original Trades: {orig}, ML Filtered: {filt}")
        print(f"Final Participation: {(filt/orig)*100:.1f}%")
    else:
        print("Training Failed.")

if __name__ == "__main__":
    test_pipeline()
