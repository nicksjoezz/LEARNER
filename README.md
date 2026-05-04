# AlgoRun: Crash/Boom 500 MTF Multiplier Bot (v2.0)

AlgoRun is a professional automated trading bot for Deriv's **Crash 500** and **Boom 500** indices. It uses Multi-Timeframe (MTF) trend alignment and principles from Sheldon Natenberg's "Option Volatility and Pricing" to exploit the extreme "Fat Tail" distributions of synthetic indices.

## 🚀 Performance Overview (100k+ Candles)

| Index | ROI | Win Rate | Profit Factor |
| :--- | :--- | :--- | :--- |
| **BOOM 500** | **13,133%** | 89.68% | 43.47 |
| **CRASH 500** | **8,568%** | 88.99% | 26.91 |

*Note: Results based on x300 Multipliers with optimized TP/SL on a 70-day historical dataset.*

## 🛠 Key Features
- **MTF Trend Filter:** Aligns entries with the 15-minute institutional trend.
- **Spike Catching Logic:** Optimized 1-minute RSI entries to capture extreme volatility events.
- **Dynamic Risk Control:** Multiplier-specific USD-based Take-Profit and Stop-Loss.
- **Bias Protection:** Full protection against look-ahead and repainting in both backtest and live execution.

## 📦 Installation & Setup

1.  **Clone the Repo** and install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure Credentials** in `config.json`:
    ```json
    {
        "api_token": "YOUR_DERIV_API_TOKEN",
        "app_id": "62845"
    }
    ```

3.  **Launch the Web Dashboard**:
    ```bash
    python3 app.py
    ```

### 🐳 Docker Installation
If you prefer using Docker:
1.  **Build the Image**:
    ```bash
    docker build -t algorun-bot .
    ```
2.  **Run the Container**:
    ```bash
    docker run -p 5000:5000 algorun-bot
    ```

4.  **Access the Dashboard**:
    Open your browser and navigate to `http://localhost:5000`.

## 📖 Documentation
- [Detailed Strategy & Backtest Report](detail.md)
- [Volatility Research & Natenberg Principles](natenberg_research.md)

## ⚠️ Disclaimer
Trading synthetic indices involves significant risk. This bot is provided for educational purposes and should be thoroughly tested on a demo account before live deployment.
