#upstox ka api use krra 
# get_data.py
import upstox_client
from config import ACCESS_TOKEN
import pandas as pd

configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client = upstox_client.ApiClient(configuration)

history_api = upstox_client.HistoryApi(api_client)

# Fetch 1 year of RELIANCE daily data
data = history_api.get_historical_candle_data1(
    "NSE_EQ|INE002A01018",   # RELIANCE
    "day",                    # interval
    "2024-12-31",            # to date
    "2024-01-01",            # from date
    "2.0"
)

# Convert to dataframe
candles = data.data.candles
df = pd.DataFrame(candles, columns=["date","open","high","low","close","volume","oi"])
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date")

print(df.tail(10))
print(f"\nTotal candles : {len(df)}")
print("✅ Historical data working!")