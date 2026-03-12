## 2025-05-15 - [Optimization of Streaming Data Updates in Pandas]
**Learning:** Performing `pd.concat`, `drop_duplicates`, and `sort_values` on every market tick (approx. every 2 seconds) creates a significant CPU bottleneck due to constant memory allocation and data copying. In-place updates using `.at` for the building candle reduce complexity from $O(N \log N)$ to $O(1)$ for the vast majority of updates.
**Action:** Use in-place row updates for real-time data streams and only perform expensive merge/sort operations when a new candle is detected or when out-of-order data is received.
