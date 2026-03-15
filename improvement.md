# Strategy & ML Improvement Roadmap ⚡

This document outlines research-backed improvements to enhance the performance and reliability of the QuantumTrade bot, specifically addressing the win-rate drop observed after moving to fair out-of-sample testing.

## 1. Machine Learning Refinement (Neural Filter)

The current Random Forest model uses a basic set of features. To improve predictive power, we should focus on **Feature Engineering** and **Model Architecture**.

### A. Advanced Feature Engineering
- **Lagged Features**: Add price changes and indicator values from the previous 3-5 candles. Markets have memory, and the momentum of the last few periods is highly predictive.
- **Relative Strength Index (RSI) Slope**: Instead of raw RSI, use the slope of RSI over the last 3 candles to capture accelerating momentum.
- **Volatility Ratios**: Include the ratio of current ATR to a longer-term average ATR. Signals in extreme volatility regimes often behave differently.
- **RSI Divergence**: Create a boolean feature for RSI divergence (price makes a lower low, but RSI makes a higher low).
- **Time-Series Decomposition**: Add Trend, Seasonality, and Residual components derived from the price action.
- **Candlestick Patterns**: Encode common patterns like Engulfing, Hammers, or Dojis as numerical features.

### B. Alternative Algorithms
- **XGBoost / LightGBM**: These gradient-boosting algorithms often outperform Random Forest in financial time-series because they minimize residual errors more aggressively.
- **LSTM (Long Short-Term Memory)**: For a truly "Neural" approach, an LSTM network can capture temporal dependencies that tree-based models might miss.

### C. Training Optimizations
- **Probability Thresholding**: Instead of just using the model's `predict()` (0 or 1), use `predict_proba()`. Only take trades where the win probability is > 65%. This significantly reduces trade frequency but boosts win-rate.
- **Class Imbalance**: Use SMOTE (Synthetic Minority Over-sampling Technique) or adjust class weights if the "Win" signals are significantly fewer than "Loss" signals.
- **Hyperparameter Tuning**: Implement a GridSearch or Bayesian Optimization to find the optimal `n_estimators`, `max_depth`, and `min_samples_leaf`.
- **Walk-Forward Analysis**: Instead of a single split, use a rolling window training approach where the model is retrained every X days to adapt to changing market "regimes".

---

## 2. Core Strategy Optimization (UT Bot Alerts)

The UT Bot Alerts strategy is a trend-following system. It excels in trending markets but can be "chopped up" in ranging markets.

### A. Multi-Timeframe Confirmation (MTF)
- **Trend Alignment**: Only take a BUY signal on the 5-minute chart if the 15-minute or 1-hour trend (e.g., EMA 200) is also pointing UP.
- **Anchor Chart**: Use a higher timeframe to determine the "Market Regime" (Trending vs. Ranging) and adjust the UT Bot sensitivity (`a` and `c` parameters) dynamically.

### B. Advanced Filtering
- **Volume Profile**: Use tick volume to verify moves. A breakout on low volume is more likely to be a fake-out.
- **VIX/Volatility Filter**: Disable the bot or tighten the Neural Filter during periods of extreme market-wide volatility (VIX spikes).
- **Spread/Cost Awareness**: Ensure signals are only taken if the expected profit significantly outweighs the broker spread/commission.

---

## 3. Execution & Risk Management

### A. Dynamic Exit Strategy
- Instead of a fixed 3-candle duration (15m), implement a trailing stop-loss based on the UT Bot's own ATR trailing stop line.
- **Time-based Exit**: If the trade is still flat after 5 candles, close it manually to free up capital.

### B. Fractional Kelly Criterion
- Use a conservative version of the Kelly Criterion to calculate position size based on the ML model's confidence score (probability of win).

---

## 4. Key Learnings from Research (Random Forest & Indicators)

### A. Random Forest specific optimizations (Video: Sm03GTT6OOw)
- **Feature Importance**: Use the model's `feature_importances_` attribute to prune noise. If a feature (like a specific lag) has near-zero importance, remove it to reduce overfitting.
- **Triple Barrier Labeling**: Instead of simple Win/Loss based on a fixed time exit, use a "Triple Barrier" (Stop Loss, Take Profit, or Time Out). This provides the ML model with more meaningful "Why" behind a trade's success.
- **Bootstrapping**: Ensure the Random Forest is utilizing its "Bagging" (Bootstrap Aggregating) nature correctly by having a large enough number of trees (100-500).

### B. High-Probability Signal Indicators (Video: R9XoCwRhmXw)
- **Consensus Momentum**: The "best" indicators often aren't complex. Simple crossovers (e.g., EMA 9/21) combined with a high-timeframe trend filter yield the highest signal-to-noise ratio.
- **Volume Climax**: Identifying volume "exhaustion" at the end of a trend can prevent taking signals that are likely to reverse immediately.

---

## 5. Immediate Action Items for v3.0
1. **[ML]** Add 3 lags of RSI and MACD to the feature set.
2. **[ML]** Implement `predict_proba()` thresholding (only execute if prob > 0.65).
3. **[Strategy]** Implement a 15-minute Trend Filter (EMA 100/200) for trend alignment.
4. **[Backtest]** Implement Walk-Forward Cross-Validation to ensure stability across different years.
5. **[Data]** Fetch and include Tick Volume as a feature for the ML model.
