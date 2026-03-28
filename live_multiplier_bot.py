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

# Strategy Parameters (Centralized)
# TP and SL are now expressed as ROI percentages of the STAKE.
# e.g., 150.0 means +150% profit. -20.0 means -20% loss.
STRATEGY_CONFIG = {
    'BOOM500': {
        'multiplier': 300,
        'tp_roi': 150.0, # 150% ROI
        'sl_roi': -20.0, # -20% ROI
        'direction': 'buy'
    },
    'CRASH500': {
        'multiplier': 300,
        'tp_roi': 100.0, # 100% ROI
        'sl_roi': -20.0, # -20% ROI
        'direction': 'sell'
    }
}

class MultiplierBot:
    def __init__(self, api_token, app_id='62845', socketio=None):
        self.api_token = api_token
        self.app_id = app_id
        self.socketio = socketio
        self.api = None
        self.is_running = False
        self.history_1m = {}
        self.history_15m = {}
        self.last_1m_epoch = {}
        self.last_15m_epoch = {}
        self.balance = 0.0
        self.currency = "USD"
        self.stake = 10.0
        self.stake_type = 'fixed' # 'fixed' or 'percent'
        self.log_buffer = []
        self.main_task = None

        logging.basicConfig(level=logging.INFO, format='%(message)s')
        self.logger = logging.getLogger("MultiplierBot")

    def log(self, message, level="info"):
        timestamp = time.strftime('%H:%M:%S', time.gmtime())
        full_msg = f"{timestamp} | {message}"
        if level == "info": self.logger.info(full_msg)
        else: self.logger.error(full_msg)

        if self.socketio:
            self.socketio.emit('log_update', {'msg': full_msg})

    async def connect(self):
        try:
            self.api = DerivAPI(app_id=self.app_id)
            auth = await self.api.authorize(self.api_token)
            self.balance = float(auth['authorize']['balance'])
            self.currency = auth['authorize']['currency']
            self.log(f"Connected! Balance: {self.balance} {self.currency}")
            self.update_status()
            return True
        except Exception as e:
            self.log(f"Connection error: {e}", "error")
            return False

    def update_status(self):
        if self.socketio:
            self.socketio.emit('status_update', {
                'active': self.is_running,
                'balance': f"{self.balance:.2f} {self.currency}",
                'logs': self.log_buffer[-5:]
            })

    async def fetch_initial_data(self, symbol):
        res_1m = await self.api.ticks_history({
            'ticks_history': symbol, 'end': 'latest', 'count': 500, 'granularity': 60, 'style': 'candles'
        })
        self.history_1m[symbol] = pd.DataFrame(res_1m['candles'])
        res_15m = await self.api.ticks_history({
            'ticks_history': symbol, 'end': 'latest', 'count': 100, 'granularity': 900, 'style': 'candles'
        })
        self.history_15m[symbol] = pd.DataFrame(res_15m['candles'])
        self.last_1m_epoch[symbol] = self.history_1m[symbol].iloc[-1]['epoch']
        self.last_15m_epoch[symbol] = self.history_15m[symbol].iloc[-1]['epoch']
        self.log(f"History initialized for {symbol}")

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

        # Calculate actual stake
        actual_stake = self.stake
        if self.stake_type == 'percent':
            actual_stake = round(self.balance * (self.stake / 100.0), 2)
            if actual_stake < 1.0: actual_stake = 1.0 # Minimum reasonable multiplier stake

        # Map ROI % to USD amount
        # Profit/Loss = Stake * (ROI / 100)
        tp_usd = round(actual_stake * (config['tp_roi'] / 100.0), 2)
        sl_usd = round(abs(actual_stake * (config['sl_roi'] / 100.0)), 2)

        # SL Safety Check: Cannot exceed stake
        if sl_usd >= actual_stake:
            sl_usd = round(actual_stake * 0.9, 2)

        self.log(f"PLACING {symbol} {config['direction']} | Stake: ${actual_stake} | TP: ${tp_usd} | SL: ${sl_usd}")

        params = {
            "buy": 1, "price": actual_stake,
            "parameters": {
                "amount": actual_stake, "basis": "stake",
                "contract_type": "MULTUP" if config['direction'] == 'buy' else "MULTDOWN",
                "currency": self.currency, "multiplier": config['multiplier'], "symbol": symbol,
                "limit_order": {"take_profit": tp_usd, "stop_loss": sl_usd}
            }
        }
        try:
            res = await self.api.buy(params)
            if 'buy' in res:
                self.log(f"SUCCESS: {symbol} {config['direction']} placed! ID: {res['buy']['contract_id']}")
            else:
                self.log(f"ERROR: {res.get('error', {}).get('message')}", "error")
        except Exception as e:
            self.log(f"Placement error: {e}", "error")

    async def start(self):
        if not await self.connect(): return
        self.is_running = True
        self.update_status()
        await self.main_loop(['BOOM500', 'CRASH500'])

    async def stop(self):
        self.is_running = False
        if self.main_task:
            self.main_task.cancel()
        if self.api:
            await self.api.disconnect()
        self.log("Bot stopped.")
        self.update_status()

    async def main_loop(self, symbols):
        for s in symbols: await self.fetch_initial_data(s)
        while self.is_running:
            for symbol in symbols:
                try:
                    res = await self.api.ticks_history({
                        'ticks_history': symbol, 'end': 'latest', 'count': 5, 'granularity': 60, 'style': 'candles'
                    })
                    new_df = pd.DataFrame(res['candles'])
                    # Persistence: Save only the NEWEST closed candle to CSV
                    last_epoch = new_df.iloc[-1]['epoch']
                    if last_epoch > self.last_1m_epoch.get(symbol, 0):
                        data_dir = 'market_data'
                        os.makedirs(data_dir, exist_ok=True)
                        # Only save the last candle
                        last_candle = new_df.tail(1)
                        last_candle.to_csv(f"{data_dir}/{symbol}_1m_history.csv", mode='a', header=not os.path.exists(f"{data_dir}/{symbol}_1m_history.csv"), index=False)

                    if last_epoch > self.last_1m_epoch[symbol]:
                        self.log(f"[{symbol}] New candle closed.")
                        self.history_1m[symbol] = pd.concat([self.history_1m[symbol], new_df]).drop_duplicates('epoch').tail(500)
                        self.last_1m_epoch[symbol] = last_epoch
                        if last_epoch % 900 == 0:
                            res_15 = await self.api.ticks_history({
                                'ticks_history': symbol, 'end': 'latest', 'count': 5, 'granularity': 900, 'style': 'candles'
                            })
                            self.history_15m[symbol] = pd.concat([self.history_15m[symbol], pd.DataFrame(res_15['candles'])]).drop_duplicates('epoch').tail(100)
                        if self.calculate_strategy(symbol):
                            self.log(f"ENTRY SIGNAL for {symbol}!")
                            await self.place_trade(symbol)
                except Exception as e:
                    self.log(f"Loop error: {e}", "error")
            await asyncio.sleep(10)
