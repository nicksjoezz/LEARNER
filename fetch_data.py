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
                return int(config.get('fetch_days', 730)), config.get('app_id', '1089')
        except:
            pass
    return 730, '1089'

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
    async def fetch_missing_data(current_start, current_end):
        nonlocal df
        while current_end > current_start:
            # Deriv's ticks_history with 'end' gives candles BEFORE that time.
            # So to get the latest ones, we start from 'end_time' and go backward.
            # BUT, to update an existing dataset, we can also go forward or backward.
            # The most robust way is to fetch from 'end_time' backward until we hit
            # the last recorded candle in our CSV.

            sys.stderr.write(f"[{symbol}] Fetching missing candles up to {datetime.fromtimestamp(current_end)}\n")
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
                    sys.stderr.write(f"API Error: {response['error']}\n")
                    break

                candles = response.get('candles', [])
                if not candles:
                    sys.stderr.write(f"No more candles available for {symbol}.\n")
                    break

                df_new = pd.DataFrame(candles)

                # Identify if any of these candles are already in our DF
                # If candles[0]['epoch'] is less than our last_recorded, it means we've caught up.

                df = pd.concat([df, df_new]).drop_duplicates(subset=['epoch']).sort_values('epoch')

                # Data Cleaning: Filter by fetch_days to keep on-disk data lean
                cutoff = int((datetime.now() - timedelta(days=fetch_days + 1)).timestamp())
                df = df[df['epoch'] >= cutoff]

                # Save immediately
                df.to_csv(filepath, index=False)

                # Check if the earliest candle in this batch is still after our target start
                batch_earliest = int(candles[0]['epoch'])
                if batch_earliest <= current_start:
                    sys.stderr.write(f"[{symbol}] Caught up with historical data.\n")
                    break

                # Move the 'end' backward for the next call
                current_end = batch_earliest - 1
                await asyncio.sleep(0.5)

            except Exception as e:
                sys.stderr.write(f"Fetch error: {e}. Retrying...\n")
                await asyncio.sleep(5)
                continue

    # Determine what's missing
    last_recorded = int(df['epoch'].max()) if not df.empty else start_time

    # If the gap between now and the last candle is more than one candle period, fetch.
    if end_time - last_recorded > granularity:
        await fetch_missing_data(last_recorded, end_time)

    # Also check if we need to extend backwards to reach fetch_days
    earliest_recorded = int(df['epoch'].min()) if not df.empty else end_time
    if earliest_recorded > start_time:
        await fetch_missing_data(start_time, earliest_recorded)

    await api.disconnect()
    sys.stderr.write(f"Completed incremental update for {symbol}. Total: {len(df)}\n")
    return df

async def main():
    symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']
    for symbol in symbols:
        await update_symbol_data(symbol)

if __name__ == "__main__":
    asyncio.run(main())
