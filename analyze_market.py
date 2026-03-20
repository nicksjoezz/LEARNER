import pandas as pd
import numpy as np
import os

def analyze_spikes(filepath, symbol, threshold_factor=3):
    df = pd.read_csv(filepath)
    df['returns'] = df['close'] - df['open']

    # Calculate average return and standard deviation
    avg_return = df['returns'].mean()
    std_return = df['returns'].std()

    if 'BOOM' in symbol:
        # Spikes are upward
        spikes = df[df['returns'] > threshold_factor * std_return]
        normal = df[df['returns'] <= threshold_factor * std_return]
    else:
        # Crashes are downward
        spikes = df[df['returns'] < -threshold_factor * std_return]
        normal = df[df['returns'] >= -threshold_factor * std_return]

    print(f"\n--- Analysis for {symbol} ({filepath}) ---")
    print(f"Total Candles: {len(df)}")
    print(f"Total Spikes/Crashes (>{threshold_factor} std): {len(spikes)}")
    print(f"Average Return (Normal): {normal['returns'].mean():.4f}")
    print(f"Average Return (Spike): {spikes['returns'].mean():.4f}")
    print(f"Spike Frequency: {len(spikes)/len(df):.2%}")

    # Volatility Clustering check (if a spike happened in the last 15 candles)
    df['is_spike'] = False
    if 'BOOM' in symbol:
        df.loc[df['returns'] > threshold_factor * std_return, 'is_spike'] = True
    else:
        df.loc[df['returns'] < -threshold_factor * std_return, 'is_spike'] = True

    df['spike_in_last_15'] = df['is_spike'].shift(1).rolling(window=15).max() > 0

    prob_after_spike = df[df['spike_in_last_15'] == True]['is_spike'].mean()
    prob_overall = df['is_spike'].mean()

    print(f"Probability of spike (Overall): {prob_overall:.2%}")
    print(f"Probability of spike (Given spike in last 15 mins): {prob_after_spike:.2%}")
    print(f"Volatility Clustering Factor: {prob_after_spike/prob_overall:.2f}x")

if __name__ == "__main__":
    analyze_spikes('data/BOOM500_60s_30d.csv', 'BOOM500')
    analyze_spikes('data/CRASH500_60s_30d.csv', 'CRASH500')
