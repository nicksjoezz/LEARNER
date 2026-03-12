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

class LiveHandler:
    def __init__(self, bot):
        self.bot = bot
        self.api = None
        self.history_df = pd.DataFrame()
        self.last_candle_epoch = 0
        self.ohlc_subscription = None
        self.strategy_handler = StrategyHandler(bot)
        self.trade_handler = TradeHandler(bot)

    async def connect(self, config):
        try:
            self.api = DerivAPI(app_id=config['app_id'])
            auth = await asyncio.wait_for(self.api.authorize(config['api_token']), timeout=20)
            self.bot.balance = float(auth['authorize']['balance'])
            self.bot.log(f"Connected to Deriv. Balance: ${self.bot.balance:.2f}")

            # Subscribe to updates
            poc_sub = await self.api.subscribe({'proposal_open_contract': 1, 'subscribe': 1})
            poc_sub.subscribe(self.handle_contract_update)

            bal_sub = await self.api.subscribe({'balance': 1, 'subscribe': 1})
            bal_sub.subscribe(self.handle_balance_update)

            return True
        except Exception as e:
            self.bot.log(f"LiveHandler Connection error: {e}")
            return False

    def handle_balance_update(self, data):
        if 'balance' in data:
            self.bot.balance = float(data['balance']['balance'])
            self.bot.save_state()
            self.bot.update_status()

    def handle_contract_update(self, data):
        if 'proposal_open_contract' in data:
            self.trade_handler.handle_contract_update(data['proposal_open_contract'])

    async def fetch_history(self, symbol):
        self.bot.log(f"Fetching fresh history for {symbol} (1000 candles)...")

        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                self.bot.log(f"Fetch attempt {attempt+1}/{max_attempts}...")

                # Fetch in two chunks to avoid large request timeouts
                all_candles = []
                current_end = "latest"
                for chunk in range(2):
                    response = await asyncio.wait_for(self.api.ticks_history({
                        'ticks_history': symbol,
                        'end': current_end,
                        'count': 500,
                        'granularity': 300,
                        'style': 'candles'
                    }), timeout=45)

                    if 'candles' in response:
                        candles = response['candles']
                        all_candles.extend(candles)
                        if len(candles) > 0:
                            current_end = str(candles[0]['epoch'])
                    else:
                        break

                if len(all_candles) >= 500:
                    df = pd.DataFrame(all_candles)
                    df = df.drop_duplicates(subset=['epoch']).sort_values('epoch')
                    self.history_df = df
                    self.last_candle_epoch = int(df.iloc[-1]['epoch'])
                    self.bot.log(f"History loaded: {len(df)} candles.")
                    return True
                else:
                    self.bot.log(f"Attempt {attempt+1}: Insufficient candles ({len(all_candles)}).")
            except asyncio.TimeoutError:
                self.bot.log(f"Attempt {attempt+1}: Timeout during chunk fetch.")
            except Exception as e:
                self.bot.log(f"Attempt {attempt+1}: Error fetching history: {type(e).__name__}: {e}")

            if attempt < max_attempts - 1:
                await asyncio.sleep(2)

        return False

    async def start_ohlc_subscription(self, symbol):
        try:
            self.ohlc_subscription = await self.api.subscribe({
                'ticks_history': symbol,
                'subscribe': 1,
                'end': 'latest',
                'granularity': 300,
                'style': 'candles'
            })
            self.ohlc_subscription.subscribe(self.handle_ohlc_update)
            return True
        except Exception as e:
            self.bot.log(f"Subscription error: {e}")
            return False

    def handle_ohlc_update(self, data):
        if 'ohlc' in data:
            ohlc = data['ohlc']
            epoch = int(ohlc['open_time'])

            new_candle = {
                'epoch': epoch,
                'open': float(ohlc['open']),
                'high': float(ohlc['high']),
                'low': float(ohlc['low']),
                'close': float(ohlc['close'])
            }

            if not self.history_df.empty and self.history_df.iloc[-1]['epoch'] == epoch:
                idx = self.history_df.index[-1]
                for col, val in new_candle.items():
                    self.history_df.at[idx, col] = val
            else:
                self.history_df = pd.concat([self.history_df, pd.DataFrame([new_candle])], ignore_index=True)
                if len(self.history_df) > 1000:
                    self.history_df = self.history_df.iloc[-1000:]

            if epoch > self.last_candle_epoch:
                if self.last_candle_epoch != 0:
                    self.bot.log(f"CANDLE CLOSED: {time.ctime(self.last_candle_epoch)}")
                    asyncio.create_task(self.check_signals())
                self.last_candle_epoch = epoch

    async def check_signals(self):
        side = await self.strategy_handler.check_signals(self.history_df)
        if side:
            await self.place_trade(side)

    async def place_trade(self, side):
        await self.trade_handler.place_trade(
            self.api, side, self.bot.config['symbol'], self.bot.config['strategy']
        )

    async def disconnect(self):
        if self.api:
            try:
                await asyncio.wait_for(self.api.disconnect(), timeout=5)
            except:
                pass
            self.api = None
