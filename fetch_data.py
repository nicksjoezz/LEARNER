import asyncio
import pandas as pd
from deriv_api import DerivAPI
import os
import json
from datetime import datetime, timedelta
import sys
from config_utils import load_config

async def update_symbol_data(symbol, data_dir='data'):
    config = load_config()
    fetch_days = int(config.get('fetch_days', 365))
    app_id = config.get('app_id')
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

    now_ts = int(datetime.utcnow().timestamp())
    start_ts = int((datetime.utcnow() - timedelta(days=fetch_days)).timestamp())

    api = DerivAPI(app_id=app_id)

    async def fetch_range(current_start, current_end):
        nonlocal df
        while current_end > current_start:
            # Check for existing data block to jump over
            if not df.empty:
                existing_candles = df[(df['epoch'] <= current_end) & (df['epoch'] >= current_start)]
                if not existing_candles.empty:
                    # If current_end is already covered, jump to the start of this block
                    latest_in_range = existing_candles['epoch'].max()
                    if latest_in_range >= current_end - granularity:
                        new_end = existing_candles['epoch'].min() - 1
                        if new_end <= current_start:
                            break
                        current_end = new_end
                        continue

            sys.stderr.write(f"[{symbol}] Syncing gap ending at {datetime.fromtimestamp(current_end)}\n")

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
                    sys.stderr.write(f"[{symbol}] No more candles returned from API.\n")
                    break

                num_received = len(candles)
                df_new = pd.DataFrame(candles)

                # Filter out candles we already have
                if not df.empty:
                    df_new = df_new[~df_new['epoch'].isin(df['epoch'])]

                num_new = len(df_new)
                if not df_new.empty:
                    df = pd.concat([df, df_new]).drop_duplicates(subset=['epoch']).sort_values('epoch')
                    # Immediate filtering to keep only what's needed
                    cutoff = int((datetime.utcnow() - timedelta(days=fetch_days + 1)).timestamp())
                    df = df[df['epoch'] >= cutoff]
                    # SAVE REAL-TIME
                    df.to_csv(filepath, index=False)
                    sys.stderr.write(f"[{symbol}] Saved {num_new} new candles ({num_received} total in batch).\n")
                else:
                    sys.stderr.write(f"[{symbol}] Batch contained no new data.\n")

                batch_earliest = int(candles[0]['epoch'])
                if batch_earliest <= current_start:
                    break
                current_end = batch_earliest - 1

                await asyncio.sleep(0.5)

            except Exception as e:
                sys.stderr.write(f"Fetch error: {e}. Retrying in 5s...\n")
                await asyncio.sleep(5)

    # 1. Sync forward (from last recorded to now)
    last_recorded = int(df['epoch'].max()) if not df.empty else start_ts
    if now_ts - last_recorded > granularity:
        sys.stderr.write(f"[{symbol}] Updating to latest data...\n")
        await fetch_range(last_recorded, now_ts)

    # 2. Sync backward (from earliest recorded to start_ts)
    earliest_recorded = int(df['epoch'].min()) if not df.empty else now_ts
    if earliest_recorded > start_ts:
        sys.stderr.write(f"[{symbol}] Fetching historical gaps...\n")
        await fetch_range(start_ts, earliest_recorded)

    try:
        await asyncio.wait_for(api.disconnect(), timeout=5)
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
