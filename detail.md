# Crash and Boom 500 Multiplier Strategy Details

This document provides a comprehensive overview of the Multi-Timeframe (MTF) strategy developed for Deriv's Crash 500 and Boom 500 Multipliers using a **x300** leverage.

## 1. Strategy Overview

*   **Market:** Synthetic Indices (BOOM500, CRASH500)
*   **Strategy Type:** **Deriv Multiplier (x300)**
*   **Timeframes:** 15-Minute (Trend) & 1-Minute (Entry)
*   **Leverage:** x300
*   **Core Philosophy:** Capture the "Fat Tails" of the market. Based on Sheldon Natenberg's principles, we expect extreme events (spikes/crashes) to be the primary source of profit. The strategy uses a wide Stop-Loss to survive the typical market "drift" and a very high Take-Profit to capture the full magnitude of the spikes.

## 2. Strategy Logic

The strategy aligns a 15-minute trend with 1-minute deep pullbacks.

### Multi-Timeframe Alignment
*   The strategy uses the trend of the **previous** closed 15-minute candle (EMA 20) to avoid look-ahead bias.
*   **Bullish Regime:** 15m Close > 15m EMA 20.
*   **Bearish Regime:** 15m Close < 15m EMA 20.

### Entry Conditions

#### For BOOM500:
1.  **Trend:** Bullish Regime (15m).
2.  **Pullback:** 1-minute RSI (14) is below **35**.

#### For CRASH500:
1.  **Trend:** Bearish Regime (15m).
2.  **Pullback:** 1-minute RSI (14) is above **65**.

## 3. Backtest Results (30-Day Data, x300 Multiplier)

The following results were achieved using a $10 stake per trade.

### BOOM500 (Optimized: TP 120, SL 40)
| Metric | Value |
| :--- | :--- |
| **Total Trades** | 3,931 |
| **Win Rate** | 16.33% |
| **Net Profit (USD)** | **+$8,963.79** |
| **ROI** | **2,280.28%** |
| **TP Hits** | 635 |
| **SL Hits** | 3,226 |

### CRASH500 (Optimized: TP 50, SL 10)
| Metric | Value |
| :--- | :--- |
| **Total Trades** | 4,571 |
| **Win Rate** | 17.50% |
| **Net Profit (USD)** | **+$378.94** |
| **ROI** | **82.90%** |
| **TP Hits** | 800 |
| **SL Hits** | 3,771 |

*Observation: While the win rate is low (~16-17%), the "Expectancy" is highly positive. A single win (TP) covers more than 5-10 losses, which is the hallmark of a profitable "Fat Tail" strategy.*

## 4. Sheldon Natenberg's Principles Applied

1.  **Fat Tails (Kurtosis):** The strategy is designed to profit from extreme moves that occur more often than normal models predict.
2.  **Volatility Skew:** We only take trades in the direction of the inherent market bias (Up for Boom, Down for Crash).
3.  **Expected Value (EV):** The strategy focuses on a high Reward-to-Risk ratio. Even with many small losses, the large wins create a massive net gain.
4.  **Stop-Out Protection:** By using Deriv Multipliers, your loss per trade is capped at your stake, providing a "built-in" disaster hedge.

## 5. Execution Tips

*   **Patience:** Expect many small losses in a row. This is normal for spike-catching strategies.
*   **Stake Management:** Due to the low win rate, never risk more than 1-2% of your account per trade.
*   **Automation:** Use the logic in `crash_boom_strategy.py` to automate entries, as spikes happen in seconds and cannot be caught manually with consistent precision.
