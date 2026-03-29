from flask import Flask, render_template, request
from flask_socketio import SocketIO
import json
import os
import asyncio
import threading
import time
from live_multiplier_bot import MultiplierBot

app = Flask(__name__)
# Using threading mode to avoid eventlet/gevent conflicts with asyncio
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

CONFIG_FILE = 'config.json'
bot = None
background_sync_started = False

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                conf = json.load(f)
                conf.setdefault('api_token', '')
                conf.setdefault('app_id', '62845')
                conf.setdefault('mode', 'demo')
                conf.setdefault('stake', 10.0)
                conf.setdefault('stake_type', 'fixed')
                return conf
        except: pass
    return {"api_token": "", "app_id": "62845", "mode": "demo", "stake": 10.0, "stake_type": "fixed"}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

@app.route('/')
def index():
    return render_template('index.html', config=load_config())

@app.route('/settings')
def settings():
    return render_template('settings.html', config=load_config())

@socketio.on('connect')
def handle_connect():
    global background_sync_started
    if not background_sync_started:
        thread = threading.Thread(target=background_sync_logic, daemon=True)
        thread.start()
        background_sync_started = True

@socketio.on('save_settings')
def handle_save_settings(data):
    config = load_config()
    config['api_token'] = data.get('api_token', config['api_token'])
    config['mode'] = data.get('mode', config['mode'])
    config['stake'] = float(data.get('stake', 10))
    config['stake_type'] = data.get('stake_type', 'fixed')
    save_config(config)
    socketio.emit('notify', {'msg': 'Settings Saved Successfully'})

def background_sync_logic():
    """Maintains balance updates in the background thread."""
    from deriv_api import DerivAPI

    async def get_balance(config):
        api = DerivAPI(app_id=config['app_id'])
        try:
            await asyncio.wait_for(api.authorize(config['api_token']), timeout=10)
            res = await asyncio.wait_for(api.balance(), timeout=10)
            if 'balance' in res:
                bal = res['balance']
                socketio.emit('status_update', {'balance': f"{bal['balance']:.2f} {bal['currency']}"})
        except Exception as e:
            print(f"Sync balance error: {e}")
        finally:
            try:
                await asyncio.wait_for(api.disconnect(), timeout=5)
            except: pass

    while True:
        try:
            config = load_config()
            if config['api_token']:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(get_balance(config))
                except Exception as e:
                    print(f"Background sync loop error: {e}")
                finally:
                    loop.close()
        except Exception as e:
            print(f"Background sync outer error: {e}")
        time.sleep(10) # More frequent sync

@socketio.on('toggle_bot')
def handle_toggle_bot(data):
    global bot
    active = data.get('active', False)

    if active:
        config = load_config()
        if not config['api_token']:
            socketio.emit('notify', {'msg': 'Error: API Token Missing'})
            socketio.emit('status_update', {'active': False})
            return

        if bot and bot.is_running:
            return

        bot = MultiplierBot(api_token=config['api_token'], socketio=socketio)
        bot.stake = float(config['stake'])
        bot.stake_type = config['stake_type']

        def bot_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(bot.start())
            except Exception as e:
                print(f"Bot worker error: {e}")
                socketio.emit('notify', {'msg': f'Bot Error: {str(e)}'})
            finally:
                bot.is_running = False
                socketio.emit('status_update', {'active': False})
                loop.close()

        thread = threading.Thread(target=bot_worker, daemon=True)
        thread.start()
    else:
        if bot:
            bot.is_running = False
            socketio.emit('status_update', {'active': False})
            socketio.emit('notify', {'msg': 'Bot Stop Requested'})

if __name__ == '__main__':
    # allow_unsafe_werkzeug required for SocketIO threading mode on dev server
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
