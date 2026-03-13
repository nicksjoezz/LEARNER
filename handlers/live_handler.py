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
            # Clear any old API instance to avoid Bad File Descriptor
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
            self.bot.log("Subscribing to balance and contract updates...")
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
        self.bot.log(f"Fetching initial historical data for {symbol} (500 candles)...")
        try:
            # Using primary API for history fetch
            response = await asyncio.wait_for(self.api.ticks_history({
                'ticks_history': symbol,
                'end': 'latest',
                'count': 500,
                'granularity': 300,
                'style': 'candles'
            }), timeout=30)

            if 'candles' in response:
                df = pd.DataFrame(response['candles'])
                df = df.sort_values('epoch')
                # Ensure float types to avoid TypeError during tick updates
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = df[col].astype(float)
                self.history_df = df
                self.last_candle_epoch = int(df.iloc[-1]['epoch'])
                self.bot.log(f"Initial history loaded: {len(df)} candles.")
                return True
        except Exception as e:
            self.bot.log(f"History fetch error: {e}")
        return False

    async def ohlc_subscription_loop(self, symbol):
        """
        Resilient loop using 'ticks' subscription to build OHLC candles manually.
        This avoids common hangs in the 'ohlc' subscription type.
        """
        while self.bot.is_running:
            try:
                self.bot.log(f"Starting tick stream for {symbol}...")

                # Subscribe to ticks for higher reliability
                sub = await self.api.subscribe({'ticks': symbol, 'subscribe': 1})
                sub.subscribe(lambda data: self.ohlc_queue.put_nowait(data))

                self.bot.log(f"Tick stream active. Monitoring for signal conditions...")

                while self.bot.is_running:
                    try:
                        # 60s timeout for individual ticks - very aggressive reconnection
                        data = await asyncio.wait_for(self.ohlc_queue.get(), timeout=60)

                        if 'tick' in data:
                            self.handle_tick(data['tick'])

                        # Periodic heartbeat/ping
                        self.tick_count += 1
                        if self.tick_count % 100 == 0:
                            await self.api.ping({'ping': 1})
                            self.bot.update_status()

                    except asyncio.TimeoutError:
                        self.bot.log("Tick stream timeout. Reconnecting...")
                        break
                    except Exception as e:
                        self.bot.log(f"Stream processing error: {e}")
                        break
            except Exception as e:
                if self.bot.is_running:
                    self.bot.log(f"Subscription failed: {e}. Retrying in 5s...")
                    await asyncio.sleep(5)
                else: break

    def handle_tick(self, tick):
        price = float(tick['quote'])
        epoch = int(tick['epoch'])
        # Current 5-minute candle start time
        candle_start = (epoch // 300) * 300

        if self.history_df.empty:
            # Should not happen as we fetch history first
            return

        last_idx = self.history_df.index[-1]
        last_candle = self.history_df.iloc[-1]

        if candle_start == last_candle['epoch']:
            # Update current candle
            self.history_df.at[last_idx, 'close'] = price
            if price > last_candle['high']: self.history_df.at[last_idx, 'high'] = price
            if price < last_candle['low']: self.history_df.at[last_idx, 'low'] = price
        elif candle_start > last_candle['epoch']:
            # NEW CANDLE STARTED
            self.bot.log(f"CANDLE CLOSED: {time.strftime('%H:%M:%S', time.gmtime(last_candle['epoch']))}")

            # 1. Add new candle starting with this tick (previous candle is now at index -2)
            new_row = {
                'epoch': candle_start,
                'open': price,
                'high': price,
                'low': price,
                'close': price
            }
            # Ensure float types for the new row
            new_df = pd.DataFrame([new_row])
            for col in ['open', 'high', 'low', 'close']:
                new_df[col] = new_df[col].astype(float)

            self.history_df = pd.concat([self.history_df, new_df], ignore_index=True)
            if len(self.history_df) > 1000:
                self.history_df = self.history_df.iloc[-1000:]

            self.last_candle_epoch = candle_start

            # 2. Trigger signal check on the JUST CLOSED candle (index -2)
            asyncio.create_task(self.check_signals())

    async def check_signals(self):
        # Always print signal status to logs as requested
        side = await self.strategy_handler.check_signals(self.history_df)
        if side:
            await self.place_trade(side)

    async def place_trade(self, side):
        # Close opposite trades first (Reversal logic)
        opposite = 'PUT' if side == 'CALL' else 'CALL'
        await self.trade_handler.close_trades_by_side(opposite, self.api)

        await self.trade_handler.place_trade(
            self.api, side, self.bot.config['symbol'], self.bot.config['strategy']
        )

    async def disconnect(self):
        if self.api:
            try:
                await asyncio.wait_for(self.api.disconnect(), timeout=5)
            except: pass
            self.api = None
