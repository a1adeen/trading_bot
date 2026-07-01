# live_bot.py
# Live intraday bot — VWAP + RSI + Volume + DOM
# Runs every 15 mins from 9:15am to 3:15pm
# Paper trading mode — signals only, no real orders yet

import upstox_client
import pandas as pd
import schedule
import time
import logging
from datetime import datetime
from config import ACCESS_TOKEN

# ─── CONFIG ───────────────────────────────────────────
CAPITAL       = 50000
RISK_PCT      = 0.01
MARKET_OPEN   = "09:15"
MARKET_CLOSE  = "15:15"
PAPER_TRADE   = True    # True = signals only | False = real orders

WATCHLIST = {
    "RELIANCE"  : "NSE_EQ|INE002A01018",
    "TCS"       : "NSE_EQ|INE467B01029",
    "INFY"      : "NSE_EQ|INE009A01021",
    "HDFCBANK"  : "NSE_EQ|INE040A01034",
    "ICICIBANK" : "NSE_EQ|INE090A01021"
}

# ─── LOGGING ──────────────────────────────────────────
logging.basicConfig(
    filename = "bot_log.txt",
    level    = logging.INFO,
    format   = "%(asctime)s | %(message)s"
)

def log(msg):
    print(msg)
    logging.info(msg)

# ─── CONNECT ──────────────────────────────────────────
configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client    = upstox_client.ApiClient(configuration)
history_api   = upstox_client.HistoryApi(api_client)
market_api    = upstox_client.MarketQuoteApi(api_client)
order_api     = upstox_client.OrderApiV3(api_client)

# ─── TRACK OPEN POSITIONS ─────────────────────────────
open_positions = {}  # symbol -> position details
daily_pnl      = 0
MAX_DAILY_LOSS = 2000  # kill switch

# ─── FETCH LIVE 15MIN CANDLES ─────────────────────────
def fetch_candles(symbol, token):
    """Fetch today's 15min candles."""
    try:
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")

        data = history_api.get_intraday_candle_data(
            token,
            "15minute",
            "2.0"
        )
        candles = data.data.candles
        if not candles:
            log(f"  ⚠️ No candles yet for {symbol}")
            return None

        df = pd.DataFrame(candles, columns=[
            "date","open","high","low","close","volume","oi"
        ])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        log(f"  📊 {symbol} — {len(df)} candles fetched")
        return df

    except Exception as e:
        log(f"  ❌ Fetch failed {symbol}: {e}")
        return None
# ─── COMPUTE INDICATORS ───────────────────────────────
def compute_indicators(df):
    """Add VWAP, RSI, ATR to dataframe."""
    df = df.copy()

    # VWAP — resets each day
    df["tp"]     = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]
    df["vwap"]   = df["tp_vol"].cumsum() / df["volume"].cumsum()

    # RSI 14
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs       = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR 14
    df["tr"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = df["tr"].ewm(com=13, adjust=False).mean()

    return df

# ─── CHECK SIGNAL ─────────────────────────────────────
def check_signal(df):
    """Check latest candle for signal."""
    if len(df) < 5:
        return "NONE"

    i     = len(df) - 1
    curr  = df.iloc[i]
    prev  = df.iloc[i - 1]

    price = curr["close"]
    vwap  = curr["vwap"]
    rsi   = curr["rsi"]
    vol   = curr["volume"]
    avg_vol = df["volume"].iloc[-10:].mean()

    # Conditions
    price_above_vwap = price > vwap
    price_below_vwap = price < vwap
    rsi_cross_up     = prev["rsi"] < 50 and rsi >= 50
    rsi_cross_down   = prev["rsi"] > 50 and rsi <= 50
    vol_ok           = vol > avg_vol * 0.8

    # Volume buildup last 2 candles
    buy_vol_prev  = prev["volume"] if prev["close"] > prev["open"] else 0
    buy_vol_curr  = curr["volume"] if curr["close"] > curr["open"] else 0
    sell_vol_prev = prev["volume"] if prev["close"] < prev["open"] else 0
    sell_vol_curr = curr["volume"] if curr["close"] < curr["open"] else 0

    buy_building  = buy_vol_curr  > buy_vol_prev  and buy_vol_prev  > 0
    sell_building = sell_vol_curr > sell_vol_prev and sell_vol_prev > 0

    # BUY signal
    if price_above_vwap and rsi_cross_up and (buy_building or vol_ok):
        return "BUY"

    # SELL signal
    elif price_below_vwap and rsi_cross_down and (sell_building or vol_ok):
        return "SELL"

    return "NONE"

# ─── CHECK DOM ────────────────────────────────────────
def check_dom(token):
    """Check depth of market."""
    try:
        depth    = market_api.get_full_market_quote(token, "2.0")
        key      = list(depth.data.keys())[0]
        dom      = depth.data[key].depth
        bid_vol  = sum(l.quantity for l in dom.buy[:5]  if l.quantity)
        ask_vol  = sum(l.quantity for l in dom.sell[:5] if l.quantity)
        ratio    = bid_vol / ask_vol if ask_vol > 0 else 0
        return ratio >= 1.5, ratio <= 0.67, ratio
    except:
        return False, False, 0

# ─── PLACE ORDER ──────────────────────────────────────
def place_order(symbol, token, qty, transaction_type):
    """Place real or paper order."""
    if PAPER_TRADE:
        log(f"  📋 PAPER ORDER — {transaction_type} {qty} {symbol}")
        return "PAPER_ORDER_123"

    try:
        order = upstox_client.PlaceOrderV3Request(
            quantity         = qty,
            product          = "I",           # intraday
            validity         = "DAY",
            price            = 0,
            instrument_token = token,
            order_type       = "MARKET",
            transaction_type = transaction_type,
            disclosed_quantity = 0,
            trigger_price    = 0,
            is_amo           = False,
            slice            = True
        )
        response = order_api.place_order(order)
        log(f"  ✅ ORDER PLACED — {response.data.order_id}")
        return response.data.order_id
    except Exception as e:
        log(f"  ❌ Order failed: {e}")
        return None

# ─── MONITOR POSITIONS ────────────────────────────────
def monitor_positions():
    """Check if any open position hit SL or Target."""
    global daily_pnl

    for symbol, pos in list(open_positions.items()):
        try:
            quote = market_api.get_full_market_quote(
                WATCHLIST[symbol], "2.0"
            )
            key   = list(quote.data.keys())[0]
            price = quote.data[key].last_price

            if price <= pos["sl"]:
                pnl = -pos["sl_pts"] * pos["qty"]
                daily_pnl += pnl
                log(f"  🔴 SL HIT — {symbol} @ ₹{price} | P&L: ₹{pnl:.0f}")
                place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
                del open_positions[symbol]

            elif price >= pos["target"]:
                pnl = pos["tgt_pts"] * pos["qty"]
                daily_pnl += pnl
                log(f"  🟢 TARGET HIT — {symbol} @ ₹{price} | P&L: ₹{pnl:.0f}")
                place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
                del open_positions[symbol]

        except Exception as e:
            log(f"  ❌ Monitor error {symbol}: {e}")

# ─── MAIN SCAN ────────────────────────────────────────
def run_scan():
    """Main function — runs every 15 minutes."""
    global daily_pnl

    now = datetime.now().strftime("%H:%M")
    log(f"\n{'='*50}")
    log(f"  SCAN at {now}")
    log(f"  Daily P&L: ₹{daily_pnl:.0f} | Positions: {len(open_positions)}")
    log(f"{'='*50}")

    # Kill switch
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"  🚨 KILL SWITCH — daily loss ₹{abs(daily_pnl):.0f} exceeded limit")
        return

    # Market closed check
    if now < MARKET_OPEN or now > MARKET_CLOSE:
        log(f"  ⏸️  Market closed")
        return

    # Monitor existing positions first
    if open_positions:
        log(f"  Monitoring {len(open_positions)} open positions...")
        monitor_positions()

    # Scan for new signals
    for symbol, token in WATCHLIST.items():
        if symbol in open_positions:
            continue  # already in trade

        df = fetch_candles(symbol, token)
        if df is None or len(df) < 5:
            continue

        df     = compute_indicators(df)
        signal = check_signal(df)

        curr  = df.iloc[-1]
        price = curr["close"]
        vwap  = curr["vwap"]
        rsi   = curr["rsi"]
        atr   = curr["atr"]

        log(f"\n  {symbol} — ₹{price:.1f} | VWAP:{vwap:.1f} | RSI:{rsi:.1f}")

        if signal == "BUY":
            log(f"  📶 BUY signal detected")
            dom_buy, _, ratio = check_dom(token)

            if dom_buy:
                sl_pts  = round(atr * 1.0, 2)
                tgt_pts = round(atr * 2.0, 2)
                risk    = CAPITAL * RISK_PCT
                qty     = max(1, int(risk / sl_pts))

                log(f"  ✅ DOM confirmed ({ratio:.2f}x)")
                log(f"  Entry:  ₹{price:.2f}")
                log(f"  SL:     ₹{price - sl_pts:.2f} (-{sl_pts:.1f})")
                log(f"  Target: ₹{price + tgt_pts:.2f} (+{tgt_pts:.1f})")
                log(f"  Qty:    {qty} shares")

                order_id = place_order(symbol, token, qty, "BUY")

                if order_id:
                    open_positions[symbol] = {
                        "entry"     : price,
                        "sl"        : round(price - sl_pts,  2),
                        "target"    : round(price + tgt_pts, 2),
                        "sl_pts"    : sl_pts,
                        "tgt_pts"   : tgt_pts,
                        "qty"       : qty,
                        "order_id"  : order_id
                    }
            else:
                log(f"  ⏸️  DOM weak ({ratio:.2f}x) — skip")

        elif signal == "SELL" and symbol in open_positions:
            log(f"  🔴 SELL signal — closing {symbol}")
            pos = open_positions[symbol]
            pnl = (price - pos["entry"]) * pos["qty"]
            daily_pnl += pnl
            place_order(symbol, token, pos["qty"], "SELL")
            del open_positions[symbol]
            log(f"  Closed {symbol} | P&L: ₹{pnl:.0f}")

        else:
            log(f"  ⬜ No signal")

    # Square off all positions at 3:15pm
    if now >= "15:15":
        log(f"\n  ⏰ 3:15pm — squaring off all positions")
        for symbol in list(open_positions.keys()):
            pos = open_positions[symbol]
            place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
            del open_positions[symbol]
            log(f"  Squared off {symbol}")

# ─── START BOT ────────────────────────────────────────
if __name__ == "__main__":
    log("=" * 50)
    log("  LIVE BOT STARTING")
    log(f"  Capital    : ₹{CAPITAL:,}")
    log(f"  Mode       : {'PAPER TRADING' if PAPER_TRADE else '🔴 LIVE TRADING'}")
    log(f"  Kill switch: ₹{MAX_DAILY_LOSS} daily loss")
    log(f"  Stocks     : {', '.join(WATCHLIST.keys())}")
    log("=" * 50)

    # Run immediately once
    run_scan()

    # Then every 15 minutes
    schedule.every(15).minutes.do(run_scan)

    log("\n  Bot running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(30)