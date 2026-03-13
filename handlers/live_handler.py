import asyncio
import pandas as pd
import numpy as np
import time
import os
import json
import logging
from deriv_api import DerivAPI
from handlers.strategy_handler import StrategyHandler
from handlers.trade_handler import TradeHandler
from datetime import datetime, timedelta

class LiveHandler:
    def __init__(self, bot):
        self.bot = bot
        self.api = None
        self.history_df = pd.DataFrame()
        self.last_candle_epoch = 0
        self.ohlc_queue = asyncio.Queue()
        self.strategy_handler = StrategyHandler(bot)
        self.trade_handler = TradeHandler(bot)
        self.tick_count = 0

    async def connect(self, config):
        try:
            if self.api:
                try: await asyncio.wait_for(self.api.disconnect(), timeout=5)
                except: pass

            self.api = DerivAPI(app_id=config.get('app_id', '62845'))
            auth = await asyncio.wait_for(self.api.authorize(config['api_token']), timeout=30)
            self.bot.balance = float(auth['authorize']['balance'])
            self.bot.log(f"Connected to Deriv. Balance: ${self.bot.balance:.2f}")
            return True
        except Exception as e:
            self.bot.log(f"Connection error: {e}")
            return False

    async def subscribe_account(self):
        try:
            self.bot.log("Subscribing to account updates...")
            poc_sub = await self.api.subscribe({'proposal_open_contract': 1, 'subscribe': 1})
            poc_sub.subscribe(self.handle_contract_update)

            bal_sub = await self.api.subscribe({'balance': 1, 'subscribe': 1})
            bal_sub.subscribe(self.handle_balance_update)
            return True
        except Exception as e:
            self.bot.log(f"Account subscription error: {e}")
            return False

    def handle_balance_update(self, data):
        if 'balance' in data:
            self.bot.balance = float(data['balance']['balance'])
            self.bot.update_status()

    def handle_contract_update(self, data):
        if 'proposal_open_contract' in data:
            self.trade_handler.handle_contract_update(data['proposal_open_contract'])

    async def fetch_history(self, symbol):
        self.bot.log(f"Fetching initial history for {symbol} (1000 candles)...")

        all_candles = []
        try:
            # Using a dedicated connection and fetching in chunks for reliability
            temp_api = DerivAPI(app_id=self.bot.config.get('app_id', '62845'))
            await asyncio.wait_for(temp_api.authorize(self.bot.config['api_token']), timeout=20)

            current_end = "latest"
            for chunk_idx in range(4): # 4 * 250 = 1000 candles
                success = False
                for attempt in range(3):
                    try:
                        self.bot.log(f"Fetching history chunk {chunk_idx+1}/4 (Attempt {attempt+1})...")
                        response = await asyncio.wait_for(temp_api.ticks_history({
                            'ticks_history': symbol,
                            'end': current_end,
                            'count': 250,
                            'granularity': 300,
                            'style': 'candles'
                        }), timeout=30)

                        if 'candles' in response:
                            candles = response['candles']
                            if candles:
                                all_candles.extend(candles)
                                current_end = str(candles[0]['epoch'] - 1)
                                success = True
                                break
                            else:
                                success = True # No more data
                                break
                    except Exception as e:
                        self.bot.log(f"Chunk error: {e}")
                        await asyncio.sleep(2)
                if not success: break

            await temp_api.disconnect()

            if len(all_candles) >= 200:
                df = pd.DataFrame(all_candles)
                df = df.drop_duplicates(subset=['epoch']).sort_values('epoch')
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = df[col].astype(float)
                self.history_df = df
                self.last_candle_epoch = int(df.iloc[-1]['epoch'])
                self.bot.log(f"History loaded: {len(df)} candles.")
                return True
        except Exception as e:
            self.bot.log(f"Initial history fetch failed: {e}")

        # Fallback to local data if available
        try:
            filepath = os.path.join('data', f"{symbol}_5m_2y.csv")
            if os.path.exists(filepath):
                self.bot.log("API history failed. Loading from local cache...")
                df = pd.read_csv(filepath).tail(1000)
                if not df.empty:
                    df = df.sort_values('epoch')
                    for col in ['open', 'high', 'low', 'close']: df[col] = df[col].astype(float)
                    self.history_df = df
                    self.last_candle_epoch = int(df.iloc[-1]['epoch'])
                    self.bot.log(f"Cache history loaded: {len(df)} candles.")
                    return True
        except: pass

        return False

    async def ohlc_subscription_loop(self, symbol):
        """
        Manually build OHLC candles from tick data for higher reliability.
        """
        while self.bot.is_running:
            try:
                self.bot.log(f"Initializing tick stream for {symbol}...")

                while not self.ohlc_queue.empty():
                    self.ohlc_queue.get_nowait()

                sub = await self.api.subscribe({'ticks': symbol, 'subscribe': 1})
                sub.subscribe(lambda data: self.ohlc_queue.put_nowait(data))

                self.bot.log(f"Tick stream active. Monitoring {symbol}...")

                while self.bot.is_running:
                    try:
                        # Use 90s watchdog
                        data = await asyncio.wait_for(self.ohlc_queue.get(), timeout=90)

                        if 'tick' in data:
                            self.handle_tick(data['tick'])

                        self.tick_count += 1
                        if self.tick_count % 50 == 0:
                            # Periodic heartbeat with live price
                            self.bot.log(f"Live Tick: {data['tick']['quote']} (Session ticks: {self.tick_count})")
                            await self.api.ping({'ping': 1})

                    except asyncio.TimeoutError:
                        self.bot.log("Tick stream watchdog timeout (90s). Reconnecting...")
                        break
                    except Exception as e:
                        if self.bot.is_running: self.bot.log(f"Stream error: {e}")
                        break
            except Exception as e:
                if self.bot.is_running:
                    self.bot.log(f"Stream initialization failed: {e}. Retrying...")
                    await asyncio.sleep(5)
                else: break

    def handle_tick(self, tick):
        price = float(tick['quote'])
        epoch = int(tick['epoch'])
        candle_start = (epoch // 300) * 300

        if self.history_df.empty: return

        last_idx = self.history_df.index[-1]
        last_candle = self.history_df.iloc[-1]

        if candle_start == last_candle['epoch']:
            # Update current candle
            self.history_df.at[last_idx, 'close'] = price
            if price > last_candle['high']: self.history_df.at[last_idx, 'high'] = price
            if price < last_candle['low']: self.history_df.at[last_idx, 'low'] = price
        elif candle_start > last_candle['epoch']:
            # PREVIOUS CANDLE CLOSED
            self.bot.log(f"CANDLE CLOSED: {time.strftime('%H:%M:%S', time.gmtime(last_candle['epoch']))}")

            # Start new candle FIRST (so index -2 is the closed one)
            new_row = pd.DataFrame([{
                'epoch': candle_start,
                'open': price, 'high': price, 'low': price, 'close': price
            }])
            for col in ['open', 'high', 'low', 'close']: new_row[col] = new_row[col].astype(float)
            self.history_df = pd.concat([self.history_df, new_row], ignore_index=True)
            if len(self.history_df) > 1000: self.history_df = self.history_df.iloc[-1000:]
            self.last_candle_epoch = candle_start

            # NOW trigger signal check on closed candle
            asyncio.create_task(self.check_signals())

    async def check_signals(self):
        side = await self.strategy_handler.check_signals(self.history_df)
        if side:
            await self.place_trade(side)

    async def place_trade(self, side):
        # Implementation of reversal: close opposite first
        opposite = 'PUT' if side == 'CALL' else 'CALL'
        await self.trade_handler.close_trades_by_side(opposite, self.api)

        await self.trade_handler.place_trade(
            self.api, side, self.bot.config['symbol'], self.bot.config['strategy']
        )

    async def disconnect(self):
        if self.api:
            try: await asyncio.wait_for(self.api.disconnect(), timeout=5)
            except: pass
            self.api = None
