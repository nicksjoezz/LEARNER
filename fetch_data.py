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

    async def fetch_range(current_start, current_end, direction='backward'):
        nonlocal df
        while (direction == 'backward' and current_end > current_start) or \
              (direction == 'forward' and current_start < current_end):

            # For Deriv API 'ticks_history':
            # 'end' is the latest time we want
            # 'count' is how many candles to fetch *before* 'end'
            sys.stderr.write(f"[{symbol}] Fetching {direction} gap. Target: {datetime.fromtimestamp(current_start if direction == 'backward' else current_end)}\n")

            try:
                params = {
                    'ticks_history': symbol,
                    'end': str(current_end),
                    'adjust_start_time': 1,
                    'count': 5000,
                    'granularity': granularity,
                    'style': 'candles'
                }

                response = await api.ticks_history(params)

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
                cutoff = int((datetime.utcnow() - timedelta(days=fetch_days + 1)).timestamp())
                df = df[df['epoch'] >= cutoff]

                # SAVE REAL-TIME
                df.to_csv(filepath, index=False)

                batch_earliest = int(candles[0]['epoch'])
                batch_latest = int(candles[-1]['epoch'])

                if direction == 'backward':
                    if batch_earliest <= current_start:
                        break
                    current_end = batch_earliest - 1
                else: # forward
                    # Forward fetching with 'end' as moving target:
                    # We always fetch up to the latest known 'now_ts'.
                    # If the earliest candle in our batch is already in our df, we've caught up.
                    if batch_latest >= current_end:
                        break
                    # To fetch "forward", we actually move 'end' to 'now_ts' but we only do it once?
                    # No, the API 'end' is the UPPER bound.
                    # If we already have up to 'batch_latest', and we want to reach 'now_ts',
                    # we should actually be using 'start' if we wanted true forward,
                    # but 'ticks_history' is easier with 'end' and 'count'.
                    # Let's just use the 'backward' logic to fill gaps from 'now_ts' down to 'last_recorded'.
                    if batch_earliest <= current_start:
                        break
                    current_end = batch_earliest - 1

                await asyncio.sleep(1)

            except Exception as e:
                sys.stderr.write(f"Fetch error: {e}. Retrying in 10s...\n")
                await asyncio.sleep(10)
                continue

    # 1. Fill forward gap: From last recorded in CSV up to NOW
    last_recorded = int(df['epoch'].max()) if not df.empty else start_ts
    if now_ts - last_recorded > granularity:
        sys.stderr.write(f"[{symbol}] Filling forward gap from {datetime.fromtimestamp(last_recorded)} to {datetime.fromtimestamp(now_ts)}\n")
        await fetch_range(last_recorded, now_ts, direction='forward')

    # 2. Fill backward gap: From earliest recorded down to START_TS
    earliest_recorded = int(df['epoch'].min()) if not df.empty else now_ts
    if earliest_recorded > start_ts:
        sys.stderr.write(f"[{symbol}] Filling backward gap from {datetime.fromtimestamp(earliest_recorded)} down to {datetime.fromtimestamp(start_ts)}\n")
        await fetch_range(start_ts, earliest_recorded, direction='backward')

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
