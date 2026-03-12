import asyncio
from deriv_api import DerivAPI
import json

async def check_api():
    with open('config.json', 'r') as f:
        config = json.load(f)

    api = DerivAPI(app_id=config['app_id'])
    try:
        print("Authorizing...")
        auth = await api.authorize(config['api_token'])
        print(f"Auth success: {auth['authorize']['loginid']}")

        print("Fetching 1000 candles for R_100...")
        import time
        start = time.time()
        r = await api.ticks_history({
            'ticks_history': 'R_100',
            'end': 'latest',
            'count': 1000,
            'granularity': 300,
            'style': 'candles'
        })
        end = time.time()
        if 'candles' in r:
            print(f"Fetched {len(r['candles'])} candles in {end-start:.2f}s.")
        else:
            print(f"Error: {r}")
    except Exception as e:
        print(f"API Error: {e}")
    finally:
        await api.disconnect()

if __name__ == "__main__":
    asyncio.run(check_api())
