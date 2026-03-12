## 2025-03-12 - [Pandas Tick Update Optimization]
**Learning:** Performing `pd.concat`, `drop_duplicates`, and `sort_values` on every price tick (multiple times per second) is extremely CPU-intensive and can block the event loop in high-frequency trading scenarios.
**Action:** Use in-place updates for the current candle and simple appends for new candles to maintain a rolling window of historical data.

## 2025-03-12 - [Deriv API Connection Resilience]
**Learning:** Concurrent start/stop calls and rapid reconnections to the Deriv API via `python-deriv-api` can lead to "Bad file descriptor" errors and race conditions.
**Action:** Implement an `asyncio.Lock` to serialize initialization and connection attempts, and ensure explicit cleanup of existing connections before establishing new ones.
