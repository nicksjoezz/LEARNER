import asyncio
import pandas as pd
from deriv_api import DerivAPI
import os
import json
from datetime import datetime, timedelta
import sys

CONFIG_FILE = 'config.json'

def load_fetch_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                return int(config.get('fetch_days', 730)), config.get('app_id', '62845')
        except:
            pass
    return 730, '62845'

async def update_symbol_data(symbol, data_dir='data'):
    fetch_days, app_id = load_fetch_config()
    filepath = os.path.join(data_dir, f"{symbol}_5m_2y.csv")
    granularity = 300
    os.makedirs(data_dir, exist_ok=True)

    df = pd.DataFrame()
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath)
            if not df.empty:
                df = df.drop_duplicates(subset=['epoch']).sort_values('epoch')
        except Exception as e:
            sys.stderr.write(f"Error reading {filepath}: {e}. Starting fresh.\n")

    end_time = int(datetime.now().timestamp())
    start_time = int((datetime.now() - timedelta(days=fetch_days)).timestamp())

    api = DerivAPI(app_id=app_id)

    # Function for incremental candle fetching
    async def fetch_missing_data(current_start, current_end, direction='backward'):
        nonlocal df
        while current_end > current_start:
            sys.stderr.write(f"[{symbol}] Fetching {direction} gap up to {datetime.fromtimestamp(current_end)}\n")
            try:
                response = await api.ticks_history({
                    'ticks_history': symbol,
                    'end': str(current_end),
                    'adjust_start_time': 1,
                    'count': 5000,
                    'granularity': granularity,
                    'style': 'candles'
                })

                if 'error' in response:
                    err = response['error']
                    sys.stderr.write(f"API Error ({err.get('code')}): {err.get('message')}\n")
                    if err.get('code') == 'RateLimit':
                        await asyncio.sleep(60)
                        continue
                    break

                candles = response.get('candles', [])
                if not candles:
                    sys.stderr.write(f"No more candles available for {symbol}.\n")
                    break

                df_new = pd.DataFrame(candles)
                df = pd.concat([df, df_new]).drop_duplicates(subset=['epoch']).sort_values('epoch')

                # Immediate filtering to keep only what's needed
                cutoff = int((datetime.now() - timedelta(days=fetch_days + 1)).timestamp())
                df = df[df['epoch'] >= cutoff]

                # SAVE REAL-TIME
                df.to_csv(filepath, index=False)

                batch_earliest = int(candles[0]['epoch'])

                # Check if we caught up with existing data or target start
                # Use a small overlap to be safe
                if direction == 'forward' and batch_earliest <= current_start:
                    break
                elif direction == 'backward' and batch_earliest <= current_start:
                    break

                if batch_earliest <= current_start: break

                current_end = batch_earliest - 1
                await asyncio.sleep(1) # Responsible rate limiting

            except Exception as e:
                sys.stderr.write(f"Fetch error: {e}. Retrying in 10s...\n")
                await asyncio.sleep(10)
                continue

    # 1. Update forward from last recorded to now
    last_recorded = int(df['epoch'].max()) if not df.empty else start_time
    if end_time - last_recorded > granularity:
        await fetch_missing_data(last_recorded, end_time, direction='forward')

    # 2. Extend backward to meet fetch_days if needed
    earliest_recorded = int(df['epoch'].min()) if not df.empty else end_time
    if earliest_recorded > start_time:
        await fetch_missing_data(start_time, earliest_recorded, direction='backward')

    try:
        await asyncio.wait_for(api.disconnect(), timeout=10)
    except:
        pass

    sys.stderr.write(f"Completed incremental update for {symbol}. Total: {len(df)} candles.\n")
    return df

async def main():
    symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']
    for symbol in symbols:
        await update_symbol_data(symbol)

if __name__ == "__main__":
    asyncio.run(main())
