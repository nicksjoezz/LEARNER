import asyncio
import json
import os
import sys

sys.path.append(os.getcwd())

from trading_bot import TradingBot
from handlers.live_handler import LiveHandler

class MockSocketIO:
    def emit(self, event, data):
        print(f"[Emit] {event}: {data}")

async def test_order_placement():
    with open('config.json', 'r') as f:
        config = json.load(f)

    print(f"Testing order placement for symbol: {config.get('symbol')}")

    sio = MockSocketIO()
    bot = TradingBot(sio)
    bot.config = config

    bot.live_handler = LiveHandler(bot)

    print("Connecting...")
    if await bot.live_handler.connect(config):
        print("Connected. Subscribing...")
        await bot.live_handler.subscribe_account()

        print("Placing test CALL trade...")
        # Note: side should be 'CALL' or 'PUT' as expected by Deriv API
        await bot.live_handler.place_trade('CALL')

        print("Waiting 10 seconds for confirmation...")
        await asyncio.sleep(10)

        print(f"Total trades: {bot.total_trades}")
        print(f"Active contracts: {list(bot.live_handler.trade_handler.active_contracts.keys())}")

        print("Cleaning up...")
        await bot.live_handler.disconnect()
    else:
        print("Failed to connect.")

if __name__ == "__main__":
    asyncio.run(test_order_placement())
