## 2024-05-15 - [Vectorized Backtester & Threaded Live Trading]
**Learning:** Manual row-by-row iteration in backtesting and signal processing was a major performance bottleneck, causing 100k rows to take ~7s. This blocking behavior was also likely responsible for WebSocket disconnections in live trading.
**Action:** Use NumPy vectorization for backtests (~1000x speedup) and `asyncio.to_thread` for CPU-bound signal checks in live trading to keep the event loop responsive.
