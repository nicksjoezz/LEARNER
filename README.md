# AlgoRun: Crash/Boom 500 MTF Multiplier Bot

AlgoRun is a professional-grade automated trading suite for Deriv's Crash 500 and Boom 500 indices. It uses Multi-Timeframe (MTF) analysis and principles from Sheldon Natenberg's "Option Volatility and Pricing" to capture extreme price spikes and crashes using Multipliers (x300).

## Features
- **Multi-Timeframe Logic:** Aligns 15-minute trends with 1-minute entries.
- **Fat-Tail Strategy:** Explicitly targets extreme events (spikes) while managing drift risk.
- **Look-Ahead Protection:** Uses historical candle closure data to prevent backtest and live trading bias.
- **Automated Execution:** Asynchronous bot for real-time trade placement with TP/SL.
- **Optimized Parameters:** Strategies fine-tuned on over 100,000 historical candles.

## Getting Started

### 1. Prerequisites
- Python 3.8+
- Deriv API Token (Demo account recommended for initial testing)

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Configuration
Update `config.json` with your Deriv credentials:
```json
{
    "api_token": "YOUR_DERIV_TOKEN",
    "app_id": "62845"
}
```

### 4. Running the Bot
```bash
python3 live_multiplier_bot.py
```

## Strategy Details
For in-depth analysis and strategy logic, see `detail.md`.
For research on volatility principles, see `natenberg_research.md`.

## Disclaimer
Trading involves risk. This bot is for educational and demo purposes. Always test strategies in a risk-controlled environment.
