## 2025-05-22 - Unsafe Positional Assignment in Pandas
**Learning:** Using `df.iloc[-1] = list` is dangerous because it assumes a specific column order. When DataFrames are created from JSON/API responses, column order is not guaranteed.
**Action:** Always use label-based assignment with `.loc` and explicit column names when performing in-place row updates in Pandas.
