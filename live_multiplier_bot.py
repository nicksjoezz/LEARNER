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

# Strategy Parameters
STRATEGY_CONFIG = {
    'BOOM500': {
        'multiplier': 300,
        'tp_roi': 150.0,
        'sl_roi': -20.0,
        'direction': 'buy'
    },
    'CRASH500': {
        'multiplier': 300,
        'tp_roi': 100.0,
        'sl_roi': -20.0,
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
        self.is_initializing = False
        self.history_1m = {}
        self.history_15m = {}
        self.last_1m_epoch = {}
        self.balance = 0.0
        self.currency = "USD"
        self.stake = 10.0
        self.stake_type = 'fixed'
        self.active_positions = {} # {symbol: contract_id}

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
                'balance': f"{self.balance:.2f} {self.currency}"
            })

    def calculate_strategy(self, symbol):
        df_with_signals = crash_boom_mtf_strategy(self.history_1m[symbol], self.history_15m[symbol], symbol)
        last_signal = df_with_signals.iloc[-2]
        return {
            'buy': last_signal.get('buy', False),
            'sell': last_signal.get('sell', False)
        }

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
                symbol_api = DerivAPI(app_id=self.app_id)
                await symbol_api.authorize(self.api_token)

                # Immediate history fetch
                res_1m = await symbol_api.ticks_history({'ticks_history': symbol, 'end': 'latest', 'count': 500, 'granularity': 60, 'style': 'candles'})
                df_1m = pd.DataFrame(res_1m['candles'])
                for col in ['open', 'high', 'low', 'close', 'epoch']: df_1m[col] = pd.to_numeric(df_1m[col])
                self.history_1m[symbol] = df_1m

                res_15m = await symbol_api.ticks_history({'ticks_history': symbol, 'end': 'latest', 'count': 100, 'granularity': 900, 'style': 'candles'})
                df_15m = pd.DataFrame(res_15m['candles'])
                for col in ['open', 'high', 'low', 'close', 'epoch']: df_15m[col] = pd.to_numeric(df_15m[col])
                self.history_15m[symbol] = df_15m

                self.last_1m_epoch[symbol] = int(df_1m.iloc[-1]['epoch'])
                self.log(f"[{symbol}] Initial history loaded. Checking for immediate signals...")

                # Immediate check
                signals = self.calculate_strategy(symbol)
                await self.process_signals(symbol, signals, symbol_api)

                # Subscription
                subscription = await symbol_api.subscribe({'ticks_history': symbol, 'end': 'latest', 'subscribe': 1, 'granularity': 60, 'style': 'candles', 'count': 1})
                queue = asyncio.Queue()
                subscription.subscribe(on_next=lambda m: loop.call_soon_threadsafe(queue.put_nowait, m))

                while self.is_running:
                    msg = await queue.get()
                    if 'ohlc' in msg:
                        candle = msg['ohlc']
                        candle_start = int(candle['epoch'])
                        if (candle_start // 60) > (self.last_1m_epoch[symbol] // 60):
                            self.log(f"[{symbol}] Minute rollover detected. Recalculating...")
                            new_row = {'epoch': float(candle_start), 'open': float(candle['open']), 'high': float(candle['high']), 'low': float(candle['low']), 'close': float(candle['close'])}
                            self.history_1m[symbol] = pd.concat([self.history_1m[symbol], pd.DataFrame([new_row])]).drop_duplicates('epoch').tail(500)
                            self.last_1m_epoch[symbol] = candle_start

                            # Persistence
                            pd.DataFrame([new_row]).to_csv(f"market_data/{symbol}_1m_history.csv", mode='a', header=not os.path.exists(f"market_data/{symbol}_1m_history.csv"), index=False)

                            if candle_start % 900 == 0:
                                res_15 = await symbol_api.ticks_history({'ticks_history': symbol, 'end': 'latest', 'count': 1, 'granularity': 900, 'style': 'candles'})
                                if 'candles' in res_15:
                                    df_15_new = pd.DataFrame(res_15['candles'])
                                    for col in ['open', 'high', 'low', 'close', 'epoch']: df_15_new[col] = pd.to_numeric(df_15_new[col])
                                    self.history_15m[symbol] = pd.concat([self.history_15m[symbol], df_15_new]).drop_duplicates('epoch').tail(100)

                            signals = self.calculate_strategy(symbol)
                            await self.process_signals(symbol, signals, symbol_api)

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
        self.log("All symbol workers active.")

        while self.is_running:
            await asyncio.sleep(5)

    async def process_signals(self, symbol, signals, api):
        target_direction = STRATEGY_CONFIG[symbol]['direction']
        entry_signal = signals['buy'] if target_direction == 'buy' else signals['sell']
        opposite_signal = signals['sell'] if target_direction == 'buy' else signals['buy']

        # 1. Close if opposite signal
        if opposite_signal and symbol in self.active_positions:
            self.log(f"[{symbol}] OPPOSITE SIGNAL detected! Closing existing position.")
            await self.close_position(symbol, api)

        # 2. Open if entry signal and no position
        if entry_signal and symbol not in self.active_positions:
            self.log(f"[{symbol}] ENTRY SIGNAL detected!")
            await self.place_trade_isolated(symbol, api)

    async def close_position(self, symbol, api):
        if symbol not in self.active_positions: return

        contract_id = self.active_positions.get(symbol)
        if not contract_id: return

        try:
            res = await api.sell({"sell": contract_id, "price": 0})
            if 'sell' in res:
                self.log(f"SUCCESS: {symbol} closed manually on opposite signal.")
                if symbol in self.active_positions: del self.active_positions[symbol]
            else:
                err_msg = res.get('error', {}).get('message', 'Unknown error')
                self.log(f"ERROR closing {symbol}: {err_msg}", "error")
                if "invalid contract" in err_msg.lower() or "not found" in err_msg.lower():
                    # If contract is already gone, clean up
                    if symbol in self.active_positions: del self.active_positions[symbol]
        except Exception as e:
            self.log(f"Close error {symbol}: {e}", "error")

    async def place_trade_isolated(self, symbol, api):
        if symbol in self.active_positions:
            return

        config = STRATEGY_CONFIG[symbol]
        actual_stake = self.stake
        if self.stake_type == 'percent':
            actual_stake = round(self.balance * (self.stake / 100.0), 2)
            if actual_stake < 1.0: actual_stake = 1.0

        tp_usd = round(actual_stake * (config['tp_roi'] / 100.0), 2)
        sl_usd = round(abs(actual_stake * (config['sl_roi'] / 100.0)), 2)
        if sl_usd >= actual_stake: sl_usd = round(actual_stake * 0.9, 2)

        self.log(f"PLACING {symbol} {config['direction']} | Stake: ${actual_stake}")

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

    async def stop(self):
        self.is_running = False
        await asyncio.sleep(1)
        if self.api:
            await self.api.disconnect()
        self.log("Bot stopped.")
        self.update_status()
