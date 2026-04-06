import asyncio
import pandas as pd
import numpy as np
import time
import json
import os
import logging
from logging.handlers import RotatingFileHandler
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
    def __init__(self, api_token, app_id='62845', socketio=None, logger_callback=None):
        self.api_token = api_token
        self.app_id = app_id
        self.socketio = socketio
        self.logger_callback = logger_callback
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
        self.positions_lock = threading.Lock()
        self.logger = logging.getLogger("MultiplierBot")

    def log(self, message):
        # Always include symbol prefix if possible for clarity
        if self.logger_callback:
            self.logger_callback(message)
        else:
            timestamp = time.strftime('%H:%M:%S', time.gmtime())
            full_msg = f"{timestamp} | {message}"
            self.logger.info(full_msg)
            if self.socketio:
                # Direct emit as fallback
                self.socketio.emit('log_update', {'msg': full_msg}, namespace='/')

    async def start(self):
        self.is_initializing = True
        os.makedirs('market_data', exist_ok=True)
        self.log("Bot system starting up...")

        while self.is_initializing:
            try:
                api = DerivAPI(app_id=self.app_id)
                auth = await asyncio.wait_for(api.authorize(self.api_token), timeout=15)
                self.balance = float(auth['authorize']['balance'])
                self.currency = auth['authorize']['currency']
                self.log(f"Connected to Deriv! Balance: {self.balance} {self.currency}")

                await self.sync_open_positions(api)

                self.is_initializing = False
                self.is_running = True

                # Run the unified monitoring loop
                await self.monitoring_loop(api)
            except Exception as e:
                self.log(f"Initialization error: {e}. Retrying in 10s...")
                try: await api.disconnect()
                except: pass
                await asyncio.sleep(10)
            finally:
                self.is_initializing = False
                self.is_running = False

    async def monitoring_loop(self, api):
        self.log("Unified monitoring loop active for BOOM500 and CRASH500.")
        symbols = ['BOOM500', 'CRASH500']

        # Initial History Load for all symbols
        for symbol in symbols:
            try:
                await self.refresh_history(symbol, api)
                self.log(f"[{symbol}] Historical data synchronized.")
                signals = self.calculate_strategy(symbol)
                await self.process_signals(symbol, signals, api)
            except Exception as e:
                self.log(f"[{symbol}] Failed initial data sync: {e}")

        # Persistent subscriptions using a single connection
        queues = {s: asyncio.Queue() for s in symbols}
        loop = asyncio.get_running_loop()

        subscription_objects = []
        for symbol in symbols:
            try:
                sub = await api.subscribe({'ticks_history': symbol, 'subscribe': 1, 'granularity': 60, 'style': 'candles', 'count': 1})
                if sub:
                    # ReactiveX subscription - push into asyncio queues
                    s_obj = sub.subscribe(on_next=lambda m, s=symbol: loop.call_soon_threadsafe(queues[s].put_nowait, m))
                    subscription_objects.append(s_obj)
                    self.log(f"[{symbol}] Live OHLC subscription established.")
                else:
                    self.log(f"[{symbol}] Critical: Subscription failed (None returned).", "error")
            except Exception as e:
                self.log(f"[{symbol}] Subscription error: {e}", "error")

        last_portfolio_sync = time.time()
        last_heartbeat = time.time()

        try:
            while self.is_running:
                # Process all available messages from all queues
                for symbol in symbols:
                    q = queues[symbol]
                    while not q.empty():
                        msg = q.get_nowait()
                        if 'ohlc' in msg:
                            await self.handle_ohlc(symbol, msg['ohlc'], api)

                now = time.time()
                # Unified periodic tasks
                if now - last_portfolio_sync > 60: # Every minute
                    await self.sync_open_positions(api)
                    last_portfolio_sync = now

                if now - last_heartbeat > 300: # Every 5 minutes
                    self.log(f"24/7 Status Check: Monitoring {symbols}. Active: {list(self.active_positions.keys())}")
                    last_heartbeat = now

                await asyncio.sleep(0.5)
        finally:
            self.log("Cleaning up subscriptions and connection...")
            for s in subscription_objects: s.dispose()
            try: await asyncio.wait_for(api.disconnect(), timeout=5)
            except: pass
            self.log("Unified monitoring loop terminated.")

    async def refresh_history(self, symbol, api):
        res_1m = await asyncio.wait_for(api.ticks_history({'ticks_history': symbol, 'end': 'latest', 'count': 500, 'granularity': 60, 'style': 'candles'}), timeout=20)
        df_1m = pd.DataFrame(res_1m['candles'])
        for col in ['open', 'high', 'low', 'close', 'epoch']: df_1m[col] = pd.to_numeric(df_1m[col])
        self.history_1m[symbol] = df_1m

        res_15m = await asyncio.wait_for(api.ticks_history({'ticks_history': symbol, 'end': 'latest', 'count': 100, 'granularity': 900, 'style': 'candles'}), timeout=20)
        df_15m = pd.DataFrame(res_15m['candles'])
        for col in ['open', 'high', 'low', 'close', 'epoch']: df_15m[col] = pd.to_numeric(df_15m[col])
        self.history_15m[symbol] = df_15m
        self.last_1m_epoch[symbol] = int(df_1m.iloc[-1]['epoch'])

    async def handle_ohlc(self, symbol, candle, api):
        candle_start = int(candle['epoch'])
        if (candle_start // 60) > (self.last_1m_epoch.get(symbol, 0) // 60):
            self.log(f"[{symbol}] New minute candle detected. Analyzing...")
            new_row = {'epoch': float(candle_start), 'open': float(candle['open']), 'high': float(candle['high']), 'low': float(candle['low']), 'close': float(candle['close'])}
            self.history_1m[symbol] = pd.concat([self.history_1m[symbol], pd.DataFrame([new_row])]).drop_duplicates('epoch').tail(500)
            self.last_1m_epoch[symbol] = candle_start

            # Persistence
            pd.DataFrame([new_row]).to_csv(f"market_data/{symbol}_1m_history.csv", mode='a', header=not os.path.exists(f"market_data/{symbol}_1m_history.csv"), index=False)

            if candle_start % 900 == 0:
                res_15 = await api.ticks_history({'ticks_history': symbol, 'end': 'latest', 'count': 1, 'granularity': 900, 'style': 'candles'})
                if 'candles' in res_15:
                    df_15_new = pd.DataFrame(res_15['candles'])
                    for col in ['open', 'high', 'low', 'close', 'epoch']: df_15_new[col] = pd.to_numeric(df_15_new[col])
                    self.history_15m[symbol] = pd.concat([self.history_15m[symbol], df_15_new]).drop_duplicates('epoch').tail(100)

            signals = self.calculate_strategy(symbol)
            await self.process_signals(symbol, signals, api)

    def calculate_strategy(self, symbol):
        df_with_signals = crash_boom_mtf_strategy(self.history_1m[symbol], self.history_15m[symbol], symbol)
        last_signal = df_with_signals.iloc[-2]
        rsi = round(last_signal.get('rsi', 0), 2)
        trend = "UP" if last_signal.get('15m_trend_up') else "DOWN"
        self.log(f"[{symbol}] Analysis: RSI={rsi} | 15m Trend={trend}")
        return {'buy': last_signal.get('buy', False), 'sell': last_signal.get('sell', False)}

    async def process_signals(self, symbol, signals, api):
        target_direction = STRATEGY_CONFIG[symbol]['direction']
        entry_signal = signals['buy'] if target_direction == 'buy' else signals['sell']
        opposite_signal = signals['sell'] if target_direction == 'buy' else signals['buy']

        if opposite_signal:
            with self.positions_lock: has_pos = symbol in self.active_positions
            if has_pos:
                self.log(f"[{symbol}] Reversal signal detected. Closing existing position.")
                await self.close_position(symbol, api)

        if entry_signal:
            with self.positions_lock: has_pos = symbol in self.active_positions
            if not has_pos:
                self.log(f"[{symbol}] Strategy match! Entering position...")
                await self.place_trade_isolated(symbol, api)

    async def close_position(self, symbol, api):
        with self.positions_lock:
            contract_id = self.active_positions.get(symbol)
        if not contract_id: return
        try:
            res = await asyncio.wait_for(api.sell({"sell": contract_id, "price": 0}), timeout=15)
            if 'sell' in res:
                self.log(f"SUCCESS: {symbol} position {contract_id} closed.")
                with self.positions_lock:
                    if self.active_positions.get(symbol) == contract_id: del self.active_positions[symbol]
            else:
                err = res.get('error', {}).get('message', 'Unknown error')
                if "invalid contract" in err.lower() or "not found" in err.lower():
                    with self.positions_lock:
                        if self.active_positions.get(symbol) == contract_id: del self.active_positions[symbol]
        except Exception as e: self.log(f"[{symbol}] Close error: {e}")

    async def place_trade_isolated(self, symbol, api):
        with self.positions_lock:
            if symbol in self.active_positions: return
        config = STRATEGY_CONFIG[symbol]
        actual_stake = self.stake
        if self.stake_type == 'percent':
            actual_stake = max(1.0, round(self.balance * (self.stake / 100.0), 2))
        tp_usd = round(actual_stake * (config['tp_roi'] / 100.0), 2)
        sl_usd = round(min(actual_stake * 0.9, abs(actual_stake * (config['sl_roi'] / 100.0))), 2)

        self.log(f"[{symbol}] Executing {config['direction']} | Stake: ${actual_stake}")
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
                with self.positions_lock: self.active_positions[symbol] = cid
                self.log(f"SUCCESS: {symbol} trade active. ID: {cid}")
                asyncio.create_task(self.monitor_contract_isolated(symbol, cid, api))
            else: self.log(f"[{symbol}] Execution ERROR: {res.get('error', {}).get('message')}")
        except Exception as e: self.log(f"[{symbol}] Placement exception: {e}")

    async def monitor_contract_isolated(self, symbol, contract_id, api):
        try:
            sub = await api.subscribe({"proposal_open_contract": 1, "contract_id": contract_id})
            queue = asyncio.Queue()
            loop = asyncio.get_running_loop()
            s_obj = sub.subscribe(on_next=lambda m: loop.call_soon_threadsafe(queue.put_nowait, m))
            try:
                while self.is_running:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                        if 'proposal_open_contract' in msg:
                            c = msg['proposal_open_contract']
                            if c.get('is_expired') or c.get('status') != 'open':
                                self.log(f"[{symbol}] Position closed. Profit: ${c.get('profit', 0)}")
                                with self.positions_lock:
                                    if self.active_positions.get(symbol) == contract_id: del self.active_positions[symbol]
                                break
                    except asyncio.TimeoutError: continue
            finally: s_obj.dispose()
        except Exception as e:
            self.log(f"[{symbol}] Monitor error: {e}")
            with self.positions_lock:
                if self.active_positions.get(symbol) == contract_id: del self.active_positions[symbol]

    async def sync_open_positions(self, api):
        try:
            res = await asyncio.wait_for(api.portfolio(), timeout=15)
            if 'portfolio' in res:
                contracts = res['portfolio'].get('contracts', [])
                found_symbols = set()

                try: current_loop = asyncio.get_running_loop()
                except: current_loop = None

                with self.positions_lock:
                    for c in contracts:
                        symbol = c.get('symbol')
                        cid = c.get('contract_id')
                        if symbol in ['BOOM500', 'CRASH500'] and cid:
                            found_symbols.add(symbol)
                            if self.active_positions.get(symbol) != cid:
                                self.active_positions[symbol] = cid
                                self.log(f"Resuming monitoring for existing {symbol} position: {cid}")
                                if current_loop: current_loop.create_task(self.monitor_contract_isolated(symbol, cid, api))
                    for symbol in list(self.active_positions.keys()):
                        if symbol not in found_symbols:
                            self.log(f"Cleaning up inactive tracking for {symbol}")
                            del self.active_positions[symbol]
        except Exception as e: self.log(f"Portfolio sync error: {e}")

    async def stop(self):
        self.is_running = False
        self.log("Bot shutdown sequence initiated.")
