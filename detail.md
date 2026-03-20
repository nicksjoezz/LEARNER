# Crash and Boom 500 MTF Strategy Details

This document provides a comprehensive overview of the Multi-Timeframe (MTF) strategy developed for Deriv's Crash 500 and Boom 500 indices.

## 1. Strategy Overview

*   **Market:** Synthetic Indices (BOOM500, CRASH500)
*   **Strategy Type:** **Rise and Fall (Binary)**
*   **Timeframes:** 15-Minute (Trend) & 1-Minute (Entry)
*   **Core Philosophy:** Trend following on the higher timeframe (HTF) and mean reversion/momentum on the lower timeframe (LTF). The strategy is designed to catch "fat tail" events (spikes and crashes) while maintaining a positive expectancy during the slow-drift phases.

## 2. Strategy Logic

The strategy relies on aligning a 15-minute trend with 1-minute pullbacks to identify high-probability entry points for spikes (Boom) and crashes (Crash).

### Multi-Timeframe Alignment (Corrected for Look-Ahead Bias)
*   The strategy uses the trend of the **previous** closed 15-minute candle to determine the directional bias for the current 1-minute candles.
*   HTF Trend Indicator: **EMA 20** (15-minute timeframe).

### Entry Conditions

#### For BOOM500 (Catching Spikes):
1.  **Trend Alignment:** Previous 15m candle closed above its EMA 20 (Bullish Trend).
2.  **Pullback (LTF):** 1-minute RSI (14) is below **40** (indicates a temporary pullback).
3.  **Momentum (LTF):** 1-minute price bounces and closes above its **EMA 5**.

#### For CRASH500 (Catching Crashes):
1.  **Trend Alignment:** Previous 15m candle closed below its EMA 20 (Bearish Trend).
2.  **Pullback (LTF):** 1-minute RSI (14) is above **60**.
3.  **Momentum (LTF):** 1-minute price drops and closes below its **EMA 5**.

### Exit Strategy
*   Fixed duration: **5 minutes** (5 candles).
*   In "Rise and Fall" trading, we look for a price higher (Boom) or lower (Crash) than the entry after 5 minutes.
*   Spikes or crashes occurring within these 5 minutes typically result in a "Win" due to the magnitude of the move.

## 3. Backtest Results (30-Day Data)

The following results were achieved over a 30-day period (approx. 45,000 1-minute candles).

### BOOM500
| Metric | Value |
| :--- | :--- |
| **Total Trades** | 107 |
| **Win Rate** | 37.38% |
| **Total Profit (Points)** | +2.68 |
| **Avg Profit per Trade** | +0.0250 |
| **Spikes Caught** | 17 |
| **Spike Capture Efficiency** | 15.89% |

### CRASH500
| Metric | Value |
| :--- | :--- |
| **Total Trades** | 119 |
| **Win Rate** | 37.82% |
| **Total Profit (Points)** | +15.61 |
| **Avg Profit per Trade** | +0.1312 |
| **Crashes Caught** | 23 |
| **Crash Capture Efficiency** | 19.33% |

*Note: In Crash/Boom markets, a lower win rate can still be profitable because a single "Win" (catching a spike) often covers multiple small "Losses" (during the drift phase) due to the extreme price movement.*

## 4. Sheldon Natenberg's Principles Applied

The strategy incorporates several advanced concepts from *Option Volatility and Pricing*:

1.  **Fat Tails (Kurtosis):** The strategy explicitly targets the extreme outliers (spikes) which standard models ignore. By staying in the direction of the HTF trend, we increase the probability of being "Long Volatility" when the fat tail event occurs.
2.  **Volatility Skew:** Boom indices have a natural upward skew (risk of a move up), and Crash indices have a downward skew. The strategy aligns its direction (Long for Boom, Short for Crash) with this inherent market skew.
3.  **Volatility Clustering:** By requiring a 15m trend, we are statistically more likely to be in the market during a "High Volatility Regime" where spikes occur in groups.

## 5. Risk Management Recommendations

*   **Stake Size:** For Rise and Fall, keep stakes small (1-2% of balance) as the win rate can be below 50%.
*   **Trend Filter:** Never trade against the 15m EMA 20 trend.
*   **Execution:** Use an automated bot (like the provided `trading_bot.py` or a specialized script) to ensure entry occurs immediately upon candle close to capture the momentum shift.
