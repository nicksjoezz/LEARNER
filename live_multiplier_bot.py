import asyncio
import pandas as pd
import numpy as np
import time
import json
import os
import logging
import threading
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
        self.active_positions = {} # {symbol: contract_id}
        self.is_initializing = False

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
                'initializing': self.is_initializing,
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
        self.last_1m_epoch[symbol] = int(self.history_1m[symbol].iloc[-1]['epoch'])
        self.last_15m_epoch[symbol] = int(self.history_15m[symbol].iloc[-1]['epoch'])
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
        if symbol in self.active_positions:
            self.log(f"Skipping {symbol} - already have an active position.")
            return

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
                cid = res['buy']['contract_id']
                self.active_positions[symbol] = cid
                self.log(f"SUCCESS: {symbol} {config['direction']} placed! ID: {cid}")
                # Start monitoring this contract
                asyncio.create_task(self.monitor_contract(symbol, cid))
            else:
                self.log(f"ERROR: {res.get('error', {}).get('message')}", "error")
        except Exception as e:
            self.log(f"Placement error: {e}", "error")

    async def monitor_contract(self, symbol, contract_id):
        """Monitor a specific contract until it closes."""
        try:
            sub = await self.api.subscribe({"proposal_open_contract": 1, "contract_id": contract_id})

            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()
            sub.subscribe(on_next=lambda m: loop.call_soon_threadsafe(queue.put_nowait, m))

            while self.is_running:
                msg = await queue.get()
                if 'proposal_open_contract' in msg:
                    contract = msg['proposal_open_contract']
                    if contract.get('is_expired') or contract.get('status') != 'open':
                        profit = contract.get('profit', 0)
                        self.log(f"CLOSED {symbol} | Profit: ${profit}")
                        if symbol in self.active_positions:
                            del self.active_positions[symbol]

                        # Refresh balance
                        bal_res = await self.api.balance()
                        if 'balance' in bal_res:
                            self.balance = float(bal_res['balance']['balance'])
                            self.update_status()
                        break
        except Exception as e:
            self.log(f"Monitor error for {symbol}: {e}", "error")
            if symbol in self.active_positions: del self.active_positions[symbol]

    async def start(self):
        self.is_initializing = True
        self.update_status()

        if not await self.connect():
            self.is_initializing = False
            self.update_status()
            return

        self.is_running = True

        symbols = ['BOOM500', 'CRASH500']

        def symbol_worker(symbol):
            self.log(f"Worker thread for {symbol} starting...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def run_symbol():
                # Separate API connection for each symbol thread to ensure total isolation
                symbol_api = DerivAPI(app_id=self.app_id)
                await symbol_api.authorize(self.api_token)

                # Fetch history using this thread's API
                res_1m = await symbol_api.ticks_history({
                    'ticks_history': symbol, 'end': 'latest', 'count': 500, 'granularity': 60, 'style': 'candles'
                })
                self.history_1m[symbol] = pd.DataFrame(res_1m['candles'])
                res_15m = await symbol_api.ticks_history({
                    'ticks_history': symbol, 'end': 'latest', 'count': 100, 'granularity': 900, 'style': 'candles'
                })
                self.history_15m[symbol] = pd.DataFrame(res_15m['candles'])
                self.last_1m_epoch[symbol] = int(self.history_1m[symbol].iloc[-1]['epoch'])
                self.last_15m_epoch[symbol] = int(self.history_15m[symbol].iloc[-1]['epoch'])
                self.log(f"[{symbol}] Initial history loaded.")

                # Immediate Signal Check
                if self.calculate_strategy(symbol):
                    self.log(f"[{symbol}] IMMEDIATE SIGNAL detected!")
                    await self.place_trade_isolated(symbol, symbol_api)

                # Subscription
                subscription = await symbol_api.subscribe({
                    'ticks_history': symbol, 'end': 'latest', 'subscribe': 1, 'granularity': 60, 'style': 'candles'
                })

                queue = asyncio.Queue()
                subscription.subscribe(on_next=lambda m: loop.call_soon_threadsafe(queue.put_nowait, m))

                while self.is_running:
                    msg = await queue.get()
                    if 'ohlc' in msg:
                        candle = msg['ohlc']
                        if int(candle['epoch']) > self.last_1m_epoch[symbol]:
                            self.log(f"[{symbol}] Minute rollover. Recalculating...")

                            new_row = {
                                'epoch': candle['epoch'], 'open': candle['open'], 'high': candle['high'],
                                'low': candle['low'], 'close': candle['close']
                            }
                            self.history_1m[symbol] = pd.concat([self.history_1m[symbol], pd.DataFrame([new_row])]).drop_duplicates('epoch').tail(500)
                            self.last_1m_epoch[symbol] = int(candle['epoch'])

                            # Persistence
                            pd.DataFrame([new_row]).to_csv(f"market_data/{symbol}_1m_history.csv", mode='a', header=not os.path.exists(f"market_data/{symbol}_1m_history.csv"), index=False)

                            if int(candle['epoch']) % 900 == 0:
                                res_15 = await symbol_api.ticks_history({
                                    'ticks_history': symbol, 'end': 'latest', 'count': 1, 'granularity': 900, 'style': 'candles'
                                })
                                if 'candles' in res_15:
                                    self.history_15m[symbol] = pd.concat([self.history_15m[symbol], pd.DataFrame(res_15['candles'])]).drop_duplicates('epoch').tail(100)

                            if self.calculate_strategy(symbol):
                                self.log(f"[{symbol}] SIGNAL detected!")
                                await self.place_trade_isolated(symbol, symbol_api)

                await symbol_api.disconnect()

            try:
                loop.run_until_complete(run_symbol())
            except Exception as e:
                self.log(f"Worker for {symbol} error: {e}", "error")
            finally:
                loop.close()

        for s in symbols:
            threading.Thread(target=symbol_worker, args=(s,), daemon=True).start()

        self.is_initializing = False
        self.update_status()
        self.log("All systems active in separate threads.")

        while self.is_running:
            await asyncio.sleep(5)

    async def place_trade_isolated(self, symbol, api):
        """Places a trade using the provided API instance (isolated per thread)."""
        if symbol in self.active_positions: return

        config = STRATEGY_CONFIG[symbol]
        actual_stake = self.stake
        if self.stake_type == 'percent':
            actual_stake = round(self.balance * (self.stake / 100.0), 2)
            if actual_stake < 1.0: actual_stake = 1.0

        tp_usd = round(actual_stake * (config['tp_roi'] / 100.0), 2)
        sl_usd = round(abs(actual_stake * (config['sl_roi'] / 100.0)), 2)
        if sl_usd >= actual_stake: sl_usd = round(actual_stake * 0.9, 2)

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
            res = await api.buy(params)
            if 'buy' in res:
                cid = res['buy']['contract_id']
                self.active_positions[symbol] = cid
                self.log(f"SUCCESS: {symbol} placed! ID: {cid}")
                asyncio.create_task(self.monitor_contract_isolated(symbol, cid, api))
            else:
                self.log(f"ERROR placing {symbol}: {res.get('error', {}).get('message')}", "error")
        except Exception as e:
            self.log(f"Placement error {symbol}: {e}", "error")

    async def monitor_contract_isolated(self, symbol, contract_id, api):
        """Monitor a specific contract until it closes using the thread's API."""
        try:
            sub = await api.subscribe({"proposal_open_contract": 1, "contract_id": contract_id})
            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()
            sub.subscribe(on_next=lambda m: loop.call_soon_threadsafe(queue.put_nowait, m))

            while self.is_running:
                msg = await queue.get()
                if 'proposal_open_contract' in msg:
                    contract = msg['proposal_open_contract']
                    if contract.get('is_expired') or contract.get('status') != 'open':
                        profit = contract.get('profit', 0)
                        self.log(f"CLOSED {symbol} | Profit: ${profit}")
                        if symbol in self.active_positions: del self.active_positions[symbol]

                        bal_res = await api.balance()
                        if 'balance' in bal_res:
                            self.balance = float(bal_res['balance']['balance'])
                            self.update_status()
                        break
        except Exception as e:
            self.log(f"Monitor error for {symbol}: {e}", "error")
            if symbol in self.active_positions: del self.active_positions[symbol]

    async def subscribe_candles(self, symbol):
        self.log(f"Starting subscription for {symbol}")
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        def on_next(msg):
            # Deriv API (rx) might call this from a different thread
            loop.call_soon_threadsafe(queue.put_nowait, msg)

        def on_error(e):
            self.log(f"Stream error for {symbol}: {e}", "error")

        try:
            # Subscribe to 1-minute candles
            subscription = await self.api.subscribe({
                'ticks_history': symbol,
                'end': 'latest',
                'subscribe': 1,
                'granularity': 60,
                'style': 'candles'
            })

            # Use rx subscribe since the result is an Observable
            subscription.subscribe(on_next=on_next, on_error=on_error)

            while self.is_running:
                msg = await queue.get()
                if 'ohlc' in msg:
                    candle = msg['ohlc']
                    # Check if candle just CLOSED
                    # Deriv sends updates for the current candle. We only act on close.
                    if int(candle['epoch']) > self.last_1m_epoch[symbol]:
                        self.log(f"[{symbol}] Candle closed at {candle['epoch']}. Recalculating...")

                        # Update local history
                        new_row = {
                            'epoch': candle['epoch'],
                            'open': candle['open'],
                            'high': candle['high'],
                            'low': candle['low'],
                            'close': candle['close']
                        }
                        self.history_1m[symbol] = pd.concat([self.history_1m[symbol], pd.DataFrame([new_row])]).drop_duplicates('epoch').tail(500)
                        self.last_1m_epoch[symbol] = int(candle['epoch'])

                        # Data Persistence
                        data_dir = 'market_data'
                        os.makedirs(data_dir, exist_ok=True)
                        pd.DataFrame([new_row]).to_csv(f"{data_dir}/{symbol}_1m_history.csv", mode='a', header=not os.path.exists(f"{data_dir}/{symbol}_1m_history.csv"), index=False)

                        # Check 15m alignment
                        if int(candle['epoch']) % 900 == 0:
                            res_15 = await self.api.ticks_history({
                                'ticks_history': symbol, 'end': 'latest', 'count': 1, 'granularity': 900, 'style': 'candles'
                            })
                            if 'candles' in res_15:
                                self.history_15m[symbol] = pd.concat([self.history_15m[symbol], pd.DataFrame(res_15['candles'])]).drop_duplicates('epoch').tail(100)
                                self.log(f"[{symbol}] 15m trend updated.")

                        # Recalculate Strategy
                        if self.calculate_strategy(symbol):
                            self.log(f"ENTRY SIGNAL for {symbol}!")
                            await self.place_trade(symbol)

        except Exception as e:
            self.log(f"Subscription error for {symbol}: {e}", "error")

    async def stop(self):
        self.is_running = False
        # Small delay to allow worker loops to detect is_running=False
        await asyncio.sleep(1)
        if self.api:
            await self.api.disconnect()
        self.log("Bot stopped.")
        self.update_status()
