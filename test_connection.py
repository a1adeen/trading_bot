# test_connection.py
import upstox_client
from config import API_KEY, API_SECRET, ACCESS_TOKEN

# Setup connection
configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN

api_client = upstox_client.ApiClient(configuration)

try:
    # Test 1 — check profile
    profile_api = upstox_client.UserApi(api_client)
    profile = profile_api.get_profile("2.0")
    print("✅ Connected to Upstox!")
    print(f"   Name   : {profile.data.user_name}")
    print(f"   Email  : {profile.data.email}")
    print(f"   Broker : Upstox")

    # Test 2 — fetch RELIANCE live price
    market_api = upstox_client.MarketQuoteApi(api_client)
    quote = market_api.get_full_market_quote(
        "NSE_EQ|INE002A01018",  # RELIANCE token
        "2.0"
    )
    price = quote.data["NSE_EQ:RELIANCE"].last_price
    print(f"\n✅ RELIANCE live price : ₹{price}")
    print("\n🎉 API fully working — ready to build!")

except Exception as e:
    print(f"❌ Error: {e}")