from flask import Flask, render_template, request
from flask_socketio import SocketIO
import json
import os
import asyncio
import threading
import time
import logging
import sys
from live_multiplier_bot import MultiplierBot
from logging.handlers import RotatingFileHandler

# Configure root logger to catch logs from all modules
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if not root_logger.handlers:
    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    root_logger.addHandler(ch)
    sys.stdout.reconfigure(line_buffering=True)

    # File Handler
    fh = RotatingFileHandler('bot.log', maxBytes=5*1024*1024, backupCount=2)
    fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    root_logger.addHandler(fh)

logger = logging.getLogger(__name__)

app = Flask(__name__)
# Using threading mode as per project memory
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*", logger=True, engineio_logger=True)

CONFIG_FILE = 'config.json'
bot = None
background_sync_started = False
log_buffer = []
LOG_BUFFER_SIZE = 100

def add_to_log(message):
    global log_buffer
    # Message might already have a timestamp if coming from MultiplierBot.log
    if " | " in message and message[:2].isdigit() and message[2] == ":":
        full_msg = message
    else:
        timestamp = time.strftime('%H:%M:%S', time.gmtime())
        full_msg = f"{timestamp} | {message}"

    # Update buffer
    if not log_buffer or log_buffer[-1] != full_msg:
        log_buffer.append(full_msg)
        if len(log_buffer) > LOG_BUFFER_SIZE:
            log_buffer.pop(0)

    # Broadcast to all clients
    socketio.emit('log_update', {'msg': full_msg}, namespace='/')

    # Also log to file/console
    logger.info(full_msg)

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
    global background_sync_started, bot, log_buffer
    logger.info(f"Socket connected: {request.sid}")

    # Send current bot status immediately to the new client
    status = {
        'active': bot.is_running if bot else False,
        'initializing': bot.is_initializing if bot else False,
        'balance': f"{bot.balance:.2f} {bot.currency}" if bot and bot.balance else "0.00 USD"
    }
    logger.info(f"Sending initial status to {request.sid}: {status}")
    socketio.emit('status_update', status, room=request.sid)

    # Send log buffer with slight delay to ensure client is ready
    def send_buffer(sid, buffer_copy):
        time.sleep(1.5) # Increased delay to ensure client side is ready
        # Small chunks to avoid Socket.IO blocking
        for log_msg in buffer_copy:
            try:
                # Use 'to' instead of 'room' for consistency
                socketio.emit('log_update', {'msg': log_msg}, to=sid, namespace='/')
                time.sleep(0.02)
            except: pass

    if log_buffer:
        logger.info(f"Sending {len(log_buffer)} buffered logs to {request.sid}")
        threading.Thread(target=send_buffer, args=(request.sid, list(log_buffer)), daemon=True).start()

    # Always trigger an immediate balance check on connect
    config = load_config()
    if config['api_token']:
        logger.info("Triggering immediate balance sync")
        threading.Thread(target=immediate_balance_sync, args=(config,), daemon=True).start()

    if not background_sync_started:
        logger.info("Starting background sync thread")
        thread = threading.Thread(target=background_sync_logic, daemon=True)
        thread.start()
        background_sync_started = True

@socketio.on('save_settings')
def handle_save_settings(data):
    global bot
    logger.info(f"Received save_settings: {data}")
    config = load_config()
    config['api_token'] = data.get('api_token', config['api_token'])
    config['mode'] = data.get('mode', config['mode'])
    config['stake'] = float(data.get('stake', 10))
    config['stake_type'] = data.get('stake_type', 'fixed')
    save_config(config)

    if bot:
        bot.api_token = config['api_token']
        bot.stake = float(config['stake'])
        bot.stake_type = config['stake_type']
        logger.info("Bot parameters updated with new settings")

    socketio.emit('notify', {'msg': 'Settings Saved Successfully'})

def immediate_balance_sync(config):
    global bot
    from deriv_api import DerivAPI
    async def get_balance():
        api = DerivAPI(app_id=config['app_id'])
        try:
            await asyncio.wait_for(api.authorize(config['api_token']), timeout=10)
            res = await asyncio.wait_for(api.balance(), timeout=10)
            if 'balance' in res:
                bal = res['balance']
                bal_val = float(bal['balance'])
                bal_curr = bal['currency']
                bal_str = f"{bal_val:.2f} {bal_curr}"

                if bot:
                    bot.balance = bal_val
                    bot.currency = bal_curr

                status = {
                    'balance': bal_str,
                    'active': bot.is_running if bot else False,
                    'initializing': bot.is_initializing if bot else False
                }
                socketio.emit('status_update', status, namespace='/')
        except Exception as e:
            logger.error(f"Immediate balance sync error: {e}")
        finally:
            try:
                await asyncio.wait_for(api.disconnect(), timeout=5)
            except: pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(get_balance())
    finally:
        loop.close()

def background_sync_logic():
    """Maintains balance updates in the background thread."""
    global bot
    from deriv_api import DerivAPI

    async def get_balance(config):
        api = DerivAPI(app_id=config['app_id'])
        try:
            await asyncio.wait_for(api.authorize(config['api_token']), timeout=10)
            res = await asyncio.wait_for(api.balance(), timeout=10)
            if 'balance' in res:
                bal = res['balance']
                bal_val = float(bal['balance'])
                bal_curr = bal['currency']
                bal_str = f"{bal_val:.2f} {bal_curr}"

                if bot:
                    bot.balance = bal_val
                    bot.currency = bal_curr

                status = {
                    'balance': bal_str,
                    'active': bot.is_running if bot else False,
                    'initializing': bot.is_initializing if bot else False
                }
                socketio.emit('status_update', status, namespace='/')
        except Exception as e:
            logger.error(f"Background sync error: {e}")
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
                    logger.error(f"Background sync loop error: {e}")
                finally:
                    loop.close()
        except Exception as e:
            logger.error(f"Background sync outer error: {e}")
        time.sleep(15)

@socketio.on('internal_log')
def handle_internal_log(data):
    global log_buffer
    msg = data.get('msg')
    if msg:
        if not log_buffer or log_buffer[-1] != msg:
            log_buffer.append(msg)
            if len(log_buffer) > LOG_BUFFER_SIZE:
                log_buffer.pop(0)
        # Broadcast to all frontend clients
        socketio.emit('log_update', {'msg': msg}, namespace='/')

@socketio.on('toggle_bot')
def handle_toggle_bot(data):
    global bot
    active = data.get('active', False)
    logger.info(f"Toggle bot requested: {active}")

    if active:
        config = load_config()
        if not config['api_token']:
            socketio.emit('notify', {'msg': 'Error: API Token Missing'}, namespace='/')
            socketio.emit('status_update', {'active': False, 'initializing': False}, namespace='/')
            return

        if bot.is_running or bot.is_initializing or bot.should_run:
            logger.info("Bot already running or initializing")
            bot.update_status()
            return

        bot.api_token = config['api_token']
        bot.stake = float(config['stake'])
        bot.stake_type = config['stake_type']
        bot.should_run = True
        bot.is_initializing = True # Set immediately for UI feedback
        bot.update_status()

        def bot_worker():
            logger.info("Bot worker thread starting")
            while bot.should_run:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(bot.start())
                except Exception as e:
                    logger.error(f"Bot worker thread fatal error: {e}")
                    try:
                        socketio.emit('notify', {'msg': f'Bot Fatal Error: {str(e)}'}, namespace='/')
                    except: pass
                    if bot.should_run:
                        time.sleep(10)
                finally:
                    loop.close()

                if not bot.should_run:
                    break

            logger.info("Bot worker thread finishing")
            bot.is_running = False
            bot.is_initializing = False
            bot.update_status()

        thread = threading.Thread(target=bot_worker, daemon=True)
        thread.start()
    else:
        if bot:
            logger.info("Stopping bot requested")
            bot.should_run = False
            bot.is_running = False
            bot.is_initializing = False
            bot.update_status()
            socketio.emit('notify', {'msg': 'Bot Stop Requested'})

# Initialize bot globally
_config = load_config()
bot = MultiplierBot(api_token=_config['api_token'], socketio=socketio, logger_callback=add_to_log)
bot.stake = float(_config['stake'])
bot.stake_type = _config['stake_type']

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
