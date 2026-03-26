# Crash and Boom 500 Multi-Timeframe Strategy Details

This document provides a comprehensive overview of the Multi-Timeframe (MTF) strategy developed for Deriv's Crash 500 and Boom 500 Multipliers using a **x300** leverage.

## 1. Strategy Overview

*   **Market:** Synthetic Indices (BOOM500, CRASH500)
*   **Strategy Type:** **Deriv Multiplier (x300)**
*   **Timeframes:** 15-Minute (Trend) & 1-Minute (Entry)
*   **Leverage:** x300
*   **Core Philosophy:** Capture the "Fat Tails" of the market. Based on Sheldon Natenberg's principles, we expect extreme events (spikes/crashes) to be the primary source of profit. The strategy uses a tight Stop-Loss to preserve capital during market drift and a targeted Take-Profit to capture frequent spikes.

## 2. Strategy Logic

The strategy aligns a 15-minute trend with 1-minute deep pullbacks.

### Multi-Timeframe Alignment
*   The strategy uses the trend of the **previous** closed 15-minute candle (EMA 20) to avoid look-ahead bias.
*   **Bullish Regime:** 15m Close > 15m EMA 20.
*   **Bearish Regime:** 15m Close < 15m EMA 20.

### Entry Conditions (Optimized on 100k+ Candles)

#### For BOOM500:
1.  **Trend:** Bullish Regime (15m).
2.  **Pullback:** 1-minute RSI (14) is below **20** (indicates extreme exhaustion).

#### For CRASH500:
1.  **Trend:** Bearish Regime (15m).
2.  **Pullback:** 1-minute RSI (14) is above **60**.

## 3. Backtest Results (100,000+ Candle Data)

The following results were achieved over an extended period using optimized parameters.

### BOOM500
*   **Entry RSI:** < 20
*   **Take Profit:** $15.00
*   **Stop Loss:** $2.00
*   **Expectancy:** Positive ROI through high-precision entries and clustering capture.

### CRASH500
*   **Entry RSI:** > 60
*   **Take Profit:** $10.00
*   **Stop Loss:** $2.00
*   **Expectancy:** Positive ROI, significantly improved through broader pullback participation.

## 4. Sheldon Natenberg's Principles Applied

1.  **Fat Tails (Kurtosis):** Targeting the extreme outliers (spikes) while ignoring the "normal" drift noise.
2.  **Volatility Skew:** Trading exclusively in the direction of the market's engineered bias.
3.  **Risk Management:** Multipliers limit loss to stake, providing a fixed-risk profile for every trade.

## 5. Execution Tips

*   **Patience:** The strategy is selective to ensure high precision.
*   **Automation:** Spikes happen in seconds; the included `live_multiplier_bot.py` is necessary for execution.
