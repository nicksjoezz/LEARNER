import asyncio
import pandas as pd
from deriv_api import DerivAPI
import os
from datetime import datetime, timedelta
import sys

APP_ID = '62845' # Using the one from config.json

async def fetch_symbol_data(symbol, granularity, days=30, data_dir='data'):
    os.makedirs(data_dir, exist_ok=True)
    filename = f"{symbol}_{granularity}s_{days}d.csv"
    filepath = os.path.join(data_dir, filename)

    end_time = int(datetime.now().timestamp())
    start_time = int((datetime.now() - timedelta(days=days)).timestamp())

    api = DerivAPI(app_id=APP_ID)
    df = pd.DataFrame()

    current_end = end_time
    total_fetched = 0

    try:
        while current_end > start_time:
            sys.stderr.write(f"[{symbol} {granularity}s] Fetching up to {datetime.fromtimestamp(current_end)}\n")
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
            df = pd.concat([df, df_new]).drop_duplicates(subset=['epoch']).sort_values('epoch')

            new_end = int(candles[0]['epoch']) - 1
            if new_end >= current_end: break
            current_end = new_end

            total_fetched = len(df)
            if current_end < start_time: break
            await asyncio.sleep(0.2)

    except Exception as e:
        sys.stderr.write(f"Fetch error: {e}\n")
    finally:
        await api.disconnect()

    if not df.empty:
        df.to_csv(filepath, index=False)
        print(f"Saved {len(df)} candles to {filepath}")
    else:
        print(f"No data fetched for {symbol} {granularity}s")

async def main():
    symbols = ['BOOM500', 'CRASH500']
    granularities = [60, 900] # 1m and 15m
    days = 30 # User asked for analysis, 30 days should be enough for a start

    for symbol in symbols:
        for gran in granularities:
            await fetch_symbol_data(symbol, gran, days=days)

if __name__ == "__main__":
    asyncio.run(main())
