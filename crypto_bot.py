# crypto_bot.py
# Live crypto trading bot — Binance
# Same VWAP + RSI + DOM strategy as NSE bot
# Runs 24/7 — best used during night hours 10pm - 9am IST
# Paper trading mode — no real money

from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import schedule
import time
import logging
import json
import os
from datetime import datetime
from config import BINANCE_API_KEY, BINANCE_API_SECRET

# ─── CONFIG ───────────────────────────────────────────
CAPITAL        = 50000   # in INR equivalent
RISK_PCT       = 0.01    # risk 1% per trade
PAPER_TRADE    = True    # True = no real orders
MAX_DAILY_LOSS = 2000    # kill switch in INR

# Top crypto pairs to scan — USDT pairs
WATCHLIST = [
    "BTCUSDT",   # Bitcoin
    "ETHUSDT",   # Ethereum
    "BNBUSDT",   # Binance Coin
    "SOLUSDT",   # Solana
    "XRPUSDT",   # Ripple
    "ADAUSDT",   # Cardano
    "DOGEUSDT",  # Dogecoin
    "AVAXUSDT",  # Avalanche
    "MATICUSDT", # Polygon
    "LINKUSDT",  # Chainlink
    "DOTUSDT",   # Polkadot
    "LTCUSDT",   # Litecoin
    "UNIUSDT",   # Uniswap
    "ATOMUSDT",  # Cosmos
    "NEARUSDT",  # Near Protocol
    "APTUSDT",   # Aptos
    "ARBUSDT",   # Arbitrum
    "OPUSDT",    # Optimism
    "INJUSDT",   # Injective
    "SUIUSDT",   # Sui
]

# ─── LOGGING ──────────────────────────────────────────
logging.basicConfig(
    filename="crypto_bot_log.txt",
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)

def log(msg):
    print(msg)
    logging.info(msg)

# ─── CONNECT TO BINANCE ───────────────────────────────
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# ─── BOT STATE ────────────────────────────────────────
price_history  = {}
open_positions = {}
daily_pnl      = 0
closed_trades  = []

# ─── SAVE POSITIONS ───────────────────────────────────
def save_positions():
    with open("crypto_positions.json", "w") as f:
        json.dump(open_positions, f, indent=2)

# ─── FETCH LIVE CANDLES ───────────────────────────────
def fetch_candles(symbol, interval="5m", limit=50):
    """
    Fetch recent candles from Binance.
    interval options: 1m, 3m, 5m, 15m, 30m, 1h
    """
    try:
        candles = client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        df = pd.DataFrame(candles, columns=[
            "open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base",
            "taker_buy_quote","ignore"
        ])
        df["open"]   = df["open"].astype(float)
        df["high"]   = df["high"].astype(float)
        df["low"]    = df["low"].astype(float)
        df["close"]  = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except BinanceAPIException as e:
        log(f"  Fetch failed {symbol}: {e}")
        return None

# ─── FETCH ORDER BOOK (DOM) ───────────────────────────
def fetch_dom(symbol):
    """
    Fetch order book depth from Binance.
    Returns BID volume and ASK volume (top 5 levels).
    """
    try:
        depth = client.get_order_book(symbol=symbol, limit=5)
        bid_vol = sum(float(b[1]) for b in depth["bids"])
        ask_vol = sum(float(a[1]) for a in depth["asks"])
        ratio   = bid_vol / ask_vol if ask_vol > 0 else 0
        return bid_vol, ask_vol, ratio
    except:
        return 0, 0, 0

# ─── COMPUTE VWAP ─────────────────────────────────────
def compute_vwap(df):
    """VWAP from candle data."""
    df = df.copy()
    df["tp"]     = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]
    vwap = df["tp_vol"].cumsum() / df["volume"].cumsum()
    return round(float(vwap.iloc[-1]), 6)

# ─── COMPUTE RSI ──────────────────────────────────────
def compute_rsi(df, period=9):
    """RSI from close prices."""
    prices = df["close"]
    if len(prices) < period + 1:
        return 50.0
    delta  = prices.diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l  = loss.ewm(com=period - 1, adjust=False).mean()
    rs     = avg_g / avg_l
    rsi    = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)

# ─── CHECK SIGNAL ─────────────────────────────────────
def check_signal(df, rsi, vwap, dom_ratio):
    """
    Same strategy as NSE bot:
    BUY  : price > VWAP + 58 < RSI < 72 + DOM BID >= 2x ASK
    SELL : price < VWAP + RSI < 45       + DOM ASK >= 2x BID
    """
    price = float(df["close"].iloc[-1])
    vol   = float(df["volume"].iloc[-1])
    avg_vol = float(df["volume"].mean())

    above_vwap  = price > vwap
    below_vwap  = price < vwap
    rsi_bullish = 58 < rsi < 72
    rsi_bearish = rsi < 45
    vol_ok      = vol > avg_vol * 0.8
    dom_buy_ok  = dom_ratio >= 2.0
    dom_sell_ok = dom_ratio <= 0.5

    if above_vwap and rsi_bullish and dom_buy_ok and vol_ok:
        return "BUY"
    elif below_vwap and rsi_bearish and dom_sell_ok:
        return "SELL"
    return "NONE"

# ─── PLACE ORDER ──────────────────────────────────────
def place_order(symbol, side, quantity):
    if PAPER_TRADE:
        log(f"  PAPER — {side} {quantity} {symbol}")
        return "PAPER_123"
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        log(f"  ORDER placed — {order['orderId']}")
        return order["orderId"]
    except BinanceAPIException as e:
        log(f"  Order failed: {e}")
        return None

# ─── SUCCESS ALERT ────────────────────────────────────
def success_alert(symbol, entry, exit_price, pnl):
    print("\033[1m" + "=" * 50 + "\033[0m")
    print("\033[1m" + f"  TARGET HIT — {symbol}" + "\033[0m")
    print("\033[1m" + f"  Entry  : {entry}" + "\033[0m")
    print("\033[1m" + f"  Exit   : {exit_price}" + "\033[0m")
    print("\033[1m" + f"  Profit : Rs.{pnl:.2f}" + "\033[0m")
    print("\033[1m" + "=" * 50 + "\033[0m")
    os.system(f'msg * "CRYPTO TARGET: {symbol} +Rs.{pnl:.0f}"')

# ─── SL ALERT ─────────────────────────────────────────
def sl_alert(symbol, pnl):
    os.system(f'msg * "CRYPTO SL HIT: {symbol} -Rs.{abs(pnl):.0f}"')

# ─── MONITOR POSITIONS ────────────────────────────────
def monitor_positions():
    global daily_pnl
    for symbol, pos in list(open_positions.items()):
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            price  = float(ticker["price"])
        except:
            continue

        if price <= pos["sl"]:
            pnl = (price - pos["entry"]) * pos["qty"]
            daily_pnl += pnl
            sl_alert(symbol, pnl)
            log(f"  SL HIT — {symbol} @ {price} | P&L: Rs.{pnl:.2f}")
            place_order(symbol, "SELL", pos["qty"])
            closed_trades.append({"symbol": symbol, "pnl": pnl, "result": "SL"})
            del open_positions[symbol]
            save_positions()

        elif price >= pos["target"]:
            pnl = (price - pos["entry"]) * pos["qty"]
            daily_pnl += pnl
            success_alert(symbol, pos["entry"], price, pnl)
            log(f"  TARGET HIT — {symbol} @ {price} | P&L: Rs.{pnl:.2f}")
            place_order(symbol, "SELL", pos["qty"])
            closed_trades.append({"symbol": symbol, "pnl": pnl, "result": "TARGET"})
            del open_positions[symbol]
            save_positions()

# ─── MAIN SCAN ────────────────────────────────────────
def run_scan():
    global daily_pnl

    now = datetime.now().strftime("%H:%M")
    ist = datetime.now().strftime("%d %b %Y %H:%M:%S IST")

    log(f"\n{'='*50}")
    log(f"  CRYPTO SCAN at {ist}")
    log(f"  P&L: Rs.{daily_pnl:.2f} | Positions: {len(open_positions)}")
    log(f"{'='*50}")

    # Kill switch
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"  KILL SWITCH — loss Rs.{abs(daily_pnl):.0f} exceeded. Bot stopped.")
        return

    # Monitor open positions
    if open_positions:
        monitor_positions()

    # Scan all crypto pairs
    signals_found = 0

    for symbol in WATCHLIST:
        if symbol in open_positions:
            continue

        # Fetch 5min candles
        df = fetch_candles(symbol, interval="5m", limit=50)
        if df is None or len(df) < 15:
            continue

        # Compute indicators
        rsi  = compute_rsi(df)
        vwap = compute_vwap(df)
        price = float(df["close"].iloc[-1])
        atr   = float(df["high"].iloc[-1]) - float(df["low"].iloc[-1])

        # Fetch order book
        bid_vol, ask_vol, dom_ratio = fetch_dom(symbol)

        # Check signal
        signal = check_signal(df, rsi, vwap, dom_ratio)

        # Save for AI training
        with open("crypto_signal_log.json", "a") as f:
            f.write(json.dumps({
                "time"       : now,
                "symbol"     : symbol,
                "price"      : price,
                "vwap"       : vwap,
                "rsi"        : rsi,
                "signal"     : signal,
                "dom_ratio"  : round(dom_ratio, 2),
                "above_vwap" : int(price > vwap),
            }) + "\n")

        if signal == "BUY":
            # Position sizing
            sl_pts  = round(atr * 0.3, 6)
            tgt_pts = round(atr * 0.6, 6)

            # Calculate quantity based on USDT value
            usdt_risk = (CAPITAL * RISK_PCT) / 83  # convert INR to USDT
            qty       = round(usdt_risk / sl_pts, 6) if sl_pts > 0 else 0.001
            qty       = max(0.001, qty)

            log(f"\n  {symbol}")
            log(f"  Price: {price} | VWAP: {vwap} | RSI: {rsi} | DOM: {dom_ratio:.2f}x")
            log(f"  BUY | SL: {round(price-sl_pts,6)} | TGT: {round(price+tgt_pts,6)} | Qty: {qty}")

            chart_url = f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}"
            log(f"  Chart: {chart_url}")

            order_id = place_order(symbol, "BUY", qty)
            if order_id:
                open_positions[symbol] = {
                    "entry"   : price,
                    "sl"      : round(price - sl_pts,  6),
                    "target"  : round(price + tgt_pts, 6),
                    "sl_pts"  : sl_pts,
                    "tgt_pts" : tgt_pts,
                    "qty"     : qty
                }
                save_positions()
                signals_found += 1

        elif signal == "SELL" and symbol in open_positions:
            pos = open_positions[symbol]
            pnl = (price - pos["entry"]) * pos["qty"] * 83
            daily_pnl += pnl
            place_order(symbol, "SELL", pos["qty"])
            closed_trades.append({"symbol": symbol, "pnl": pnl, "result": "SELL"})
            del open_positions[symbol]
            save_positions()
            log(f"  SELL {symbol} @ {price} | P&L: Rs.{pnl:.2f}")
            signals_found += 1

        time.sleep(0.5)  # avoid rate limits

    log(f"\n  Scanned: {len(WATCHLIST)} | Signals: {signals_found}")
    if closed_trades:
        wins = len([t for t in closed_trades if t["pnl"] > 0])
        log(f"  Closed: {len(closed_trades)} | Wins: {wins} | P&L: Rs.{daily_pnl:.2f}")

# ─── START ────────────────────────────────────────────
if __name__ == "__main__":
    log("=" * 50)
    log("  CRYPTO BOT STARTING")
    log(f"  Capital    : Rs.{CAPITAL:,}")
    log(f"  Risk/trade : Rs.{int(CAPITAL * RISK_PCT)} (1%)")
    log(f"  Mode       : {'PAPER TRADING' if PAPER_TRADE else 'LIVE TRADING'}")
    log(f"  Strategy   : VWAP + RSI(58-72) + DOM(2x)")
    log(f"  Pairs      : {len(WATCHLIST)} crypto pairs")
    log(f"  Runs       : 24/7 — every 5 minutes")
    log("=" * 50)

    # First scan immediately
    run_scan()

    # Then every 5 minutes
    schedule.every(5).minutes.do(run_scan)

    log("\n  Crypto bot running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        if open_positions:
            monitor_positions()
        time.sleep(30)