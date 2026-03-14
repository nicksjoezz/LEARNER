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
from datetime import datetime

class LiveHandler:
    def __init__(self, bot):
        self.bot = bot
        self.api = None
        self.history_df = pd.DataFrame()
        self.last_candle_epoch = 0
        self.strategy_handler = StrategyHandler(bot)
        self.trade_handler = TradeHandler(bot)
        self.tick_count = 0
        self.loop = asyncio.get_event_loop()
        self.subscription = None
        self.debug_mode = True # Always on for now to debug the "stuck" issue

    async def connect(self, config):
        try:
            if self.api:
                try: await asyncio.wait_for(self.api.disconnect(), timeout=5)
                except: pass

            self.bot.log(f"DEBUG: Creating DerivAPI instance (App ID: {config.get('app_id', '62845')})")
            self.api = DerivAPI(app_id=config.get('app_id', '62845'))

            self.bot.log("DEBUG: Authorizing...")
            auth = await asyncio.wait_for(self.api.authorize(config['api_token']), timeout=30)

            if 'error' in auth:
                self.bot.log(f"DEBUG: Auth Error: {auth['error'].get('message')}")
                return False

            self.bot.balance = float(auth['authorize']['balance'])
            self.bot.log(f"Connected to Deriv. Balance: ${self.bot.balance:.2f}")
            return True
        except Exception as e:
            self.bot.log(f"Connection error: {e}")
            return False

    async def subscribe_account(self):
        try:
            self.bot.log("Subscribing to account updates...")

            # Use thread-safe wrappers for subscriptions
            poc_sub = await self.api.subscribe({'proposal_open_contract': 1, 'subscribe': 1})
            poc_sub.subscribe(
                on_next=lambda data: self.loop.call_soon_threadsafe(self.handle_contract_update, data),
                on_error=lambda err: self.loop.call_soon_threadsafe(self.bot.log, f"DEBUG Contract Stream Error: {err}")
            )

            bal_sub = await self.api.subscribe({'balance': 1, 'subscribe': 1})
            bal_sub.subscribe(
                on_next=lambda data: self.loop.call_soon_threadsafe(self.handle_balance_update, data),
                on_error=lambda err: self.loop.call_soon_threadsafe(self.bot.log, f"DEBUG Balance Stream Error: {err}")
            )
            return True
        except Exception as e:
            self.bot.log(f"Account subscription error: {e}")
            return False

    def handle_balance_update(self, data):
        if self.debug_mode: self.bot.log(f"DEBUG Balance Update: {json.dumps(data)}")
        if 'balance' in data:
            self.bot.balance = float(data['balance']['balance'])
            self.bot.update_status()

    def handle_contract_update(self, data):
        if self.debug_mode: self.bot.log(f"DEBUG Contract Update: {json.dumps(data)}")
        if 'proposal_open_contract' in data:
            self.trade_handler.handle_contract_update(data['proposal_open_contract'])

    async def fetch_history(self, symbol):
        self.bot.log(f"Fetching initial history for {symbol}...")
        try:
            params = {
                'ticks_history': symbol,
                'end': 'latest',
                'count': 1000,
                'granularity': 300,
                'style': 'candles'
            }
            self.bot.log(f"DEBUG: History Request: {json.dumps(params)}")

            response = await asyncio.wait_for(self.api.ticks_history(params), timeout=60)

            if self.debug_mode:
                self.bot.log(f"DEBUG: History Response Keys: {list(response.keys())}")
                if 'error' in response:
                    self.bot.log(f"DEBUG: History Error: {response['error'].get('message')}")

            if 'candles' in response:
                candles = response['candles']
                self.bot.log(f"DEBUG: Received {len(candles)} candles from API.")

                df = pd.DataFrame(candles)
                df = df.sort_values('epoch')
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = df[col].astype(float)

                self.history_df = df
                last_epoch = int(df.iloc[-1]['epoch'])
                self.bot.log(f"History loaded: {len(df)} candles. Last candle: {time.strftime('%H:%M:%S', time.gmtime(last_epoch))}")
                return True
            else:
                self.bot.log(f"History fetch failed: No candles in response.")
        except Exception as e:
            self.bot.log(f"History fetch error: {e}")
        return False

    async def start_trading(self, symbol):
        try:
            self.bot.log(f"Starting tick stream for {symbol}...")
            self.subscription = await self.api.subscribe({'ticks': symbol, 'subscribe': 1})

            # Bridge the background callback thread to the main event loop thread
            self.subscription.subscribe(
                on_next=lambda data: self.loop.call_soon_threadsafe(self.handle_tick_data, data),
                on_error=lambda err: self.loop.call_soon_threadsafe(self.bot.log, f"DEBUG Tick Stream Error: {err}")
            )

            self.bot.log(f"Tick stream active. Monitoring {symbol}...")
            return True
        except Exception as e:
            self.bot.log(f"Failed to start tick stream: {e}")
            return False

    def handle_tick_data(self, data):
        if not self.bot.is_running: return

        if self.debug_mode:
            # Log every 10th tick to avoid cluttering but show activity
            if self.tick_count % 10 == 0:
                self.bot.log(f"DEBUG RAW TICK: {json.dumps(data)}")

        if 'tick' in data:
            tick = data['tick']
            price = float(tick['quote'])
            epoch = int(tick['epoch'])
            candle_start = (epoch // 300) * 300

            if self.history_df.empty:
                if self.tick_count % 50 == 0:
                    self.bot.log("DEBUG: history_df empty, skipping tick.")
                return

            last_idx = self.history_df.index[-1]
            last_candle_epoch = int(self.history_df.iloc[-1]['epoch'])

            if candle_start == last_candle_epoch:
                # Update current candle
                self.history_df.at[last_idx, 'close'] = price
                if price > self.history_df.at[last_idx, 'high']:
                    self.history_df.at[last_idx, 'high'] = price
                if price < self.history_df.at[last_idx, 'low']:
                    self.history_df.at[last_idx, 'low'] = price
            elif candle_start > last_candle_epoch:
                # NEW CANDLE DETECTED
                closed_epoch = last_candle_epoch
                closed_time = time.strftime('%H:%M:%S', time.gmtime(closed_epoch))
                self.bot.log(f"CANDLE CLOSED: {closed_time}. New Start: {time.strftime('%H:%M:%S', time.gmtime(candle_start))} Price: {price}")

                # 1. Add new candle first
                new_row = pd.DataFrame([{
                    'epoch': candle_start,
                    'open': price, 'high': price, 'low': price, 'close': price
                }])
                for col in ['open', 'high', 'low', 'close']:
                    new_row[col] = new_row[col].astype(float)

                self.history_df = pd.concat([self.history_df, new_row], ignore_index=True)
                if len(self.history_df) > 1200:
                    self.history_df = self.history_df.iloc[-1000:]

                # 2. Trigger signal check on closed candle (iloc[-2])
                asyncio.create_task(self.check_signals())

            self.tick_count += 1
            if self.tick_count % 50 == 0:
                self.bot.log(f"Bot Heartbeat: {price} ({self.tick_count} ticks)")
                asyncio.create_task(self.api.ping({'ping': 1}))

    async def check_signals(self):
        side = await self.strategy_handler.check_signals(self.history_df)
        if side:
            await self.place_trade(side)

    async def place_trade(self, side):
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
