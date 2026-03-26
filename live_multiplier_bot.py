import asyncio
import pandas as pd
import numpy as np
import time
import json
import os
import logging
from deriv_api import DerivAPI
import ta
from crash_boom_strategy import crash_boom_mtf_strategy

# Strategy Parameters
# Note: Deriv Multipliers require TP and SL as absolute dollar amounts.
# For a $10 stake, a 100% gain is $10.
STRATEGY_CONFIG = {
    'BOOM500': {
        'multiplier': 300,
        'tp_usd': 15.0, # Optimized: capture frequent medium spikes
        'sl_usd': 2.0,  # Optimized: tight stop to preserve capital
        'direction': 'buy'
    },
    'CRASH500': {
        'multiplier': 300,
        'tp_usd': 10.0,
        'sl_usd': 2.0,
        'direction': 'sell'
    }
}

class MultiplierBot:
    def __init__(self, api_token, app_id='62845'):
        self.api_token = api_token
        self.app_id = app_id
        self.api = None
        self.is_running = False
        self.history_1m = {} # symbol -> df
        self.history_15m = {} # symbol -> df
        self.last_1m_epoch = {}
        self.last_15m_epoch = {}

        logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
        self.logger = logging.getLogger("MultiplierBot")

    async def connect(self):
        try:
            self.api = DerivAPI(app_id=self.app_id)
            auth = await self.api.authorize(self.api_token)
            self.logger.info(f"Authorized! Balance: {auth['authorize']['balance']} {auth['authorize']['currency']}")
            return True
        except Exception as e:
            self.logger.error(f"Connection error: {e}")
            return False

    async def fetch_initial_data(self, symbol):
        # Fetch 1m data (need ~100 for indicators)
        res_1m = await self.api.ticks_history({
            'ticks_history': symbol, 'end': 'latest', 'count': 500, 'granularity': 60, 'style': 'candles'
        })
        self.history_1m[symbol] = pd.DataFrame(res_1m['candles'])

        # Fetch 15m data
        res_15m = await self.api.ticks_history({
            'ticks_history': symbol, 'end': 'latest', 'count': 100, 'granularity': 900, 'style': 'candles'
        })
        self.history_15m[symbol] = pd.DataFrame(res_15m['candles'])

        self.last_1m_epoch[symbol] = self.history_1m[symbol].iloc[-1]['epoch']
        self.last_15m_epoch[symbol] = self.history_15m[symbol].iloc[-1]['epoch']
        self.logger.info(f"Initialized history for {symbol}")

    def calculate_strategy(self, symbol):
        # Use the centralized logic from crash_boom_strategy
        df_with_signals = crash_boom_mtf_strategy(self.history_1m[symbol], self.history_15m[symbol], symbol)

        # Check the signal on the last COMPLETED candle (index -2)
        last_signal = df_with_signals.iloc[-2]

        if STRATEGY_CONFIG[symbol]['direction'] == 'buy':
            return last_signal['buy']
        else:
            return last_signal['sell']

    async def place_trade(self, symbol):
        config = STRATEGY_CONFIG[symbol]
        stake = 10 # Default demo stake

        params = {
            "buy": 1,
            "price": stake,
            "parameters": {
                "amount": stake,
                "basis": "stake",
                "contract_type": "MULTUP" if config['direction'] == 'buy' else "MULTDOWN",
                "currency": "USD",
                "multiplier": config['multiplier'],
                "symbol": symbol,
                "limit_order": {
                    "take_profit": config['tp_usd'],
                    "stop_loss": config['sl_usd']
                }
            }
        }

        try:
            res = await self.api.buy(params)
            if 'buy' in res:
                self.logger.info(f"TRADE PLACED: {symbol} {config['direction']} at {res['buy']['start_time']}")
            else:
                self.logger.error(f"Execution Error: {res.get('error', {}).get('message')}")
        except Exception as e:
            self.logger.error(f"Placement Error: {e}")

    async def main_loop(self, symbols):
        for s in symbols:
            await self.fetch_initial_data(s)

        self.is_running = True
        self.logger.info("Bot is running...")

        while self.is_running:
            for symbol in symbols:
                try:
                    # Update OHLC
                    res = await self.api.ticks_history({
                        'ticks_history': symbol, 'end': 'latest', 'count': 5, 'granularity': 60, 'style': 'candles'
                    })
                    new_df = pd.DataFrame(res['candles'])
                    last_epoch = new_df.iloc[-1]['epoch']

                    if last_epoch > self.last_1m_epoch[symbol]:
                        # New 1m candle closed
                        self.logger.info(f"[{symbol}] New 1m candle: {time.ctime(last_epoch)}")
                        self.history_1m[symbol] = pd.concat([self.history_1m[symbol], new_df]).drop_duplicates('epoch').tail(500)
                        self.last_1m_epoch[symbol] = last_epoch

                        # Sync 15m every 15 mins
                        if last_epoch % 900 == 0:
                            res_15 = await self.api.ticks_history({
                                'ticks_history': symbol, 'end': 'latest', 'count': 5, 'granularity': 900, 'style': 'candles'
                            })
                            self.history_15m[symbol] = pd.concat([self.history_15m[symbol], pd.DataFrame(res_15['candles'])]).drop_duplicates('epoch').tail(100)

                        # Check Signal
                        if self.calculate_strategy(symbol):
                            self.logger.info(f"SIGNAL DETECTED for {symbol}!")
                            await self.place_trade(symbol)

                except Exception as e:
                    self.logger.error(f"Loop error for {symbol}: {e}")

            await asyncio.sleep(10) # Check every 10 seconds

async def main():
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)

    bot = MultiplierBot(api_token=config['api_token'])
    if await bot.connect():
        await bot.main_loop(['BOOM500', 'CRASH500'])

if __name__ == "__main__":
    asyncio.run(main())
