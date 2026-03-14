## 2025-05-15 - [Backtester Bottleneck]
**Learning:** The `Backtester.run` method used a Python loop to iterate over 100,000+ rows of OHLC data, which is extremely slow in Python (~7.6s).
**Action:** Vectorized the backtester using NumPy indexing to identify signals and calculate trade results, reducing execution time to ~0.007s (a 1000x speedup). This pattern should be applied to any time-series analysis in the bot.
