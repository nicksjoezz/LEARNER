import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO
import json
import os
import threading
import asyncio
from live_multiplier_bot import MultiplierBot

app = Flask(__name__)
socketio = SocketIO(app, async_mode='eventlet')

CONFIG_FILE = 'config.json'
bot = None
background_api = None

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            conf = json.load(f)
            # Defaults
            if 'stake' not in conf: conf['stake'] = 10
            if 'stake_type' not in conf: conf['stake_type'] = 'fixed'
            return conf
    return {"api_token": "", "app_id": "62845", "mode": "demo", "stake": 10, "stake_type": "fixed"}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

@app.route('/')
def index():
    config = load_config()
    return render_template('index.html', config=config)

@app.route('/settings')
def settings():
    config = load_config()
    return render_template('settings.html', config=config)

@socketio.on('save_settings')
def handle_save_settings(data):
    config = load_config()
    config['api_token'] = data.get('api_token', config['api_token'])
    config['mode'] = data.get('mode', config['mode'])
    config['stake'] = float(data.get('stake', 10))
    config['stake_type'] = data.get('stake_type', 'fixed')
    save_config(config)
    socketio.emit('notify', {'msg': 'Settings Saved Successfully'})
    # Trigger re-connect for background task if token changed
    start_background_sync()

def start_background_sync():
    global background_api
    config = load_config()
    if not config['api_token']: return

    from deriv_api import DerivAPI
    async def sync_balance():
        try:
            api = DerivAPI(app_id=config['app_id'])
            await api.authorize(config['api_token'])
            # Subscribe to balance
            sub = await api.subscribe({'balance': 1})
            def on_balance(data):
                if 'balance' in data:
                    socketio.emit('status_update', {'balance': f"{data['balance']['balance']:.2f} {data['balance']['currency']}"})
            sub.subscribe(on_balance)
            while True: await asyncio.sleep(60)
        except: pass

    def run_sync():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(sync_balance())

    if background_api:
        # Note: In a production environment, you would properly signal the thread to stop.
        # For this implementation, we'll allow a re-start to update settings.
        pass

    background_api = threading.Thread(target=run_sync, daemon=True)
    background_api.start()

@socketio.on('toggle_bot')
def handle_toggle_bot(data):
    global bot
    active = data.get('active', False)

    if active:
        config = load_config()
        if not config['api_token']:
            socketio.emit('notify', {'msg': 'Error: API Token Missing'})
            return

        if bot and bot.is_running: return

        bot = MultiplierBot(api_token=config['api_token'], socketio=socketio)
        bot.stake = config['stake']
        bot.stake_type = config['stake_type']

        def run_bot():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(bot.start())
            except Exception as e:
                print(f"Bot start error: {e}")
            finally:
                loop.close()

        threading.Thread(target=run_bot, daemon=True).start()
        socketio.emit('status_update', {'active': True})
    else:
        if bot:
            bot.is_running = False
            socketio.emit('status_update', {'active': False})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
