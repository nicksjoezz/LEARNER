import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import json, os, asyncio, threading, pandas as pd
from datetime import datetime, timedelta
from trading_bot import TradingBot
from model_manager import model_manager
from deriv_api import DerivAPI
from strategy_utils import ut_bot, Backtester, calculate_max_consecutive_losses, simulate_financials
from indicators import add_indicators
from config_utils import load_config, save_config

app = Flask(__name__)
# Standard Flask-SocketIO initialization
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")
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
        asyncio.run_coroutine_threadsafe(bot.start(c), bot_loop)
    return jsonify({'status': 'success'})

@app.route('/get_system_status')
def get_sys_status():
    return jsonify({
        'is_initial_training': model_manager.is_initial_training,
        'last_trained': model_manager.last_trained,
        'bot_state': bot.get_state(),
        'logs': bot.log_history
    })

async def get_bt_data(symbol, days):
    fp = os.path.join('data', f"{symbol}_5m_2y.csv")
    ts = int((datetime.now() - timedelta(days=int(days))).timestamp())

    if os.path.exists(fp):
        try:
            df = pd.read_csv(fp)
            if not df.empty and df['epoch'].min() <= ts:
                return df[df['epoch'] >= ts]
        except: pass

    c = load_config()
    api = DerivAPI(app_id=c.get('app_id'))
    end, candles = int(datetime.now().timestamp()), []
    curr = end
    while curr > ts:
        try:
            r = await api.ticks_history({'ticks_history': symbol, 'end': str(curr), 'count': 5000, 'granularity': 300, 'style': 'candles'})
            if 'candles' not in r or not r['candles']: break
            candles.extend(r['candles'][::-1])
            curr = r['candles'][0]['epoch'] - 1
            if len(candles) > (int(days) * 288 + 500): break
        except: break
    await api.disconnect()
    if not candles: return pd.DataFrame()
    return pd.DataFrame(candles).drop_duplicates(subset=['epoch']).sort_values('epoch')

@app.route('/run_backtest', methods=['POST'])
def run_bt():
    d = request.json
    try:
        future = asyncio.run_coroutine_threadsafe(get_bt_data(d['symbol'], d['days']), bot_loop)
        df_raw = future.result(timeout=60)
    except Exception as e:
        return jsonify({'error': str(e), 'results': []})

    if df_raw.empty: return jsonify({'results': []})

    df = add_indicators(df_raw)
    res = []
    params = [(1, 10), (2, 20), (3, 30), (1, 20), (2, 10), (3, 20), (1, 30), (2, 30), (3, 10), (1.5, 15)]

    balance = float(d.get('balance', 1000))
    # Use risk from request if available, else from config
    risk_pc = float(d.get('risk_pc', load_config().get('trade_pc', 1)))

    for i, (a, c) in enumerate(params):
        s_idx = i + 1
        df_sig = ut_bot(df, a=a, c=c)

        # Raw results
        tr_raw = Backtester(df_sig).run()
        raw_bal, raw_prof, raw_mcl = simulate_financials(tr_raw, balance, risk_pc)

        # ML Filtered results
        m_status = model_manager.get_model_status(d['symbol'], s_idx)
        ml = model_manager.get_model(d['symbol'], s_idx)

        if ml:
            df_filtered = ml.filter_signals(df_sig)
            tr_ml = Backtester(df_filtered).run()
            ml_bal, ml_prof, ml_mcl = simulate_financials(tr_ml, balance, risk_pc)
        else:
            tr_ml = pd.DataFrame()
            ml_bal, ml_prof, ml_mcl = 0.0, 0.0, 0

        res.append({
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
        })
    return jsonify({'results': res})

def start_bot_loop(loop):
    asyncio.set_event_loop(loop)
    loop.create_task(model_manager.daily_update_loop())
    loop.run_forever()

bot_loop = asyncio.new_event_loop()
model_manager.socketio = socketio
threading.Thread(target=start_bot_loop, args=(bot_loop,), daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
