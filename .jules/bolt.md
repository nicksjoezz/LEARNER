## 2025-05-15 - [Vectorized Backtester and MCL]
**Learning:** The iterative row-by-row processing in the backtest engine was the primary bottleneck, taking ~7.2s for 100k rows. Vectorization with NumPy reduced this to ~0.006s (1200x speedup). Similarly, `calculate_max_consecutive_losses` was optimized with NumPy for a 10x speedup.
**Action:** Always prioritize NumPy vectorization over Python loops for financial trade analysis and indicator calculations.

## 2025-05-15 - [WebSocket Stability]
**Learning:** WebSocket connections to trading APIs (like Deriv) can silently drop if no data is sent/received for a period.
**Action:** Implement a mandatory 30-second heartbeat ping in OHLC subscription loops to maintain connection persistence.
