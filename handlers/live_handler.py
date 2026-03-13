import asyncio
import pandas as pd
import numpy as np
import time
import os
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

            # Use thread-safe wrappers for subscriptions
            poc_sub = await self.api.subscribe({'proposal_open_contract': 1, 'subscribe': 1})
            poc_sub.subscribe(lambda data: self.loop.call_soon_threadsafe(self.handle_contract_update, data))

            bal_sub = await self.api.subscribe({'balance': 1, 'subscribe': 1})
            bal_sub.subscribe(lambda data: self.loop.call_soon_threadsafe(self.handle_balance_update, data))
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
        self.bot.log(f"Fetching initial history for {symbol}...")
        try:
            # Simple, single-call history fetch
            response = await asyncio.wait_for(self.api.ticks_history({
                'ticks_history': symbol,
                'end': 'latest',
                'count': 1000,
                'granularity': 300,
                'style': 'candles'
            }), timeout=60)

            if 'candles' in response:
                df = pd.DataFrame(response['candles'])
                df = df.sort_values('epoch')
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = df[col].astype(float)
                self.history_df = df
                self.last_candle_epoch = int(df.iloc[-1]['epoch'])
                self.bot.log(f"History loaded: {len(df)} candles.")
                return True
            else:
                self.bot.log(f"History fetch error: {response.get('error', {}).get('message')}")
        except Exception as e:
            self.bot.log(f"History fetch error: {e}")
        return False

    async def start_trading(self, symbol):
        """
        Starts the tick subscription and bridges it to the event loop.
        """
        try:
            self.bot.log(f"Starting tick stream for {symbol}...")
            self.subscription = await self.api.subscribe({'ticks': symbol, 'subscribe': 1})

            # Bridge the background callback thread to the main event loop thread
            self.subscription.subscribe(lambda data: self.loop.call_soon_threadsafe(self.handle_tick_data, data))

            self.bot.log(f"Tick stream active. Monitoring {symbol}...")
            return True
        except Exception as e:
            self.bot.log(f"Failed to start tick stream: {e}")
            return False

    def handle_tick_data(self, data):
        if not self.bot.is_running: return

        if 'tick' in data:
            tick = data['tick']
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

                # Create new candle FIRST
                new_row = pd.DataFrame([{
                    'epoch': candle_start,
                    'open': price, 'high': price, 'low': price, 'close': price
                }])
                for col in ['open', 'high', 'low', 'close']: new_row[col] = new_row[col].astype(float)
                self.history_df = pd.concat([self.history_df, new_row], ignore_index=True)
                if len(self.history_df) > 1000: self.history_df = self.history_df.iloc[-1000:]
                self.last_candle_epoch = candle_start

                # Trigger signal check on the CLOSED candle (now at index -2)
                asyncio.create_task(self.check_signals())

            self.tick_count += 1
            if self.tick_count % 50 == 0:
                self.bot.log(f"Tick: {price} (Total: {self.tick_count})")
                # Periodic keep-alive ping
                asyncio.create_task(self.api.ping({'ping': 1}))

    async def check_signals(self):
        # Mandatory status print inside check_signals
        side = await self.strategy_handler.check_signals(self.history_df)
        if side:
            await self.place_trade(side)

    async def place_trade(self, side):
        # Reversal: close opposite
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
