# live_bot.py
# Live intraday bot — VWAP + RSI + DOM strategy
# Scans 92 Nifty 100 stocks every 5 minutes
# Paper trading mode — no real money
# Auto-saves positions for P&L dashboard

import upstox_client
import pandas as pd
import schedule
import time
import logging
import json
import os
from datetime import datetime, date, timedelta
from config import ACCESS_TOKEN
from nifty100_tokens import NIFTY100

# ─── CONFIG ───────────────────────────────────────────
CAPITAL        = 50000   # total capital
RISK_PCT       = 0.01    # risk 1% per trade = Rs.500
MARKET_OPEN    = "09:15"
MARKET_CLOSE   = "15:25"
PAPER_TRADE    = True    # True = no real orders
MAX_DAILY_LOSS = 2000    # kill switch
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

# ─── CONNECT TO UPSTOX ────────────────────────────────
configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client   = upstox_client.ApiClient(configuration)
market_api   = upstox_client.MarketQuoteApi(api_client)
order_api    = upstox_client.OrderApiV3(api_client)
history_api  = upstox_client.HistoryApi(api_client)

# ─── BOT STATE ────────────────────────────────────────
price_history  = {}   # stores last 50 prices per stock for RSI
vwap_data      = {}   # stores running VWAP per stock
open_positions = {}   # currently open trades
daily_pnl      = 0   # running P&L for today
closed_trades  = []   # all closed trades today

# ─── SAVE POSITIONS FOR DASHBOARD ─────────────────────
def save_positions():
    """
    Writes open_positions to positions.json.
    pnl_server.py reads this file to show dashboard.
    """
    with open("positions.json", "w") as f:
        json.dump(open_positions, f, indent=2)

# ─── GET LAST TRADING DAY ─────────────────────────────
def get_last_trading_day():
    """Returns most recent past weekday date string."""
    day = date.today()
    for i in range(1, 8):
        d = day - timedelta(days=i)
        if d.weekday() < 5:
            return d.strftime("%Y-%m-%d")
    return (day - timedelta(days=1)).strftime("%Y-%m-%d")

# ─── SEED PRICE HISTORY ───────────────────────────────
def seed_price_history():
    """
    Fetches yesterday's 30min candles for all stocks.
    This gives RSI enough data points to work from 9:15am.
    Without this RSI = 50 for first hour of trading.
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
                prices = [float(c[4]) for c in candles]
                price_history[symbol] = prices[-20:]
                seeded += 1
        except:
            pass
        time.sleep(0.15)

    log(f"  Seeded {seeded}/{len(WATCHLIST)} stocks with historical prices")

# ─── FETCH LIVE MARKET QUOTE ──────────────────────────
def fetch_quote(symbol, token):
    """
    Gets real-time price, OHLC, volume and order book
    for a stock from Upstox market quote API.
    """
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
    """Appends latest price to history. Keeps last 50 prices."""
    if symbol not in price_history:
        price_history[symbol] = []
    price_history[symbol].append(price)
    if len(price_history[symbol]) > 50:
        price_history[symbol] = price_history[symbol][-50:]

# ─── COMPUTE RSI ──────────────────────────────────────
def compute_rsi(prices, period=9):
    """
    RSI measures momentum strength 0-100.
    Above 55 = bullish momentum.
    Above 75 = overbought (we avoid buying here).
    Below 45 = bearish momentum.
    Returns 50 if not enough data.
    """
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
    """
    VWAP = Volume Weighted Average Price.
    Price above VWAP = stock is bullish today.
    Price below VWAP = stock is bearish today.
    Resets every morning at 9:15am.
    """
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
    BUY conditions (ALL must be true):
      1. Price > VWAP       → stock trending up today
      2. 55 < RSI < 75      → good momentum, not overbought
      3. BID qty > 1.5x ASK → more buyers than sellers in order book

    SELL conditions (ALL must be true):
      1. Price < VWAP       → stock trending down today
      2. RSI < 45           → bearish momentum
      3. ASK qty > 1.5x BID → more sellers than buyers
    """
    price    = quote["ltp"]
    buy_qty  = quote["buy_qty"]
    sell_qty = quote["sell_qty"]

    dom_ratio   = buy_qty / sell_qty if sell_qty > 0 else 0
    dom_buy_ok  = dom_ratio >= 1.5
    dom_sell_ok = dom_ratio <= 0.67

    above_vwap  = price > vwap
    below_vwap  = price < vwap
    rsi_bullish = 55 < rsi < 75
    rsi_bearish = rsi < 45

    if above_vwap and rsi_bullish and dom_buy_ok:
        return "BUY", dom_ratio
    elif below_vwap and rsi_bearish and dom_sell_ok:
        return "SELL", dom_ratio
    return "NONE", dom_ratio

# ─── PLACE ORDER ──────────────────────────────────────
def place_order(symbol, token, qty, side):
    """
    PAPER_TRADE = True  → just logs the order, no real money
    PAPER_TRADE = False → sends real order to Upstox
    """
    if PAPER_TRADE:
        log(f"  PAPER — {side} {qty} {symbol}")
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
        log(f"  ORDER placed — {r.data.order_id}")
        return r.data.order_id
    except Exception as e:
        log(f"  Order failed: {e}")
        return None

# ─── SUCCESS ALERT ────────────────────────────────────
def success_alert(symbol, entry, exit_price, pnl):
    """Bold terminal message when target is hit."""
    print("\033[1m" + "=" * 50 + "\033[0m")
    print("\033[1m" + f"  TARGET HIT — {symbol}" + "\033[0m")
    print("\033[1m" + f"  Entry  : Rs.{entry}" + "\033[0m")
    print("\033[1m" + f"  Exit   : Rs.{exit_price}" + "\033[0m")
    print("\033[1m" + f"  Profit : Rs.{pnl:.0f}" + "\033[0m")
    print("\033[1m" + f"  Today  : Rs.{daily_pnl:.0f}" + "\033[0m")
    print("\033[1m" + "=" * 50 + "\033[0m")
    os.system(f'msg * "TARGET HIT: {symbol} +Rs.{pnl:.0f}"')

# ─── SL ALERT ─────────────────────────────────────────
def sl_alert(symbol, pnl):
    """Windows popup when SL is hit."""
    os.system(f'msg * "SL HIT: {symbol} -Rs.{abs(pnl):.0f}"')

# ─── MONITOR OPEN POSITIONS ───────────────────────────
def monitor_positions():
    """
    Checks every open trade every 30 seconds.
    Closes trade automatically if:
      - Price drops to SL level → loss
      - Price rises to Target   → profit
    """
    global daily_pnl
    for symbol, pos in list(open_positions.items()):
        quote = fetch_quote(symbol, WATCHLIST[symbol])
        if not quote:
            continue
        price = quote["ltp"]

        # Stop loss hit
        if price <= pos["sl"]:
            pnl = -pos["sl_pts"] * pos["qty"]
            daily_pnl += pnl
            sl_alert(symbol, pnl)
            log(f"  SL HIT — {symbol} @ Rs.{price} | P&L: Rs.{pnl:.0f}")
            place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
            closed_trades.append({
                "symbol": symbol, "entry": pos["entry"],
                "exit": price, "pnl": pnl, "result": "SL"
            })
            del open_positions[symbol]
            save_positions()

        # Target hit
        elif price >= pos["target"]:
            pnl = pos["tgt_pts"] * pos["qty"]
            daily_pnl += pnl
            success_alert(symbol, pos["entry"], price, pnl)
            log(f"  TARGET HIT — {symbol} @ Rs.{price} | P&L: Rs.{pnl:.0f}")
            place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
            closed_trades.append({
                "symbol": symbol, "entry": pos["entry"],
                "exit": price, "pnl": pnl, "result": "TARGET"
            })
            del open_positions[symbol]
            save_positions()

# ─── MAIN SCAN ────────────────────────────────────────
def run_scan():
    """
    Main function — runs every 5 minutes.
    Scans all 92 stocks and acts on signals.
    """
    global daily_pnl

    now = datetime.now().strftime("%H:%M")
    log(f"\n{'='*50}")
    log(f"  SCAN at {now} | P&L: Rs.{daily_pnl:.0f} | Positions: {len(open_positions)}")
    log(f"{'='*50}")

    # Kill switch — stop bot if daily loss too high
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"  KILL SWITCH — loss Rs.{abs(daily_pnl):.0f} exceeded limit. Bot stopped.")
        return

    # Market hours check
    if now < MARKET_OPEN or now > MARKET_CLOSE:
        log(f"  Market closed — waiting")
        return

    # Square off ALL positions at 3:25pm
    if now >= "15:25":
        log("  3:25pm — squaring off all positions")
        for symbol in list(open_positions.keys()):
            pos = open_positions[symbol]
            place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
            del open_positions[symbol]
        save_positions()
        return

    # Reset VWAP every morning
    if now == "09:15":
        vwap_data.clear()
        log("  VWAP reset for new day")

    # Monitor open positions
    if open_positions:
        monitor_positions()

    # Scan all 92 stocks
    signals_found = 0
    skipped       = 0

    for symbol, token in WATCHLIST.items():

        # Skip if already in trade for this stock
        if symbol in open_positions:
            continue

        # Fetch live price and order book
        quote = fetch_quote(symbol, token)
        if not quote:
            continue

        price = quote["ltp"]

        # Add to price history for RSI
        update_history(symbol, price)

        # Compute indicators
        prices = price_history.get(symbol, [])
        rsi    = compute_rsi(prices)
        vwap   = compute_vwap(symbol, quote)
        atr    = quote["high"] - quote["low"]   # daily range

        # Check signal
        signal, dom_ratio = check_signal(symbol, quote, rsi, vwap)

        # Save data for AI training later
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

        # Act on BUY signal
        if signal == "BUY":
            # Position sizing — risk 1% of capital
            sl_pts  = round(atr * 0.3, 2) if atr > 0 else 5
            tgt_pts = round(atr * 0.6, 2) if atr > 0 else 10
            risk    = CAPITAL * RISK_PCT   # Rs.500
            qty     = max(1, int(risk / sl_pts)) if sl_pts > 0 else 1

            log(f"\n  {symbol}")
            log(f"  Price: Rs.{price} | VWAP: Rs.{vwap} | RSI: {rsi} | DOM: {dom_ratio:.2f}x")
            log(f"  BUY | Entry: Rs.{price} | SL: Rs.{round(price-sl_pts,2)} | TGT: Rs.{round(price+tgt_pts,2)} | Qty: {qty}")

            order_id = place_order(symbol, token, qty, "BUY")

            if order_id:
                # Manual chart link — click to verify
                chart_url = f"https://in.tradingview.com/chart/?symbol=NSE%3A{symbol}"
                log(f"  Chart: {chart_url}")

                # Save position
                open_positions[symbol] = {
                    "entry"   : price,
                    "sl"      : round(price - sl_pts,  2),
                    "target"  : round(price + tgt_pts, 2),
                    "sl_pts"  : sl_pts,
                    "tgt_pts" : tgt_pts,
                    "qty"     : qty
                }
                save_positions()
                signals_found += 1

        # Act on SELL signal for existing position
        elif signal == "SELL" and symbol in open_positions:
            pos = open_positions[symbol]
            pnl = (price - pos["entry"]) * pos["qty"]
            daily_pnl += pnl
            place_order(symbol, token, pos["qty"], "SELL")
            closed_trades.append({
                "symbol": symbol, "entry": pos["entry"],
                "exit": price, "pnl": pnl, "result": "SELL SIGNAL"
            })
            del open_positions[symbol]
            save_positions()
            log(f"\n  SELL {symbol} @ Rs.{price} | P&L: Rs.{pnl:.0f}")
            signals_found += 1

        else:
            skipped += 1

        time.sleep(0.2)

    log(f"\n  Scanned: {len(WATCHLIST)} | Signals: {signals_found} | Skipped: {skipped}")

    # Print daily summary
    if closed_trades:
        wins = len([t for t in closed_trades if t["pnl"] > 0])
        log(f"  Closed trades today: {len(closed_trades)} | Wins: {wins} | P&L: Rs.{daily_pnl:.0f}")

# ─── START BOT ────────────────────────────────────────
if __name__ == "__main__":
    log("=" * 50)
    log("  LIVE BOT STARTING")
    log(f"  Capital      : Rs.{CAPITAL:,}")
    log(f"  Risk/trade   : Rs.{int(CAPITAL * RISK_PCT)} (1% of capital)")
    log(f"  Mode         : {'PAPER TRADING' if PAPER_TRADE else 'LIVE TRADING'}")
    log(f"  Strategy     : VWAP + RSI(55-75) + DOM(1.5x)")
    log(f"  Scan every   : 5 minutes")
    log(f"  Monitor      : every 30 seconds")
    log(f"  Kill switch  : Rs.{MAX_DAILY_LOSS} daily loss")
    log(f"  Stocks       : {len(WATCHLIST)} Nifty 100 stocks")
    log("=" * 50)

    # Step 1 — Seed RSI with yesterday's prices
    seed_price_history()

    # Step 2 — First scan immediately
    run_scan()

    # Step 3 — Schedule scan every 5 minutes
    schedule.every(5).minutes.do(run_scan)

    log("\n  Bot running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()

        # Check open positions every 30 seconds
        if open_positions:
            monitor_positions()

        time.sleep(30)