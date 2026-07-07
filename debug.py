# debug.py — test live market quote
import upstox_client
from config import ACCESS_TOKEN

configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client  = upstox_client.ApiClient(configuration)
market_api  = upstox_client.MarketQuoteApi(api_client)

# Test live quote for RELIANCE
try:
    quote = market_api.get_full_market_quote(
        "NSE_EQ|INE002A01018", "2.0"
    )
    key   = list(quote.data.keys())[0]
    data  = quote.data[key]

    print(f"✅ Live data working!")
    print(f"  Symbol    : RELIANCE")
    print(f"  LTP       : ₹{data.last_price}")
    print(f"  Open      : ₹{data.ohlc.open}")
    print(f"  High      : ₹{data.ohlc.high}")
    print(f"  Low       : ₹{data.ohlc.low}")
    print(f"  Close     : ₹{data.ohlc.close}")
    print(f"  Volume    : {data.volume:,}")
    print(f"  Buy qty   : {data.depth.buy[0].quantity}")
    print(f"  Sell qty  : {data.depth.sell[0].quantity}")

except Exception as e:
    print(f"❌ Error: {e}")