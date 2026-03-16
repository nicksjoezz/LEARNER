from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import json, os, asyncio, threading, pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from trading_bot import TradingBot
from model_manager import model_manager
from ml_filter import MLFilter
from deriv_api import DerivAPI
from strategy_utils import ut_bot, Backtester, calculate_max_consecutive_losses, simulate_financials
from indicators import add_indicators
from config_utils import load_config, save_config

app = Flask(__name__)
# Switch to threading async mode
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")
bot = TradingBot(socketio)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_config')
def get_configs(): return jsonify(load_config())

@app.route('/save_settings', methods=['POST'])
def save_configs():
    save_config(request.json)
    return jsonify({'status': 'success'})

@app.route('/toggle_bot', methods=['POST'])
def toggle_bot():
    if bot.is_running:
        asyncio.run_coroutine_threadsafe(bot.stop(), bot_loop)
    else:
        c = load_config()
        if not c.get('api_token'): return jsonify({'status': 'error', 'message': 'No API Token'})

        async def start_sequence():
            if await model_manager.ensure_symbol_ready(c['symbol']):
                await bot.start(c)
            else:
                bot.log(f"Failed to prepare {c['symbol']} for live trading.")

        asyncio.run_coroutine_threadsafe(start_sequence(), bot_loop)
    return jsonify({'success': True})

@app.route('/get_system_status')
def get_sys_status():
    return jsonify({
        'is_initial_training': model_manager.is_initial_training,
        'last_trained': model_manager.last_trained,
        'bot_state': bot.get_state(),
        'logs': bot.log_history
    })


@app.route('/run_backtest', methods=['POST'])
def run_bt():
    d = request.json
    symbol = d['symbol']
    bt_days = int(d['days'])

    async def get_all_data():
        if await model_manager.ensure_symbol_ready(symbol):
            fp = os.path.join('data', f"{symbol}_5m_2y.csv")
            if os.path.exists(fp):
                return pd.read_csv(fp)
        return pd.DataFrame()

    try:
        future = asyncio.run_coroutine_threadsafe(get_all_data(), bot_loop)
        df_full = future.result(timeout=300)
    except Exception as e:
        return jsonify({'error': f"Data load error: {e}", 'results': []})

    if df_full.empty: return jsonify({'error': 'No historical data available for this symbol', 'results': []})

    # Restructure for ML Fairness
    ts_cutoff = int((datetime.now() - timedelta(days=bt_days)).timestamp())

    # 1. Backtest Set (The period we are testing)
    df_bt_raw = df_full[df_full['epoch'] >= ts_cutoff].copy()
    # 2. Training Set (Everything BEFORE the backtest period)
    df_train_raw = df_full[df_full['epoch'] < ts_cutoff].copy()

    if len(df_train_raw) < 1000:
        return jsonify({'error': 'Insufficient history for fair ML training (Need > 1000 candles before backtest start)', 'results': []})

    # Step 1: Add indicators to FULL dataset to ensure continuity
    df_all = add_indicators(df_full)

    balance = float(d.get('balance', 1000))
    risk_pc = float(d.get('risk_pc', load_config().get('trade_pc', 1)))
    params = [(1, 10), (2, 20), (3, 30), (1, 20), (2, 10), (3, 20), (1, 30), (2, 30), (3, 10), (1.5, 15)]

    def run_single_strat(args):
        i, (a, c) = args
        s_idx = i + 1

        # Step 2: Generate signals on the FULL dataset
        df_strat = ut_bot(df_all, a=a, c=c)

        # FULL ML TRANSITION:
        # Instead of just using 'buy' and 'sell' (which are strict),
        # we let ML learn from EVERY Crossover ('above' and 'below').
        # This gives the ML a much larger pool of candidates to pick from.
        df_strat['buy'] = df_strat['ut_above'] == 1
        df_strat['sell'] = df_strat['ut_below'] == 1

        # Step 3: Split signals into TRAIN and TEST portions
        # Training set: everything BEFORE ts_cutoff
        df_train = df_strat[df_strat['epoch'] < ts_cutoff].copy()
        # Test set: everything AFTER ts_cutoff (the fresh lookback days)
        df_bt = df_strat[df_strat['epoch'] >= ts_cutoff].copy()

        # --- Fair ML Training Phase ---
        # ML trains ONLY on historical signals
        tr_train = Backtester(df_train).run()
        fair_ml = MLFilter()
        is_ready = fair_ml.train(df_train, tr_train)

        # --- Backtest Phase ---
        # Raw performance on the fresh test period
        tr_raw = Backtester(df_bt).run()
        raw_bal, raw_prof, raw_mcl = simulate_financials(tr_raw, balance, risk_pc)

        # ML Filtered performance on the fresh test period
        if is_ready:
            df_filtered = fair_ml.filter_signals(df_bt)
            tr_ml = Backtester(df_filtered).run()
            ml_bal, ml_prof, ml_mcl = simulate_financials(tr_ml, balance, risk_pc)
            m_status = 'ready'
        else:
            tr_ml = pd.DataFrame()
            ml_bal, ml_prof, ml_mcl = 0.0, 0.0, 0
            m_status = 'insufficient_data'

        return {
            'name': f"Strategy {s_idx}",
            'params': f"a={a}, c={c}",
            'raw': {
                'win_rate': float(tr_raw['win'].mean()) if not tr_raw.empty else 0,
                'trades': int(len(tr_raw)),
                'final_balance': float(raw_bal),
                'max_consec_losses': int(raw_mcl)
            },
            'ml': {
                'status': m_status,
                'win_rate': float(tr_ml['win'].mean()) if not tr_ml.empty else 0,
                'trades': int(len(tr_ml)),
                'final_balance': float(ml_bal),
                'max_consec_losses': int(ml_mcl)
            }
        }

    # Use ThreadPoolExecutor for parallel backtesting
    # Sequential execution (max_workers=1) is safest for OOM prevention in low-memory environments
    # given the size of the 100k candle dataset.
    with ThreadPoolExecutor(max_workers=1) as executor:
        res = list(executor.map(run_single_strat, enumerate(params)))

    return jsonify({'results': res})

def start_bot_loop(loop):
    asyncio.set_event_loop(loop)
    loop.create_task(model_manager.daily_update_loop())
    loop.run_forever()

bot_loop = asyncio.new_event_loop()
model_manager.socketio = socketio
threading.Thread(target=start_bot_loop, args=(bot_loop,), daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
