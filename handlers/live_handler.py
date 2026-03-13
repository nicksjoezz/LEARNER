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
            self.api = DerivAPI(app_id=config.get('app_id', '62845'))
            auth = await asyncio.wait_for(self.api.authorize(config['api_token']), timeout=30)
            self.bot.balance = float(auth['authorize']['balance'])
            self.bot.log(f"Connected to Deriv. Balance: ${self.bot.balance:.2f}")
            return True
        except Exception as e:
            self.bot.log(f"LiveHandler Connection error: {e}")
            return False

    async def subscribe_account(self):
        try:
            self.bot.log("Subscribing to account updates...")
            # Subscribe to updates
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
            self.bot.save_state()
            self.bot.update_status()

    def handle_contract_update(self, data):
        if 'proposal_open_contract' in data:
            self.trade_handler.handle_contract_update(data['proposal_open_contract'])

    async def fetch_history(self, symbol):
        self.bot.log(f"Fetching fresh history for {symbol} (1000 candles)...")

        # Use a fresh connection for fetching if the primary one is busy or stuck
        # This often resolves "stuck" history requests in python-deriv-api
        temp_api = DerivAPI(app_id=self.bot.config.get('app_id', '62845'))
        try:
            await asyncio.wait_for(temp_api.authorize(self.bot.config['api_token']), timeout=20)

            all_candles = []
            current_end = "latest"

            for i in range(4): # 4 chunks of 250 = 1000
                success = False
                for attempt in range(3):
                    try:
                        self.bot.log(f"Fetching history chunk {i+1}/4 (Attempt {attempt+1})...")
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
                        else:
                            self.bot.log(f"Chunk failed: {response.get('error', {}).get('message')}")
                    except Exception as e:
                        self.bot.log(f"Chunk error: {e}")
                    await asyncio.sleep(2)
                if not success: break

            if len(all_candles) >= 200:
                df = pd.DataFrame(all_candles)
                df = df.drop_duplicates(subset=['epoch']).sort_values('epoch')
                self.history_df = df
                self.last_candle_epoch = int(df.iloc[-1]['epoch'])
                self.bot.log(f"History loaded: {len(df)} candles.")
                return True

        except Exception as e:
            self.bot.log(f"History fetch failed: {e}")
        finally:
            try:
                await asyncio.wait_for(temp_api.disconnect(), timeout=5)
            except: pass

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
                # Optimized update of current candle
                self.history_df.loc[self.history_df.index[-1], ['open', 'high', 'low', 'close']] = \
                    [new_candle['open'], new_candle['high'], new_candle['low'], new_candle['close']]
            else:
                self.history_df = pd.concat([self.history_df, pd.DataFrame([new_candle])], ignore_index=True)
                if len(self.history_df) > 1000:
                    self.history_df = self.history_df.iloc[-1000:]

            if epoch > self.last_candle_epoch:
                if self.last_candle_epoch != 0:
                    self.bot.log(f"CANDLE CLOSED: {time.ctime(self.last_candle_epoch)}")
                    # Trigger signal check immediately
                    asyncio.create_task(self.check_signals())
                else:
                    self.bot.log(f"Bot session active. Current candle open time: {time.ctime(epoch)}")
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
