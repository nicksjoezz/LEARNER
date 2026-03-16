# ⚡ QuantumTrade: The Beginner's Guide to Your Trading Bot

Welcome! If you have zero knowledge of coding or trading, this guide is for you. We’ve designed this bot to do the "heavy lifting" so you don't have to. Here is exactly how it works, from the first click to the final trade.

---

## 🚀 Part 1: The Big Picture (A to Z)

Imagine you have a professional trader who never sleeps, never gets tired, and can remember every single price movement from the last two years. That is this bot.

1.  **Connecting**: The bot connects to your Deriv account using a "Secure Token" (like a digital key).
2.  **Watching**: It watches the "Volatility Indexes" (markets that move 24/7).
3.  **Analyzing**: Every 5 minutes, it looks at the price and calculates "Technical Indicators" (math formulas that show if the price is going up or down).
4.  **Filtering**: This is the "Magic." Before it places a trade, it asks a "Neural Brain" (Machine Learning) if this specific moment looks like a winner or a loser based on the past.
5.  **Trading**: If the "Brain" says "YES," the bot places a Rise (Up) or Fall (Down) trade automatically.

---

## 🧠 Part 2: How the "Neural Brain" (Machine Learning) Works

Think of the Machine Learning (ML) as a **Filter**.

### 1. The Strategy (The "Rough Draft")
First, we have a basic strategy called **UT Bot**. It’s like a scout that looks for basic patterns. But the scout isn't perfect—sometimes it finds a pattern that turns out to be a loss.

### 2. The Training (The "School")
To fix this, the bot goes to "School" every night (Daily Retraining).
*   It looks at **100,000+ past candles**.
*   It finds every time the Scout (UT Bot) gave a signal.
*   It records **everything** about that moment: Was the RSI high? Was it a Monday? Was the market "jittery"?
*   It then looks at the result: **Did that trade win or lose?**

### 3. The XGBoost Model (The "Super Detective")
We use a specific type of AI called **XGBoost**.
*   It builds thousands of "Decision Trees."
*   **Tree 1** might say: "If it's Monday and RSI is above 70, it's usually a loss."
*   **Tree 2** might say: "If the price is far from the average, it might be a win."
*   The AI combines **all** these trees to create a "Probability Score" (0% to 100%).

### 4. The Filtering (The "Final Decision")
When the Scout finds a trade today, the bot sends all the current data to the AI Brain.
*   **AI Brain**: "I've seen this 500 times in the last year. 80% of the time, this pattern led to a loss. **BLOCK THIS TRADE.**"
*   Or: "This pattern is a high-probability winner. **EXECUTE TRADE.**"

### 5. "Fair" Training (No Cheating!)
We use a method called **Walk-Forward Validation**.
Imagine taking an exam. If you've already seen the answers, your high score is a lie. Many trading bots "cheat" by training on the same data they use for backtesting.
**Our bot is different:** When you run a backtest, the AI is trained *only* on data that happened *before* the test started. This gives you a honest, "real-world" result of how the bot would have actually performed.

---

## 📊 Part 3: The "Features" (What the Brain Sees)

The AI doesn't just see "Price." It sees specific details called **Features**:

*   **RSI (The Speedometer)**: Is the price moving too fast?
*   **EMA Alignment (The Trend Stack)**: Are the short-term and long-term trends lined up? (Like gears in a watch).
*   **Bollinger Bands (The Rubber Band)**: Is the price stretched too far and about to snap back?
*   **Candle Streaks**: Have there been many same-color candles in a row?
*   **Market Sessions**: Is it the "London" morning or the "New York" evening?
*   **ATR Percentile**: Is the market more "jittery" than usual for this specific symbol?

---

## 🛠 Part 4: How to Use It

1.  **Settings**: Enter your **API Token** and **App ID** in the "Control Center."
2.  **Strategy Lab**: Use the "Backtest" button to simulate the past. It will show you how the bot *would* have performed.
    *   **Raw Data**: How the "Scout" did alone.
    *   **Neural Opt**: How much better it did after the **AI Filtered** the bad trades.
3.  **Engage**: Click **"ENGAGE BOT"**.
    *   The indicator will turn **GREEN** (Operational).
    *   The bot will now wait for the "Scout" and the "Brain" to agree before trading.

---

## ⚠️ Important Rules for Beginners

*   **Compounding**: The bot uses a percentage of your balance (e.g., 1%). As your balance grows, the trade size grows automatically.
*   **Volatility**: These markets (R_100, R_50, etc.) are synthetic. They don't stop for weekends or news.
*   **Neural Filter**: If you see "Signal detected... Blocked by Neural Filter" in the logs, **this is a good thing!** It means the AI just saved you from a likely loss.
*   **Accuracy Gate**: Every night, the bot checks if its new "Brain" is smart enough. If the AI can't predict patterns better than a coin flip, it will refuse to update, keeping your old (trusted) brain instead.

---

**That's it! You are now ready to run your own AI-powered trading desk. Happy Trading!** ⚡
