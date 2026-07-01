# strategy.py
# Momentum strategy with DOM confirmation
# Fixed SL = 35 points | Fixed Target = 75 points
# R:R = 1:2.1

import upstox_client
import pandas as pd
from config import ACCESS_TOKEN

# ─── CONFIG ───────────────────────────────────────────
SL_POINTS     = 35   # stop loss points below entry
TARGET_POINTS = 75   # target points above entry
RISK_PCT      = 0.01 # risk 1% of capital per trade

# ─── CONNECT TO UPSTOX ────────────────────────────────
configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client    = upstox_client.ApiClient(configuration)
market_api    = upstox_client.MarketQuoteApi(api_client)

# ─── DOM CONFIRMATION ─────────────────────────────────
def check_dom(instrument_token):
    """
    Check Depth of Market.
    BUY confirmed only if BID volume >= 1.5x ASK volume.
    Top 5 levels on each side are checked.
    """
    try:
        depth = market_api.get_full_market_quote(
            instrument_token,
            "2.0"
        )

        key      = list(depth.data.keys())[0]
        dom_data = depth.data[key].depth

        # Sum top 5 BID volumes
        bid_volume = sum(
            level.quantity
            for level in dom_data.buy[:5]
            if level.quantity
        )

        # Sum top 5 ASK volumes
        ask_volume = sum(
            level.quantity
            for level in dom_data.sell[:5]
            if level.quantity
        )

        ratio = bid_volume / ask_volume if ask_volume > 0 else 0

        print(f"    DOM — BID: {bid_volume:,} | ASK: {ask_volume:,} | Ratio: {ratio:.2f}x")

        # BUY confirmed if BID is 1.5x greater than ASK
        return ratio >= 1.5, bid_volume, ask_volume, ratio

    except Exception as e:
        print(f"    DOM check failed: {e}")
        return False, 0, 0, 0

# ─── SIGNAL ENGINE ────────────────────────────────────
def check_signal(df):
    """
    Check latest candle for BUY or SELL signal.
    Returns signal type and reason.
    """
    if len(df) < 51:
        return "NONE", "Not enough data"

    latest = df.iloc[-1]
    prev   = df.iloc[-2]

    # ── BUY CONDITIONS ──
    ema_cross_up     = latest["ema_20"] > latest["ema_50"]
    ema_was_below    = prev["ema_20"] <= prev["ema_50"]  # fresh crossover
    rsi_healthy      = 50 < latest["rsi"] < 70
    volume_spike     = latest["volume"] > 1.5 * latest["volume_ma"]
    price_above_ema  = latest["close"] > latest["ema_20"]

    # ── SELL CONDITIONS ──
    ema_cross_down   = latest["ema_20"] < latest["ema_50"]
    rsi_overbought   = latest["rsi"] > 75
    rsi_oversold     = latest["rsi"] < 30

    # ── SIGNAL LOGIC ──
    if ema_cross_up and rsi_healthy and volume_spike and price_above_ema:
        reason = (
            f"EMA20({latest['ema_20']:.1f}) > EMA50({latest['ema_50']:.1f}) | "
            f"RSI({latest['rsi']:.1f}) | "
            f"Vol spike({latest['volume']/latest['volume_ma']:.1f}x)"
        )
        return "BUY", reason

    elif ema_cross_down or rsi_overbought:
        reason = (
            f"EMA cross down" if ema_cross_down
            else f"RSI overbought({latest['rsi']:.1f})"
        )
        return "SELL", reason

    return "NONE", "No signal"

# ─── POSITION SIZING ──────────────────────────────────
def calculate_position(capital, entry_price):
    """
    Calculate quantity based on 1% capital risk.
    SL is fixed 35 points below entry.
    """
    risk_amount = capital * RISK_PCT      # e.g. ₹1000 on ₹1,00,000
    qty         = int(risk_amount / SL_POINTS)  # shares to buy

    stop_loss   = entry_price - SL_POINTS
    target      = entry_price + TARGET_POINTS

    return {
        "qty"        : qty,
        "entry"      : entry_price,
        "stop_loss"  : stop_loss,
        "target"     : target,
        "risk"       : qty * SL_POINTS,
        "reward"     : qty * TARGET_POINTS,
        "rr_ratio"   : f"1:{TARGET_POINTS/SL_POINTS:.1f}"
    }

# ─── FULL SIGNAL CHECK WITH DOM ───────────────────────
def get_trade_signal(symbol, instrument_token, df, capital=100000):
    """
    Full signal check:
    1. Check technical signal (EMA + RSI + Volume)
    2. If BUY signal — confirm with DOM
    3. Calculate position size
    4. Return complete trade details
    """
    print(f"\n{'─'*45}")
    print(f"  Checking {symbol}...")

    # Step 1 — technical signal
    signal, reason = check_signal(df)
    latest_price   = df["close"].iloc[-1]

    print(f"  Signal    : {signal}")
    print(f"  Reason    : {reason}")
    print(f"  Price     : ₹{latest_price:.2f}")

    if signal == "BUY":
        # Step 2 — DOM confirmation
        print(f"  Checking DOM...")
        dom_confirmed, bid_vol, ask_vol, ratio = check_dom(instrument_token)

        if dom_confirmed:
            # Step 3 — position sizing
            position = calculate_position(capital, latest_price)

            print(f"  DOM       : ✅ CONFIRMED (BID {ratio:.2f}x ASK)")
            print(f"  Entry     : ₹{position['entry']:.2f}")
            print(f"  Stop Loss : ₹{position['stop_loss']:.2f} (-{SL_POINTS} pts)")
            print(f"  Target    : ₹{position['target']:.2f} (+{TARGET_POINTS} pts)")
            print(f"  Qty       : {position['qty']} shares")
            print(f"  Risk      : ₹{position['risk']}")
            print(f"  Reward    : ₹{position['reward']}")
            print(f"  R:R       : {position['rr_ratio']}")
            print(f"  ACTION    : 🟢 PLACE BUY ORDER")

            return {
                "symbol"   : symbol,
                "signal"   : "BUY",
                "token"    : instrument_token,
                **position
            }
        else:
            print(f"  DOM       : ❌ NOT CONFIRMED (BID {ratio:.2f}x ASK — need 1.5x)")
            print(f"  ACTION    : ⏸️  SKIP — wait for DOM confirmation")
            return {"symbol": symbol, "signal": "SKIP_DOM"}

    elif signal == "SELL":
        print(f"  ACTION    : 🔴 CLOSE POSITION")
        return {"symbol": symbol, "signal": "SELL", "price": latest_price}

    else:
        print(f"  ACTION    : ⬜ NO TRADE")
        return {"symbol": symbol, "signal": "NONE"}


# ─── TEST RUN ─────────────────────────────────────────
if __name__ == "__main__":
    from data_pipeline import load_from_db

    WATCHLIST = {
        "RELIANCE"  : "NSE_EQ|INE002A01018",
        "TCS"       : "NSE_EQ|INE467B01029",
        "INFY"      : "NSE_EQ|INE009A01021",
        "HDFCBANK"  : "NSE_EQ|INE040A01034",
        "ICICIBANK" : "NSE_EQ|INE090A01021"
    }

    print("=" * 45)
    print("   STRATEGY SIGNAL SCANNER")
    print("=" * 45)

    results = []
    for symbol, token in WATCHLIST.items():
        df     = load_from_db(symbol)
        result = get_trade_signal(symbol, token, df, capital=100000)
        results.append(result)

    # Summary
    print(f"\n{'='*45}")
    print("   SUMMARY")
    print(f"{'='*45}")
    buy_signals = [r for r in results if r["signal"] == "BUY"]
    print(f"  BUY signals  : {len(buy_signals)}")
    print(f"  Stocks scanned: {len(results)}")
    if buy_signals:
        print(f"\n  Stocks to BUY:")
        for r in buy_signals:
            print(f"  → {r['symbol']} @ ₹{r['entry']:.2f} | SL: ₹{r['stop_loss']:.2f} | TGT: ₹{r['target']:.2f}")

            