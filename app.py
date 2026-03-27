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
bot_thread = None

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {"api_token": "", "app_id": "62845", "mode": "demo"}

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
    save_config(config)
    socketio.emit('notify', {'msg': 'Settings Saved Successfully'})

@socketio.on('toggle_bot')
def handle_toggle_bot(data):
    global bot
    active = data.get('active', False)

    if active:
        config = load_config()
        if not config['api_token']:
            socketio.emit('notify', {'msg': 'Error: API Token Missing'})
            return

        bot = MultiplierBot(api_token=config['api_token'], socketio=socketio)
        # Start bot in a dedicated asyncio loop thread
        def run_bot():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot.start())
            loop.run_forever()

        threading.Thread(target=run_bot, daemon=True).start()
        socketio.emit('status_update', {'active': True})
    else:
        if bot:
            # Need to properly stop the bot async
            socketio.emit('notify', {'msg': 'Stopping Bot...'})
            # For simplicity in this bridge, we'll just flag it
            bot.is_running = False
            socketio.emit('status_update', {'active': False})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
