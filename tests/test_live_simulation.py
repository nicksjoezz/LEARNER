import asyncio
import json
import os
import sys

# Add current directory to path so we can import trading_bot
sys.path.append(os.getcwd())

from trading_bot import TradingBot

class MockSocketIO:
    def emit(self, event, data):
        print(f"[Emit] {event}: {data}")

async def test_bot_startup():
    # Load config
    if not os.path.exists('config.json'):
        print("config.json not found")
        return

    with open('config.json', 'r') as f:
        config = json.load(f)

    if not config.get('api_token'):
        print("API token missing in config.json")
        return

    print(f"Testing bot with symbol: {config.get('symbol')}")

    sio = MockSocketIO()
    bot = TradingBot(sio)

    # We use a shorter timeout for the test if possible,
    # but the bot has its own internal timeouts.

    print("Starting bot...")
    # asyncio.create_task(bot.start(config))
    # Run start and wait a bit
    start_task = asyncio.create_task(bot.start(config))

    # Wait for initialization
    for i in range(24): # 2 minutes
        await asyncio.sleep(5)
        state = bot.get_state()
        print(f"[{i*5}s] Active: {state['active']} | Balance: {state['balance']} | Trades: {state['total_trades']}")

        if bot.is_running and bot.live_handler and not bot.live_handler.history_df.empty:
            print("Bot is running and history is loaded.")
            if len(bot.live_handler.history_df) >= 1000:
                print(f"Successfully fetched {len(bot.live_handler.history_df)} candles.")
                break

        if not bot.is_running and i > 4:
            print("Bot failed to stay running.")
            break

    print("Test running for another 30 seconds to catch ticks...")
    await asyncio.sleep(30)

    print("Stopping bot...")
    try:
        await bot.stop()
    except Exception as e:
        print(f"Error stopping bot: {e}")
    print("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(test_bot_startup())
    except KeyboardInterrupt:
        print("Test interrupted")
    except Exception as e:
        print(f"Test error: {e}")
