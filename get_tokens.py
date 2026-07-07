import requests, json, gzip, io

SYMBOLS = [
    "RELIANCE","TCS","HDFCBANK","BHARTIARTL","ICICIBANK",
    "INFOSYS","SBIN","HINDUNILVR","ITC","LT","KOTAKBANK",
    "AXISBANK","BAJFINANCE","ASIANPAINT","MARUTI","SUNPHARMA",
    "TATAMOTORS","WIPRO","TITAN","ONGC","NTPC","POWERGRID",
    "TATASTEEL","ADANIENT","ADANIPORTS","HCLTECH","BAJAJFINSV",
    "JSWSTEEL","COALINDIA","HINDALCO","GRASIM","DRREDDY",
    "TECHM","DIVISLAB","EICHERMOT","HEROMOTOCO","CIPLA",
    "TATACONSUM","BRITANNIA","APOLLOHOSP","BPCL","INDUSINDBK",
    "SBILIFE","HDFCLIFE","SHREECEM","PIDILITIND","HAVELLS",
    "DABUR","MARICO","COLPAL","GODREJCP","LUPIN","AUROPHARMA",
    "TORNTPHARM","BOSCHLTD","VOLTAS","TATAPOWER","ADANIGREEN",
    "TORNTPOWER","CONCOR","INDIGO","IRCTC","DLF","ZOMATO",
    "NAUKRI","PERSISTENT","LTIM","MPHASIS","COFORGE","OFSS",
    "OBEROIRLTY","SIEMENS","CUMMINSIND","MUTHOOTFIN","PAGEIND",
    "BERGEPAINT","ALKEM","BIOCON","IPCALAB","ABBOTINDIA",
    "UBL","NESTLEIND","TRENT","VEDL","SAIL","PNB",
    "BANKBARODA","CANBK","UNIONBANK","FEDERALBNK","IDFCFIRSTB",
    "CHOLAFIN","MOTHERSON","ASHOKLEY","ESCORTS","DIXON"
]

print("Downloading instrument list from Upstox...")
resp = requests.get(
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
    timeout=30
)
data   = json.load(gzip.open(io.BytesIO(resp.content)))
print(f"Total instruments loaded: {len(data)}")

tokens = {}
for inst in data:
    sym = inst.get("trading_symbol", "")
    if (inst.get("segment") == "NSE_EQ" and
        inst.get("instrument_type") == "EQ" and
        sym in SYMBOLS):
        tokens[sym] = inst.get("instrument_key", "")

print(f"Tokens found: {len(tokens)}")

with open("nifty100_tokens.py", "w") as f:
    f.write("NIFTY100 = {\n")
    for s, k in sorted(tokens.items()):
        f.write(f'    "{s}": "{k}",\n')
    f.write("}\n")

print(f"Done — nifty100_tokens.py saved with {len(tokens)} tokens!")