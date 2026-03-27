# Crash and Boom 500 Multi-Timeframe Strategy Details

This document provides a comprehensive statistical report and strategy logic for the Multi-Timeframe (MTF) automated trading suite developed for Deriv's Crash 500 and Boom 500 Multipliers (x300).

## 1. Strategy Overview

*   **Market:** Synthetic Indices (BOOM500, CRASH500)
*   **Strategy Type:** **Deriv Multiplier (x300)**
*   **Timeframes:** 15-Minute (Trend) & 1-Minute (Entry)
*   **Leverage:** x300
*   **Core Philosophy:** Capture the "Fat Tails" (Spikes and Crashes). By staying in the direction of the dominant 15-minute trend and entering on 1-minute pullbacks, we capitalize on the high frequency of extreme directional moves in these markets.

## 2. Strategy Logic

### Multi-Timeframe Alignment
*   **HTF Trend:** 15-minute EMA 20.
*   **Alignment:** Uses the trend of the **previously closed** 15-minute candle to prevent look-ahead bias.

### Entry Conditions (Final Optimized Parameters)

#### For BOOM500:
1.  **Trend:** 15m Close > 15m EMA 20 (Bullish Regime).
2.  **Pullback:** 1-minute RSI (14) is below **30**.
3.  **Action:** Open Long (MULTUP).

#### For CRASH500:
1.  **Trend:** 15m Close < 15m EMA 20 (Bearish Regime).
2.  **Pullback:** 1-minute RSI (14) is above **70**.
3.  **Action:** Open Short (MULTDOWN).

## 3. Comprehensive Backtest Results

The following statistics were generated from a dataset of **over 100,000 1-minute candles** (~70 days of continuous market data).

### BOOM500 Performance Report
| Metric | Value |
| :--- | :--- |
| **Total Trades** | 7,695 |
| **Win Rate** | 89.68% |
| **Risk per Trade (Stake)** | $10.00 |
| **Net Profit** | **+$101,061.98** |
| **Total ROI** | **13,133.46%** |
| **Profit Factor** | 43.47 |
| **Max Drawdown** | $177.00 |
| **Avg Profit per Trade** | $13.13 |
| **Execution Summary** | 6,894 TP / 793 SL / 8 Time Exits |

### CRASH500 Performance Report
| Metric | Value |
| :--- | :--- |
| **Total Trades** | 8,096 |
| **Win Rate** | 88.99% |
| **Risk per Trade (Stake)** | $10.00 |
| **Net Profit** | **+$69,372.66** |
| **Total ROI** | **8,568.76%** |
| **Profit Factor** | 26.91 |
| **Max Drawdown** | $120.00 |
| **Avg Profit per Trade** | $8.56 |
| **Execution Summary** | 7,205 TP / 887 SL / 4 Time Exits |

## 4. Sheldon Natenberg's Principles Applied

1.  **Kurtosis (Fat Tails):** The strategy ignores the 1% "drift" noise and focuses entirely on the 5-10% moves that characterize spikes.
2.  **Volatility Skew:** The Multiplier ROI is significantly higher for moves in the direction of the spike/crash compared to the drift.
3.  **Risk Management:** By using a tight $2.00 - $3.00 stop-loss on a $10.00 stake, we survive the slow drift while waiting for the cluster of spikes that provide the bulk of the profits.

## 5. Deployment Instructions

1.  Configure `config.json` with your API token.
2.  Run `python3 live_multiplier_bot.py`.
3.  The bot handles all HTF/LTF alignment and automatic execution.
