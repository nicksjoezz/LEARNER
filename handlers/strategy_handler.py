import pandas as pd
import time
from strategy_utils import ut_bot
from indicators import add_indicators
from model_manager import model_manager

class StrategyHandler:
    def __init__(self, bot):
        self.bot = bot
        self.strat_params = [
            (1, 10), (2, 20), (3, 30), (1, 20), (2, 10),
            (3, 20), (1, 30), (2, 30), (3, 10), (1.5, 15)
        ]

    async def check_signals(self, history_df):
        if len(history_df) < 200:
            return None

        symbol = self.bot.config['symbol']
        strategy_idx = int(self.bot.config['strategy'])
        a, c = self.strat_params[strategy_idx-1]

        try:
            # Indicator calculation
            df = history_df.copy()
            df = add_indicators(df)
            df = ut_bot(df, a=a, c=c)

            # Check signal on the last closed candle (index -2)
            raw_sig = df.iloc[-2]
            buy_triggered = raw_sig['buy']
            sell_triggered = raw_sig['sell']

            if buy_triggered or sell_triggered:
                side = 'BUY' if buy_triggered else 'SELL'
                self.bot.log(f"SIGNAL STATUS: Signal found ({side}). Verifying with Neural Filter...")

                # ML Filter
                ml = model_manager.get_model(symbol, strategy_idx)
                if ml:
                    df_ml = ml.filter_signals(df)
                    ml_sig = df_ml.iloc[-2]
                    if ml_sig['buy'] or ml_sig['sell']:
                        self.bot.log(f"NEURAL FILTER: [PASSED] Executing {side} trade.")
                        return 'CALL' if buy_triggered else 'PUT'
                    else:
                        self.bot.log(f"NEURAL FILTER: [BLOCKED] Signal filtered as low probability.")
                else:
                    self.bot.log(f"NEURAL FILTER: [INACTIVE] Model not ready. Executing raw {side} signal.")
                    return 'CALL' if buy_triggered else 'PUT'
            else:
                self.bot.log(f"SIGNAL STATUS: No signal found on closed candle.")

        except Exception as e:
            self.bot.log(f"StrategyHandler Error: {e}")

        return None
