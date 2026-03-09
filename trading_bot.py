import asyncio
import pandas as pd
import numpy as np
import time
from deriv_api import DerivAPI
from strategy_utils import ut_bot
from indicators import add_indicators
from model_manager import model_manager
import logging

class TradingBot:
    def __init__(self, socketio):
        self.socketio = socketio
        self.is_running = False
        self.main_task = None
        self.config = {}
        self.balance = 0.0
        self.wins = 0
        self.losses = 0
        self.total_trades = 0
        self.api = None
        self.last_candle_epoch = 0
        self.active_contracts = {} # contract_id -> {'side', 'entry_time', 'stake'}
        self.log_history = []
        self.max_logs = 100
        self.history_df = pd.DataFrame()
        self.current_symbol = ""
        self.ohlc_subscription = None

    def log(self, message):
        timestamp = time.strftime('%H:%M:%S', time.gmtime())
        full_log = f"{timestamp} | {message}"
        logging.info(full_log)
        self.log_history.append(full_log)
        if len(self.log_history) > self.max_logs:
            self.log_history.pop(0)
        self.socketio.emit('log', message)

    def update_status(self):
        self.socketio.emit('status_update', self.get_state())

    def get_state(self):
        return {
            'active': self.is_running,
            'balance': f"{self.balance:.2f}",
            'wins': self.wins,
            'losses': self.losses,
            'total_trades': self.total_trades,
            'config': self.config,
            'last_trained': model_manager.last_trained
        }

    async def connect(self):
        try:
            app_id = self.config.get('app_id', '62845')
            self.api = DerivAPI(app_id=app_id)
            auth = await self.api.authorize(self.config['api_token'])
            self.balance = float(auth['authorize']['balance'])
            self.log(f"Connected to Deriv. Balance: ${self.balance:.2f}")
            self.update_status()

            # Subscribe to balance updates and contract results
            asyncio.create_task(self.subscribe_to_updates())
            return True
        except Exception as e:
            self.log(f"Connection error: {e}")
            return False

    async def subscribe_to_updates(self):
        try:
            # Subscribe to proposal_open_contract to get results
            poc_sub = await self.api.subscribe({'proposal_open_contract': 1, 'subscribe': 1})
            poc_sub.subscribe(self.handle_contract_update)

            # Subscribe to balance
            bal_sub = await self.api.subscribe({'balance': 1, 'subscribe': 1})
            bal_sub.subscribe(self.handle_balance_update)
        except Exception as e:
            self.log(f"Subscription error: {e}")

    def handle_balance_update(self, data):
        if 'balance' in data:
            self.balance = float(data['balance']['balance'])
            self.update_status()

    def handle_contract_update(self, data):
        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            if contract['is_sold']:
                status = contract['status'] # won, lost
                profit = float(contract['profit'])
                contract_id = contract['contract_id']

                if contract_id in self.active_contracts:
                    side = self.active_contracts[contract_id]['side']
                    if status == 'won':
                        self.wins += 1
                        self.log(f"PROFIT: {side} trade won! +${profit:.2f}")
                    else:
                        self.losses += 1
                        self.log(f"LOSS: {side} trade lost. -${abs(profit):.2f}")

                    del self.active_contracts[contract_id]
                    self.update_status()

    async def get_ml_filter(self, symbol, strategy_idx):
        ml = model_manager.get_model(symbol, strategy_idx)
        return ml

    def reset_metrics(self):
        self.wins = 0
        self.losses = 0
        self.total_trades = 0
        self.log_history = []
        self.active_contracts = {}
        self.last_candle_epoch = 0
        self.history_df = pd.DataFrame()

    async def start(self, config):
        self.reset_metrics()
        self.config = config
        self.is_running = True
        self.current_symbol = config['symbol']
        if await self.connect():
            # Initial history fetch (500 candles for indicators)
            await self.fetch_initial_history()
            # Start OHLC subscription
            self.main_task = asyncio.create_task(self.ohlc_subscription_loop())
        else:
            self.is_running = False
            self.update_status()

    async def fetch_initial_history(self):
        symbol = self.config['symbol']
        self.log(f"Fetching initial historical data for {symbol} (500 candles)...")
        try:
            response = await self.api.ticks_history({
                'ticks_history': symbol,
                'end': 'latest',
                'count': 500,
                'granularity': 300,
                'style': 'candles'
            })
            if 'candles' in response:
                self.history_df = pd.DataFrame(response['candles'])
                # Last candle is usually the building one
                self.last_candle_epoch = self.history_df.iloc[-1]['epoch']
                self.log(f"Initial history loaded: {len(self.history_df)} candles.")
        except Exception as e:
            self.log(f"Error fetching initial history: {e}")

    async def stop(self):
        self.is_running = False
        if self.main_task:
            self.main_task.cancel()
            try:
                await self.main_task
            except asyncio.CancelledError:
                pass
            self.main_task = None

        if self.api:
            await self.api.disconnect()
            self.api = None

        self.log("Bot stopped.")
        self.update_status()

    async def ohlc_subscription_loop(self):
        symbol = self.config['symbol']
        try:
            self.ohlc_subscription = await self.api.subscribe({
                'ticks_history': symbol,
                'subscribe': 1,
                'end': 'latest',
                'granularity': 300,
                'style': 'candles'
            })
            self.ohlc_subscription.subscribe(self.handle_ohlc_update)

            # Keep the task alive
            while self.is_running:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            self.log("OHLC subscription cancelled.")
        except Exception as e:
            self.log(f"OHLC subscription error: {e}")
        finally:
            self.is_running = False

    def handle_ohlc_update(self, data):
        if 'ohlc' in data:
            ohlc = data['ohlc']
            candle_epoch = int(ohlc['open_time'])

            # Detect new candle (candle closed)
            if candle_epoch > self.last_candle_epoch:
                self.log(f"CANDLE CLOSED: {time.ctime(self.last_candle_epoch)}")

                # The closed candle data should be updated in history_df
                # Deriv OHLC updates are for the building candle.
                # So the one that just 'closed' is actually the previous building one.
                # To be absolutely sure, we trigger an update of the history buffer.
                asyncio.create_task(self.update_history_and_check_signals(candle_epoch))
                self.last_candle_epoch = candle_epoch

    async def update_history_and_check_signals(self, new_candle_epoch):
        # Brief delay to allow backend to finalize history
        await asyncio.sleep(1)

        symbol = self.config['symbol']
        try:
            # We only need the latest completed candle to update our buffer
            response = await self.api.ticks_history({
                'ticks_history': symbol,
                'end': 'latest',
                'count': 2, # current building + previous closed
                'granularity': 300,
                'style': 'candles'
            })

            if 'candles' in response and len(response['candles']) >= 2:
                latest_closed_candle = response['candles'][0]

                # Append to history_df
                new_row = pd.DataFrame([latest_closed_candle])
                self.history_df = pd.concat([self.history_df, new_row]).drop_duplicates(subset=['epoch']).sort_values('epoch')

                # Keep buffer size to 500
                if len(self.history_df) > 500:
                    self.history_df = self.history_df.iloc[-500:]

                # Check Signals on the buffer
                await self.check_signals()
        except Exception as e:
            self.log(f"Error updating history buffer: {e}")

    async def check_signals(self):
        if len(self.history_df) < 200:
            self.log(f"Buffer insufficient ({len(self.history_df)} candles). Need at least 200.")
            return

        symbol = self.config['symbol']
        strategy_idx = int(self.config['strategy'])

        strat_params = [
            (1, 10), (2, 20), (3, 30), (1, 20), (2, 10),
            (3, 20), (1, 30), (2, 30), (3, 10), (1.5, 15)
        ]
        a, c = strat_params[strategy_idx-1]

        try:
            # Use in-memory history_df for calculations
            df_calc = self.history_df.copy()
            df_calc = add_indicators(df_calc)
            df_ut = ut_bot(df_calc, a=a, c=c)

            raw_sig = df_ut.iloc[-1]
            buy_triggered = raw_sig['buy']
            sell_triggered = raw_sig['sell']

            if buy_triggered or sell_triggered:
                side = 'BUY' if buy_triggered else 'SELL'
                self.log(f"Signal found: {side}. Verifying with Neural Filter (Symbol: {symbol} Strategy: {strategy_idx})...")

                # ML Filter verification using freshly (re)trained model
                ml = await self.get_ml_filter(symbol, strategy_idx)
                if ml:
                    df_ml = ml.filter_signals(df_ut)
                    ml_sig = df_ml.iloc[-1]
                    if ml_sig['buy'] or ml_sig['sell']:
                        self.log(f"NEURAL FILTER: SIGNAL PASSED. Executing {side} trade.")
                        await self.place_trade('CALL' if buy_triggered else 'PUT')
                    else:
                        self.log(f"NEURAL FILTER: SIGNAL BLOCKED (Low probability).")
                else:
                    self.log(f"ML filter missing or training. Executing raw {side} trade.")
                    await self.place_trade('CALL' if buy_triggered else 'PUT')
            else:
                self.log("No signal found.")

        except Exception as e:
            self.log(f"Error during signal check: {e}")

    async def place_trade(self, side):
        try:
            # Avoid double entry
            if any(c['side'] == side for c in self.active_contracts.values()):
                self.log(f"Already have an active {side} trade. Skipping.")
                return

            stake_pc = float(self.config.get('trade_pc', 1))
            amount = self.balance * (stake_pc / 100.0)
            amount = round(max(amount, 0.35), 2)

            self.log(f"PLACING {side} TRADE - Stake: ${amount:.2f}")

            proposal = await self.api.buy({
                "buy": 1,
                "price": amount,
                "parameters": {
                    "amount": amount,
                    "basis": "stake",
                    "contract_type": "CALL" if side == "CALL" else "PUT",
                    "currency": "USD",
                    "duration": 15,
                    "duration_unit": "m",
                    "symbol": self.config['symbol']
                }
            })

            if 'buy' in proposal:
                contract_id = proposal['buy']['contract_id']
                self.total_trades += 1
                self.active_contracts[contract_id] = {'side': side, 'stake': amount}
                self.log(f"SUCCESS: {side} trade placed! ID: {contract_id} | Stake: ${amount:.2f}")
            else:
                err = proposal.get('error', {}).get('message', 'Unknown error')
                self.log(f"EXECUTION ERROR: Failed to place {side} trade. Reason: {err}")

            self.update_status()

        except Exception as e:
            self.log(f"Trade placement error: {e}")

    async def main_loop(self):
        pass
