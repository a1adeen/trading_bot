# pnl_server.py
# Reads positions.json written by live_bot.py
# Shows all open positions with live P&L

from flask import Flask, jsonify
import upstox_client
import json
import os
from config import ACCESS_TOKEN
from nifty100_tokens import NIFTY100

app = Flask(__name__)

configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client  = upstox_client.ApiClient(configuration)
market_api  = upstox_client.MarketQuoteApi(api_client)

def load_positions():
    try:
        with open("positions.json") as f:
            return json.load(f)
    except:
        return {}

def fetch_ltp(token):
    try:
        quote = market_api.get_full_market_quote(token, "2.0")
        key   = list(quote.data.keys())[0]
        return quote.data[key].last_price
    except:
        return None

@app.route("/api/pnl")
def pnl():
    positions = load_positions()
    result    = []
    total     = 0

    for symbol, pos in positions.items():
        token = NIFTY100.get(symbol)
        ltp   = fetch_ltp(token) if token else None
        if not ltp:
            continue

        entry    = pos["entry"]
        sl       = pos["sl"]
        target   = pos["target"]
        qty      = pos["qty"]

        pnl_val  = round((ltp - entry) * qty, 2)
        pct      = round((ltp - entry) / entry * 100, 2)
        sl_dist  = round(ltp - sl, 2)
        tgt_dist = round(target - ltp, 2)
        total   += pnl_val

        rng      = target - sl
        progress = round(max(0, min(100,
            (ltp - sl) / rng * 100 if rng > 0 else 0
        )), 1)

        result.append({
            "symbol"   : symbol,
            "entry"    : entry,
            "ltp"      : round(ltp, 2),
            "qty"      : qty,
            "sl"       : sl,
            "target"   : target,
            "pnl"      : pnl_val,
            "pct"      : pct,
            "sl_dist"  : sl_dist,
            "tgt_dist" : tgt_dist,
            "progress" : progress,
        })

    result.sort(key=lambda x: x["pnl"])
    return jsonify({
        "positions"  : result,
        "total_pnl"  : round(total, 2),
        "count"      : len(result),
        "winners"    : len([r for r in result if r["pnl"] > 0]),
        "losers"     : len([r for r in result if r["pnl"] < 0]),
    })

@app.route("/")
def dashboard():
    return open("dashboard.html", encoding="utf-8").read()

if __name__ == "__main__":
    import webbrowser
    webbrowser.open("http://localhost:5000")
    app.run(port=5000, debug=False)