import asyncio
import json
import os
import logging
import time

class TradeHandler:
    def __init__(self, bot):
        self.bot = bot
        self.active_contracts = {}
        self.trade_history_file = 'trade_history.json'
        self.load_trade_history()

    def load_trade_history(self):
        if os.path.exists(self.trade_history_file):
            try:
                with open(self.trade_history_file, 'r') as f:
                    self.trade_history = json.load(f)
            except:
                self.trade_history = []
        else:
            self.trade_history = []

    def save_trade_history(self):
        try:
            with open(self.trade_history_file, 'w') as f:
                json.dump(self.trade_history[-100:], f) # Keep last 100
        except Exception as e:
            logging.error(f"Error saving trade history: {e}")

    async def place_trade(self, api, side, symbol, strategy_idx):
        try:
            raw_amount = self.bot.balance * (float(self.bot.config.get('trade_pc', 1)) / 100.0)
            amount = round(max(raw_amount, 0.35), 2)
            if amount > 50: amount = 10.0

            self.bot.log(f"PLACING {side} - Stake: ${amount} (Strat {strategy_idx})")

            trade_params = {
                "buy": 1, "price": amount,
                "parameters": {
                    "amount": amount, "basis": "stake",
                    "contract_type": side, "currency": "USD",
                    "duration": 15, "duration_unit": "m",
                    "symbol": symbol
                }
            }

            # Use wait_for only if 'api' is an async object, otherwise just await
            res = await api.buy(trade_params)

            # If the response is handled in on_message, we might not get it here
            # But for standard DerivAPI it returns the dict.
            if res and 'buy' in res:
                cid = res['buy']['contract_id']
                self.bot.total_trades += 1
                self.active_contracts[cid] = {'side': side, 'stake': amount, 'time': time.time()}
                self.bot.log(f"Trade placed SUCCESS: {cid}")
                return cid
        except Exception as e:
            self.bot.log(f"Trade placement error: {e}")
        return None

    async def close_trades_by_side(self, side, api=None):
        if not api: return
        for cid, details in list(self.active_contracts.items()):
            if details['side'] == side:
                self.bot.log(f"REVERSAL: Closing {side} contract {cid}")
                try:
                    await api.sell({"sell": cid, "price": 0})
                except Exception as e:
                    self.bot.log(f"Reversal error: {e}")

    def handle_contract_update(self, contract):
        if not contract: return
        cid = contract.get('contract_id')
        if not cid: return

        if contract.get('is_sold'):
            status = contract.get('status')
            profit = float(contract.get('profit', 0))

            if cid in self.active_contracts:
                side = self.active_contracts[cid]['side']
                if status == 'won':
                    self.bot.wins += 1
                    self.bot.log(f"PROFIT: {side} trade won! +${profit:.2f}")
                else:
                    self.bot.losses += 1
                    self.bot.log(f"LOSS: {side} trade lost. -${abs(profit):.2f}")

                self.trade_history.append({
                    'id': cid, 'side': side, 'profit': profit, 'status': status, 'time': time.time()
                })
                del self.active_contracts[cid]
                self.save_trade_history()
                self.bot.save_state()
                self.bot.update_status()
        else:
            # New contract opened?
            if cid not in self.active_contracts and contract.get('status') == 'open':
                side = contract.get('contract_type')
                amount = float(contract.get('buy_price', 0))
                self.active_contracts[cid] = {'side': side, 'stake': amount, 'time': time.time()}
                self.bot.total_trades += 1
                self.bot.update_status()
