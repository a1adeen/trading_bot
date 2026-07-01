# strategy.py
# VWAP + RSI Crossover + Volume Buildup + DOM strategy

import upstox_client
import pandas as pd
from config import ACCESS_TOKEN

# ─── CONFIG ───────────────────────────────────────────
SL_POINTS     = 35
TARGET_POINTS = 75
RISK_PCT      = 0.01

# ─── CONNECT ──────────────────────────────────────────
configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client    = upstox_client.ApiClient(configuration)
market_api    = upstox_client.MarketQuoteApi(api_client)

# ─── ADD VWAP ─────────────────────────────────────────
def add_vwap(df):
    df = df.copy()
    df["date_only"]  = pd.to_datetime(df["date"]).dt.date
    df["tp"]         = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"]     = df["tp"] * df["volume"]
    df["cum_tp_vol"] = df.groupby("date_only")["tp_vol"].cumsum()
    df["cum_vol"]    = df.groupby("date_only")["volume"].cumsum()
    df["vwap"]       = df["cum_tp_vol"] / df["cum_vol"]
    return df

# ─── ADD RSI ──────────────────────────────────────────
def add_rsi(df, period=14):
    df       = df.copy()
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs       = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))
    return df

# ─── VOLUME BUILDUP ───────────────────────────────────
def volume_building_up(df, i):
    if i < 2:
        return False, False
    candles   = [df.iloc[i - 1], df.iloc[i]]
    buy_vols  = [c["volume"] if c["close"] > c["open"] else 0 for c in candles]
    sell_vols = [c["volume"] if c["close"] < c["open"] else 0 for c in candles]
    buy_up    = buy_vols[1]  > buy_vols[0]  and buy_vols[0]  > 0
    sell_up   = sell_vols[1] > sell_vols[0] and sell_vols[0] > 0
    return buy_up, sell_up

# ─── RSI CROSSOVER ────────────────────────────────────
def rsi_crossover(df, i):
    if i < 1:
        return False, False
    prev_rsi   = df["rsi"].iloc[i - 1]
    curr_rsi   = df["rsi"].iloc[i]
    cross_up   = prev_rsi < 50 and curr_rsi > 50
    cross_down = prev_rsi > 50 and curr_rsi < 50
    return cross_up, cross_down

# ─── DOM CHECK ────────────────────────────────────────
def check_dom(instrument_token):
    try:
        depth    = market_api.get_full_market_quote(instrument_token, "2.0")
        key      = list(depth.data.keys())[0]
        dom_data = depth.data[key].depth
        bid_vol  = sum(l.quantity for l in dom_data.buy[:5]  if l.quantity)
        ask_vol  = sum(l.quantity for l in dom_data.sell[:5] if l.quantity)
        ratio    = bid_vol / ask_vol if ask_vol > 0 else 0
        print(f"    DOM — BID: {bid_vol:,} | ASK: {ask_vol:,} | Ratio: {ratio:.2f}x")
        return ratio >= 1.5, ratio <= 0.67, ratio
    except Exception as e:
        print(f"    DOM failed: {e}")
        return False, False, 0

# ─── POSITION SIZE ────────────────────────────────────
def calculate_position(capital, entry_price, atr=None):
    sl_pts  = atr   if atr   else SL_POINTS
    tgt_pts = atr * 2 if atr else TARGET_POINTS
    risk    = capital * RISK_PCT
    qty     = max(1, int(risk / sl_pts))
    return {
        "qty"       : qty,
        "entry"     : entry_price,
        "stop_loss" : round(entry_price - sl_pts,  2),
        "target"    : round(entry_price + tgt_pts, 2),
        "risk"      : round(qty * sl_pts,  2),
        "reward"    : round(qty * tgt_pts, 2),
        "rr_ratio"  : "1:2.0"
    }

# ─── FULL SIGNAL CHECK ────────────────────────────────
def get_trade_signal(symbol, instrument_token, df, capital=50000):
    print(f"\n{'─'*50}")
    print(f"  Scanning {symbol}...")

    df     = add_vwap(df)
    df     = add_rsi(df)
    i      = len(df) - 1
    latest = df.iloc[i]
    price  = latest["close"]
    vwap   = latest["vwap"]
    rsi    = latest["rsi"]
    atr    = latest.get("atr", None)

    print(f"  Price : ₹{price:.2f}")
    print(f"  VWAP  : ₹{vwap:.2f}")
    print(f"  RSI   : {rsi:.1f}")

    rsi_up,  rsi_down = rsi_crossover(df, i)
    buy_vol, sell_vol = volume_building_up(df, i)

    buy_signal  = price > vwap and rsi_up  and buy_vol
    sell_signal = price < vwap and rsi_down and sell_vol

    if buy_signal:
        print(f"  Tech  : ✅ BUY signal")
        dom_buy, _, ratio = check_dom(instrument_token)
        if dom_buy:
            pos = calculate_position(capital, price, atr)
            print(f"  DOM   : ✅ CONFIRMED ({ratio:.2f}x)")
            print(f"  Entry : ₹{pos['entry']} | SL: ₹{pos['stop_loss']} | TGT: ₹{pos['target']}")
            print(f"  Qty   : {pos['qty']} | Risk: ₹{pos['risk']} | Reward: ₹{pos['reward']}")
            print(f"  ➡️  ACTION: 🟢 BUY")
            return {"symbol": symbol, "signal": "BUY", **pos}
        else:
            print(f"  DOM   : ❌ weak ({ratio:.2f}x) — SKIP")
            return {"symbol": symbol, "signal": "SKIP_DOM"}

    elif sell_signal:
        print(f"  Tech  : ✅ SELL signal")
        _, dom_sell, ratio = check_dom(instrument_token)
        if dom_sell:
            print(f"  DOM   : ✅ CONFIRMED — 🔴 SELL")
            return {"symbol": symbol, "signal": "SELL", "price": price}
        else:
            print(f"  DOM   : ❌ weak — SKIP")
            return {"symbol": symbol, "signal": "SKIP_DOM"}

    else:
        reasons = []
        if price <= vwap:          reasons.append(f"Price below VWAP")
        if not rsi_up:             reasons.append(f"No RSI crossover (RSI={rsi:.1f})")
        if not buy_vol:            reasons.append("No volume buildup")
        print(f"  ➡️  NO SIGNAL — {' | '.join(reasons)}")
        return {"symbol": symbol, "signal": "NONE"}


# ─── MAIN ─────────────────────────────────────────────
if __name__ == "__main__":
    from data_pipeline import load_from_db

    WATCHLIST = {
        "RELIANCE"  : "NSE_EQ|INE002A01018",
        "TCS"       : "NSE_EQ|INE467B01029",
        "INFY"      : "NSE_EQ|INE009A01021",
        "HDFCBANK"  : "NSE_EQ|INE040A01034",
        "ICICIBANK" : "NSE_EQ|INE090A01021"
    }

    print("=" * 50)
    print("  VWAP + RSI + VOLUME + DOM SCANNER")
    print("=" * 50)

    results = []
    for symbol, token in WATCHLIST.items():
        df     = load_from_db(symbol)
        result = get_trade_signal(symbol, token, df, capital=50000)
        results.append(result)

    buys  = [r for r in results if r["signal"] == "BUY"]
    sells = [r for r in results if r["signal"] == "SELL"]

    print(f"\n{'='*50}")
    print(f"  Scanned : {len(results)} stocks")
    print(f"  BUY     : {len(buys)} 🟢")
    print(f"  SELL    : {len(sells)} 🔴")
    if buys:
        print(f"\n  Stocks to BUY:")
        for r in buys:
            print(f"  → {r['symbol']} @ ₹{r['entry']} | SL: ₹{r['stop_loss']} | TGT: ₹{r['target']}")