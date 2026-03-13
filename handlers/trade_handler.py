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
            # Calculate stake based on current balance and config percentage
            raw_amount = self.bot.balance * (float(self.bot.config.get('trade_pc', 1)) / 100.0)
            amount = round(max(raw_amount, 0.35), 2)

            # CRITICAL: Cap stake for demo accounts to avoid 'maximum purchase price' errors
            # if the account has large synthetic balance.
            if amount > 50: amount = 10.0

            self.bot.log(f"PLACING {side} - Stake: ${amount} (Strat {strategy_idx})")

            # Parameters for Rise/Fall (CALL/PUT)
            trade_params = {
                "buy": 1,
                "price": amount,
                "parameters": {
                    "amount": amount,
                    "basis": "stake",
                    "contract_type": side,
                    "currency": "USD",
                    "duration": 15,
                    "duration_unit": "m",
                    "symbol": symbol
                }
            }

            r = await asyncio.wait_for(api.buy(trade_params), timeout=20)

            if 'buy' in r:
                cid = r['buy']['contract_id']
                self.bot.total_trades += 1
                self.active_contracts[cid] = {'side': side, 'stake': amount, 'time': time.time()}
                self.bot.save_state()
                self.bot.log(f"Trade placed SUCCESS: {cid}")
                return cid
            else:
                err_msg = r.get('error', {}).get('message', 'Unknown error')
                self.bot.log(f"Trade placement FAILED: {err_msg}")
        except Exception as e:
            self.bot.log(f"Trade placement ERROR: {e}")
        return None

    async def close_trades_by_side(self, side, api=None):
        """Closes all open trades of a specific type (reversal logic)."""
        if not api: return
        for cid, details in list(self.active_contracts.items()):
            if details['side'] == side:
                self.bot.log(f"REVERSAL: Closing {side} contract {cid} for opposite signal.")
                try:
                    # Deriv sell requires contract_id
                    await api.sell({"sell": cid, "price": 0})
                except Exception as e:
                    self.bot.log(f"Reversal sell error for {cid}: {e}")

    def handle_contract_update(self, contract):
        if contract['is_sold']:
            status = contract['status'] # won, lost
            profit = float(contract['profit'])
            cid = contract['contract_id']

            if cid in self.active_contracts:
                side = self.active_contracts[cid]['side']
                if status == 'won':
                    self.bot.wins += 1
                    self.bot.log(f"PROFIT: {side} trade won! +${profit:.2f}")
                else:
                    self.bot.losses += 1
                    self.bot.log(f"LOSS: {side} trade lost. -${abs(profit):.2f}")

                self.trade_history.append({
                    'id': cid,
                    'side': side,
                    'profit': profit,
                    'status': status,
                    'time': time.time()
                })
                del self.active_contracts[cid]
                self.save_trade_history()
                self.bot.save_state()
                self.bot.update_status()
