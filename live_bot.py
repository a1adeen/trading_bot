# live_bot.py
# Live intraday bot using Market Quotes API
# VWAP + RSI + DOM strategy
# Seeds price history from yesterday for immediate RSI

import upstox_client
import pandas as pd
import schedule
import time
import logging
import json
from datetime import datetime, date, timedelta
from config import ACCESS_TOKEN
from nifty100_tokens import NIFTY100

# ─── CONFIG ───────────────────────────────────────────
CAPITAL        = 50000
RISK_PCT       = 0.01
MARKET_OPEN    = "09:15"
MARKET_CLOSE   = "15:25"
PAPER_TRADE    = True
MAX_DAILY_LOSS = 2000
WATCHLIST      = NIFTY100

# ─── LOGGING ──────────────────────────────────────────
logging.basicConfig(
    filename="bot_log.txt",
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)
def log(msg):
    print(msg)
    logging.info(msg)

# ─── CONNECT ──────────────────────────────────────────
configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client   = upstox_client.ApiClient(configuration)
market_api   = upstox_client.MarketQuoteApi(api_client)
order_api    = upstox_client.OrderApiV3(api_client)
history_api  = upstox_client.HistoryApi(api_client)

# ─── STATE ────────────────────────────────────────────
price_history  = {}   # symbol -> list of close prices
vwap_data      = {}   # symbol -> vwap components
open_positions = {}   # symbol -> position details
daily_pnl      = 0

# ─── GET LAST TRADING DAY ─────────────────────────────
def get_last_trading_day():
    """Get most recent past weekday."""
    day = date.today()
    for i in range(1, 8):
        d = day - timedelta(days=i)
        if d.weekday() < 5:
            return d.strftime("%Y-%m-%d")
    return (day - timedelta(days=1)).strftime("%Y-%m-%d")

# ─── SEED PRICE HISTORY ───────────────────────────────
def seed_price_history():
    """
    Fetch yesterday's 30min candles to seed RSI calculation.
    Without this RSI defaults to 50 until enough live scans happen.
    """
    yesterday = get_last_trading_day()
    log(f"\n  Seeding price history from {yesterday}...")
    seeded = 0

    for symbol, token in list(WATCHLIST.items()):
        try:
            data    = history_api.get_historical_candle_data1(
                token, "30minute", yesterday, yesterday, "2.0"
            )
            candles = data.data.candles
            if candles:
                # Extract close prices
                prices = [float(c[4]) for c in candles]
                price_history[symbol] = prices[-20:]
                seeded += 1
        except:
            pass
        time.sleep(0.15)

    log(f"  ✅ Seeded {seeded}/{len(WATCHLIST)} stocks")

# ─── FETCH LIVE QUOTE ─────────────────────────────────
def fetch_quote(symbol, token):
    """Fetch live market quote."""
    try:
        quote = market_api.get_full_market_quote(token, "2.0")
        key   = list(quote.data.keys())[0]
        data  = quote.data[key]
        return {
            "ltp"      : data.last_price,
            "open"     : data.ohlc.open,
            "high"     : data.ohlc.high,
            "low"      : data.ohlc.low,
            "close"    : data.ohlc.close,
            "volume"   : data.volume,
            "buy_qty"  : sum(l.quantity for l in data.depth.buy[:5]  if l.quantity),
            "sell_qty" : sum(l.quantity for l in data.depth.sell[:5] if l.quantity),
        }
    except:
        return None

# ─── UPDATE PRICE HISTORY ─────────────────────────────
def update_history(symbol, price):
    """Append latest price and keep last 50."""
    if symbol not in price_history:
        price_history[symbol] = []
    price_history[symbol].append(price)
    if len(price_history[symbol]) > 50:
        price_history[symbol] = price_history[symbol][-50:]

# ─── COMPUTE RSI ──────────────────────────────────────
def compute_rsi(prices, period=9):
    """Compute RSI from price list. Returns 50 if not enough data."""
    if len(prices) < period + 1:
        return 50.0
    s      = pd.Series(prices)
    delta  = s.diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l  = loss.ewm(com=period - 1, adjust=False).mean()
    rs     = avg_g / avg_l
    rsi    = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)

# ─── COMPUTE VWAP ─────────────────────────────────────
def compute_vwap(symbol, quote):
    """Running VWAP using typical price × volume."""
    if symbol not in vwap_data:
        vwap_data[symbol] = {"cum_tp_vol": 0.0, "cum_vol": 0.0}

    tp  = (quote["high"] + quote["low"] + quote["ltp"]) / 3
    vol = quote["volume"]

    vwap_data[symbol]["cum_tp_vol"] = tp * vol
    vwap_data[symbol]["cum_vol"]    = vol

    if vol == 0:
        return quote["ltp"]

    return round(
        vwap_data[symbol]["cum_tp_vol"] / vwap_data[symbol]["cum_vol"], 2
    )

# ─── CHECK SIGNAL ─────────────────────────────────────
def check_signal(symbol, quote, rsi, vwap):
    """
    BUY  : price > VWAP + RSI > 55 + DOM BID >= 1.5x ASK
    SELL : price < VWAP + RSI < 45 + DOM ASK >= 1.5x BID
    """
    price     = quote["ltp"]
    buy_qty   = quote["buy_qty"]
    sell_qty  = quote["sell_qty"]

    dom_ratio    = buy_qty / sell_qty if sell_qty > 0 else 0
    dom_buy_ok   = dom_ratio >= 1.5
    dom_sell_ok  = dom_ratio <= 0.67

    above_vwap   = price > vwap
    below_vwap   = price < vwap

    # Get previous RSI for crossover detection
    prices   = price_history.get(symbol, [])
    prev_rsi = compute_rsi(prices[:-1]) if len(prices) > 2 else 50

    rsi_cross_up   = prev_rsi < 50 and rsi >= 50
    rsi_cross_down = prev_rsi > 50 and rsi <= 50
    rsi_bullish    = rsi > 55
    rsi_bearish    = rsi < 45

    # BUY
    if above_vwap and rsi_bullish and rsi < 75 and dom_buy_ok:
        return "BUY", dom_ratio

    # SELL
    elif below_vwap and rsi_bearish and dom_sell_ok:
        return "SELL", dom_ratio

    return "NONE", dom_ratio

# ─── PLACE ORDER ──────────────────────────────────────
def place_order(symbol, token, qty, side):
    if PAPER_TRADE:
        log(f"  📋 PAPER — {side} {qty} {symbol}")
        return "PAPER_123"
    try:
        order = upstox_client.PlaceOrderV3Request(
            quantity=qty, product="I", validity="DAY",
            price=0, instrument_token=token,
            order_type="MARKET", transaction_type=side,
            disclosed_quantity=0, trigger_price=0,
            is_amo=False, slice=True
        )
        r = order_api.place_order(order)
        log(f"  ✅ ORDER placed — {r.data.order_id}")
        return r.data.order_id
    except Exception as e:
        log(f"  ❌ Order failed: {e}")
        return None

# ─── MONITOR OPEN POSITIONS ───────────────────────────
def monitor_positions():
    global daily_pnl
    for symbol, pos in list(open_positions.items()):
        quote = fetch_quote(symbol, WATCHLIST[symbol])
        if not quote:
            continue
        price = quote["ltp"]

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

# ─── MAIN SCAN ────────────────────────────────────────
def run_scan():
    global daily_pnl

    now = datetime.now().strftime("%H:%M")
    log(f"\n{'='*50}")
    log(f"  SCAN at {now} | P&L: ₹{daily_pnl:.0f} | Positions: {len(open_positions)}")
    log(f"{'='*50}")

    # Kill switch
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"  🚨 KILL SWITCH — loss ₹{abs(daily_pnl):.0f} hit limit. Bot stopped.")
        return

    # Market hours check
    if now < MARKET_OPEN or now > MARKET_CLOSE:
        log(f"  ⏸️  Market closed — waiting")
        return

    # Square off all at 3:25pm
    if now >= "15:25":
        log("  ⏰ 3:25pm — squaring off all positions")
        for symbol in list(open_positions.keys()):
            pos = open_positions[symbol]
            place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
            del open_positions[symbol]
        return

    # Reset VWAP at market open
    if now == "09:15":
        vwap_data.clear()
        log("  🔄 VWAP reset for new day")

    # Monitor open positions first
    if open_positions:
        monitor_positions()

    # Scan all stocks
    signals_found = 0

    for symbol, token in WATCHLIST.items():

        if symbol in open_positions:
            continue

        # Fetch live quote
        quote = fetch_quote(symbol, token)
        if not quote:
            continue

        price = quote["ltp"]

        # Update price history with latest price
        update_history(symbol, price)

        # Compute indicators
        prices = price_history.get(symbol, [])
        rsi    = compute_rsi(prices)
        vwap   = compute_vwap(symbol, quote)
        atr    = quote["high"] - quote["low"]

        # Check signal
        signal, dom_ratio = check_signal(symbol, quote, rsi, vwap)

        # Save all data for AI training later
        with open("signal_log.json", "a") as f:
            f.write(json.dumps({
                "time"       : now,
                "symbol"     : symbol,
                "price"      : price,
                "vwap"       : vwap,
                "rsi"        : rsi,
                "signal"     : signal,
                "dom_ratio"  : round(dom_ratio, 2),
                "above_vwap" : int(price > vwap),
                "volume"     : quote["volume"]
            }) + "\n")

        # Act on signal
        if signal == "BUY":
            sl_pts  = round(atr * 0.3, 2) if atr > 0 else 5
            tgt_pts = round(atr * 0.6, 2) if atr > 0 else 10
            risk    = CAPITAL * RISK_PCT
            qty     = max(1, int(risk / sl_pts)) if sl_pts > 0 else 1

            log(f"\n  {symbol} — ₹{price} | VWAP:₹{vwap} | RSI:{rsi} | DOM:{dom_ratio:.2f}x")
            log(f"  🟢 BUY | Entry:₹{price} SL:₹{round(price-sl_pts,2)} TGT:₹{round(price+tgt_pts,2)} Qty:{qty}")

            order_id = place_order(symbol, token, qty, "BUY")
            if order_id:
                open_positions[symbol] = {
                    "entry"   : price,
                    "sl"      : round(price - sl_pts,  2),
                    "target"  : round(price + tgt_pts, 2),
                    "sl_pts"  : sl_pts,
                    "tgt_pts" : tgt_pts,
                    "qty"     : qty
                }
                signals_found += 1

        elif signal == "SELL" and symbol in open_positions:
            pos = open_positions[symbol]
            pnl = (price - pos["entry"]) * pos["qty"]
            daily_pnl += pnl
            place_order(symbol, token, pos["qty"], "SELL")
            del open_positions[symbol]
            log(f"\n  🔴 SELL {symbol} @ ₹{price} | P&L: ₹{pnl:.0f}")
            signals_found += 1

        time.sleep(0.2)

    if signals_found == 0:
        log(f"  ⬜ No signals this scan")
    else:
        log(f"  ✅ {signals_found} signal(s) acted on")

# ─── START ────────────────────────────────────────────
if __name__ == "__main__":
    log("=" * 50)
    log("  LIVE BOT STARTING")
    log(f"  Capital    : ₹{CAPITAL:,}")
    log(f"  Mode       : {'PAPER TRADING' if PAPER_TRADE else '🔴 LIVE TRADING'}")
    log(f"  Scan every : 5 minutes")
    log(f"  Kill switch: ₹{MAX_DAILY_LOSS} daily loss")
    log(f"  Stocks     : {len(WATCHLIST)} Nifty 100 stocks")
    log("=" * 50)

    # Seed RSI with yesterday's data
    seed_price_history()

    # First scan immediately
    run_scan()

    # Then every 5 minutes
    schedule.every(5).minutes.do(run_scan)

    log("\n  Bot running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(10)