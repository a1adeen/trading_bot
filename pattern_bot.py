# pattern_bot.py
# ANTICIPATION-BASED pattern recognition bot
# Detects patterns WHILE they are forming
# Entry happens BEFORE pattern completes
# Confirmed by price action + volume buildup
# Tracks daily P&L with account balance

import upstox_client
import pandas as pd
import numpy as np
import json
import time
import schedule
import logging
from datetime import datetime, date, timedelta
from config import ACCESS_TOKEN
from nifty100_tokens import NIFTY100

# ─── CONFIG ───────────────────────────────────────────
INITIAL_BALANCE  = 50000
RISK_PCT         = 0.01
MIN_CONFIDENCE   = 60    # enter when 60% pattern buildup
PAPER_TRADE      = True
MAX_DAILY_LOSS   = 2000
WATCHLIST        = NIFTY100
MAX_POSITIONS    = 5

# ─── LOGGING ──────────────────────────────────────────
logging.basicConfig(
    filename="pattern_bot_log.txt",
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

# ─── ACCOUNT ──────────────────────────────────────────
account = {
    "balance"      : INITIAL_BALANCE,
    "daily_pnl"    : 0,
    "total_pnl"    : 0,
    "trades_today" : 0,
    "wins_today"   : 0,
    "losses_today" : 0,
    "date"         : str(date.today())
}
open_positions = {}
closed_trades  = []

# ─── SAVE / LOAD ──────────────────────────────────────
def save_account():
    with open("pattern_account.json", "w") as f:
        json.dump({
            "account"        : account,
            "open_positions" : open_positions,
            "closed_trades"  : closed_trades[-50:]
        }, f, indent=2)

def load_account():
    global account, open_positions, closed_trades
    try:
        with open("pattern_account.json") as f:
            data = json.load(f)
        if data["account"].get("date") != str(date.today()):
            log(f"  New day — balance carries over: Rs.{data['account']['balance']:,.0f}")
            account = {
                "balance"      : data["account"]["balance"],
                "daily_pnl"    : 0,
                "total_pnl"    : data["account"]["total_pnl"],
                "trades_today" : 0,
                "wins_today"   : 0,
                "losses_today" : 0,
                "date"         : str(date.today())
            }
            open_positions = {}
            closed_trades  = []
        else:
            account        = data["account"]
            open_positions = data.get("open_positions", {})
            closed_trades  = data.get("closed_trades", [])
    except:
        log("  Starting fresh — Rs.50,000")

# ─── FETCH CANDLES ────────────────────────────────────
def fetch_candles(token, days=3):
    try:
        today     = date.today().strftime("%Y-%m-%d")
        from_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        data      = history_api.get_historical_candle_data1(
            token, "30minute", today, from_date, "2.0"
        )
        candles = data.data.candles
        if not candles or len(candles) < 5:
            return None
        df = pd.DataFrame(candles, columns=[
            "date","open","high","low","close","volume","oi"
        ])
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except:
        return None

# ═══════════════════════════════════════════════════════
#   VOLUME BUILDUP CHECKER
# ═══════════════════════════════════════════════════════
def check_volume_buildup(df, direction="BUY"):
    """
    Checks if volume is increasing in the direction of the trade.
    BUY  → green candle volumes increasing over last 3 candles
    SELL → red candle volumes increasing over last 3 candles
    Returns score 0-100
    """
    if len(df) < 4:
        return 0

    last3 = df.iloc[-3:]
    score = 0

    if direction == "BUY":
        # Green candle volumes
        vols = [
            row["volume"] if row["close"] > row["open"] else 0
            for _, row in last3.iterrows()
        ]
    else:
        # Red candle volumes
        vols = [
            row["volume"] if row["close"] < row["open"] else 0
            for _, row in last3.iterrows()
        ]

    # Check if volumes are increasing
    if vols[0] > 0 and vols[1] > vols[0]:
        score += 35
    if vols[1] > 0 and vols[2] > vols[1]:
        score += 35
    if vols[2] > df["volume"].mean():
        score += 30

    return min(100, score)

# ═══════════════════════════════════════════════════════
#   PATTERN BUILDUP DETECTORS
#   Each returns (signal, score, description)
#   Score = how much of the pattern has formed (0-100)
# ═══════════════════════════════════════════════════════

def detect_bull_engulfing_buildup(df):
    """
    Bullish Engulfing forming:
    Need: bearish candle then current candle opening lower
    and price starting to push up strongly
    """
    if len(df) < 3:
        return None

    prev  = df.iloc[-2]
    curr  = df.iloc[-1]

    # Previous candle must be bearish
    if prev["close"] >= prev["open"]:
        return None

    prev_body = abs(prev["close"] - prev["open"])

    # Current candle opened below or near prev close
    opened_below = curr["open"] <= prev["close"] * 1.002

    # Current candle is bullish
    is_bullish = curr["close"] > curr["open"]

    if not opened_below or not is_bullish:
        return None

    curr_body  = abs(curr["close"] - curr["open"])

    # Score based on how much current body covers prev body
    coverage = curr_body / prev_body if prev_body > 0 else 0
    score    = min(100, int(coverage * 80))

    # Bonus if already covering more than 50% of prev body
    if curr["close"] > (prev["open"] + prev["close"]) / 2:
        score = min(100, score + 20)

    if score >= 50:
        return ("BUY", score, "Bullish Engulfing forming")
    return None

def detect_bear_engulfing_buildup(df):
    """Bearish Engulfing forming."""
    if len(df) < 3:
        return None

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    if prev["close"] <= prev["open"]:
        return None

    prev_body  = abs(prev["close"] - prev["open"])
    opened_above = curr["open"] >= prev["close"] * 0.998
    is_bearish = curr["close"] < curr["open"]

    if not opened_above or not is_bearish:
        return None

    curr_body = abs(curr["close"] - curr["open"])
    coverage  = curr_body / prev_body if prev_body > 0 else 0
    score     = min(100, int(coverage * 80))

    if curr["close"] < (prev["open"] + prev["close"]) / 2:
        score = min(100, score + 20)

    if score >= 50:
        return ("SELL", score, "Bearish Engulfing forming")
    return None

def detect_hammer_buildup(df):
    """
    Hammer forming:
    Long lower shadow building up
    Price bouncing from low
    """
    if len(df) < 2:
        return None

    curr = df.iloc[-1]
    rng  = curr["high"] - curr["low"]
    if rng == 0:
        return None

    body         = abs(curr["close"] - curr["open"])
    lower_shadow = min(curr["open"], curr["close"]) - curr["low"]
    upper_shadow = curr["high"] - max(curr["open"], curr["close"])

    # Long lower shadow forming
    shadow_ratio = lower_shadow / rng if rng > 0 else 0

    score = 0
    if shadow_ratio > 0.4:
        score += 40
    if shadow_ratio > 0.55:
        score += 20
    if curr["close"] > curr["open"]:  # closing bullish
        score += 25
    if upper_shadow < lower_shadow * 0.3:  # small upper shadow
        score += 15

    if score >= 55:
        return ("BUY", score, "Hammer forming — price bouncing from low")
    return None

def detect_shooting_star_buildup(df):
    """Shooting Star forming — long upper shadow building."""
    if len(df) < 2:
        return None

    curr = df.iloc[-1]
    rng  = curr["high"] - curr["low"]
    if rng == 0:
        return None

    upper_shadow = curr["high"] - max(curr["open"], curr["close"])
    lower_shadow = min(curr["open"], curr["close"]) - curr["low"]
    shadow_ratio = upper_shadow / rng if rng > 0 else 0

    score = 0
    if shadow_ratio > 0.4:
        score += 40
    if shadow_ratio > 0.55:
        score += 20
    if curr["close"] < curr["open"]:
        score += 25
    if lower_shadow < upper_shadow * 0.3:
        score += 15

    if score >= 55:
        return ("SELL", score, "Shooting Star forming — price rejecting highs")
    return None

def detect_morning_star_buildup(df):
    """
    Morning Star forming:
    After bearish candle, small indecision candle
    and current candle starting bullish recovery
    """
    if len(df) < 4:
        return None

    p2   = df.iloc[-3]   # first bearish candle
    p1   = df.iloc[-2]   # small doji/indecision
    curr = df.iloc[-1]   # recovery candle forming

    is_bear_p2 = p2["close"] < p2["open"]
    body_p2    = abs(p2["close"] - p2["open"])
    body_p1    = abs(p1["close"] - p1["open"])
    is_bull    = curr["close"] > curr["open"]

    if not is_bear_p2:
        return None

    score = 0

    # P1 should be small (indecision)
    if body_p2 > 0 and body_p1 < body_p2 * 0.4:
        score += 35

    # Current candle bullish
    if is_bull:
        score += 30

    # Price recovering above p1
    if curr["close"] > p1["high"]:
        score += 25

    # Current candle recovering into p2 body
    midpoint_p2 = (p2["open"] + p2["close"]) / 2
    if curr["close"] > midpoint_p2:
        score += 10

    if score >= 55:
        return ("BUY", score, "Morning Star forming — bullish reversal starting")
    return None

def detect_evening_star_buildup(df):
    """Evening Star forming — bearish reversal starting."""
    if len(df) < 4:
        return None

    p2   = df.iloc[-3]
    p1   = df.iloc[-2]
    curr = df.iloc[-1]

    is_bull_p2 = p2["close"] > p2["open"]
    body_p2    = abs(p2["close"] - p2["open"])
    body_p1    = abs(p1["close"] - p1["open"])
    is_bear    = curr["close"] < curr["open"]

    if not is_bull_p2:
        return None

    score = 0
    if body_p2 > 0 and body_p1 < body_p2 * 0.4:
        score += 35
    if is_bear:
        score += 30
    if curr["close"] < p1["low"]:
        score += 25
    midpoint_p2 = (p2["open"] + p2["close"]) / 2
    if curr["close"] < midpoint_p2:
        score += 10

    if score >= 55:
        return ("SELL", score, "Evening Star forming — bearish reversal starting")
    return None

def detect_double_bottom_buildup(df):
    """
    Double Bottom forming:
    Price hit a low, bounced, came back near same low
    and now starting to bounce again
    """
    if len(df) < 15:
        return None

    lows   = df["low"].values
    closes = df["close"].values

    # Find the two lowest points in recent data
    recent_lows = []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            recent_lows.append((i, lows[i]))

    if len(recent_lows) < 2:
        return None

    t1, t2 = recent_lows[-2], recent_lows[-1]

    # Two bottoms at similar levels (within 2%)
    if t1[1] == 0:
        return None
    diff = abs(t1[1] - t2[1]) / t1[1]

    if diff > 0.025:
        return None

    # Current price above t2 and rising
    curr_price = closes[-1]
    if curr_price <= t2[1]:
        return None

    # Score based on how strong the bounce is
    bounce_pct = (curr_price - t2[1]) / t2[1] * 100
    score      = min(100, 55 + int(bounce_pct * 20))

    if score >= 58:
        return ("BUY", score, f"Double Bottom forming — bouncing from Rs.{t2[1]:.2f}")
    return None

def detect_double_top_buildup(df):
    """Double Top forming — price rejecting same high twice."""
    if len(df) < 15:
        return None

    highs  = df["high"].values
    closes = df["close"].values

    recent_highs = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            recent_highs.append((i, highs[i]))

    if len(recent_highs) < 2:
        return None

    h1, h2 = recent_highs[-2], recent_highs[-1]

    if h1[1] == 0:
        return None
    diff = abs(h1[1] - h2[1]) / h1[1]

    if diff > 0.025:
        return None

    curr_price = closes[-1]
    if curr_price >= h2[1]:
        return None

    drop_pct = (h2[1] - curr_price) / h2[1] * 100
    score    = min(100, 55 + int(drop_pct * 20))

    if score >= 58:
        return ("SELL", score, f"Double Top forming — rejected from Rs.{h2[1]:.2f}")
    return None

def detect_bull_flag_buildup(df):
    """
    Bull Flag forming:
    Strong up move followed by tight consolidation
    Volume contracting during consolidation
    """
    if len(df) < 12:
        return None

    pole       = df.iloc[-12:-6]   # the flagpole
    flag       = df.iloc[-6:]      # the flag (consolidation)

    pole_move  = pole["close"].iloc[-1] - pole["close"].iloc[0]
    flag_range = flag["high"].max() - flag["low"].min()

    if pole_move <= 0:
        return None

    # Flag should be small compared to pole
    ratio = flag_range / pole_move if pole_move > 0 else 999

    score = 0
    if ratio < 0.4:
        score += 40
    if ratio < 0.25:
        score += 20

    # Volume should contract during flag
    pole_vol = pole["volume"].mean()
    flag_vol = flag["volume"].mean()
    if pole_vol > 0 and flag_vol < pole_vol * 0.8:
        score += 25

    # Current price near top of flag
    flag_top = flag["high"].max()
    curr     = float(df["close"].iloc[-1])
    if curr > flag["close"].mean():
        score += 15

    if score >= 60:
        return ("BUY", score, "Bull Flag forming — consolidation after strong move")
    return None

def detect_bear_flag_buildup(df):
    """Bear Flag forming — consolidation after strong down move."""
    if len(df) < 12:
        return None

    pole      = df.iloc[-12:-6]
    flag      = df.iloc[-6:]

    pole_move = pole["close"].iloc[0] - pole["close"].iloc[-1]
    flag_range = flag["high"].max() - flag["low"].min()

    if pole_move <= 0:
        return None

    ratio = flag_range / pole_move if pole_move > 0 else 999
    score = 0

    if ratio < 0.4:
        score += 40
    if ratio < 0.25:
        score += 20

    pole_vol = pole["volume"].mean()
    flag_vol = flag["volume"].mean()
    if pole_vol > 0 and flag_vol < pole_vol * 0.8:
        score += 25

    curr = float(df["close"].iloc[-1])
    if curr < flag["close"].mean():
        score += 15

    if score >= 60:
        return ("SELL", score, "Bear Flag forming — consolidation after strong drop")
    return None

def detect_ascending_triangle_buildup(df):
    """
    Ascending Triangle forming:
    Flat resistance at top
    Higher lows being made
    """
    if len(df) < 15:
        return None

    recent = df.iloc[-15:]
    highs  = recent["high"].values
    lows   = recent["low"].values

    # Check if highs are flat (resistance)
    high_std = np.std(highs[-5:]) / np.mean(highs[-5:]) if np.mean(highs[-5:]) > 0 else 1
    flat_top = high_std < 0.008

    # Check if lows are rising
    low_slope = np.polyfit(range(len(lows[-8:])), lows[-8:], 1)[0]
    rising_lows = low_slope > 0

    if not flat_top or not rising_lows:
        return None

    score = 60
    if high_std < 0.005:
        score += 15
    if low_slope > np.mean(lows[-8:]) * 0.001:
        score += 15

    curr = float(df["close"].iloc[-1])
    resistance = np.mean(highs[-5:])
    if curr > resistance * 0.995:
        score += 10

    if score >= 60:
        return ("BUY", score, f"Ascending Triangle forming — resistance at Rs.{resistance:.2f}")
    return None

# ═══════════════════════════════════════════════════════
#   MASTER PATTERN SCANNER
# ═══════════════════════════════════════════════════════
def scan_patterns(df, symbol):
    """
    Runs all buildup detectors on current candle data.
    Combines price action score with volume buildup score.
    Returns best signal with final confidence.
    """
    detectors = [
        detect_bull_engulfing_buildup,
        detect_bear_engulfing_buildup,
        detect_hammer_buildup,
        detect_shooting_star_buildup,
        detect_morning_star_buildup,
        detect_evening_star_buildup,
        detect_double_bottom_buildup,
        detect_double_top_buildup,
        detect_bull_flag_buildup,
        detect_bear_flag_buildup,
        detect_ascending_triangle_buildup,
    ]

    buy_signals  = []
    sell_signals = []

    for detector in detectors:
        try:
            result = detector(df)
            if result:
                signal, pattern_score, desc = result

                # Get volume confirmation
                vol_score = check_volume_buildup(df, signal)

                # Combined score: 70% pattern + 30% volume
                final_score = int(pattern_score * 0.7 + vol_score * 0.3)

                if signal == "BUY":
                    buy_signals.append((desc, final_score, pattern_score, vol_score))
                else:
                    sell_signals.append((desc, final_score, pattern_score, vol_score))
        except:
            continue

    # Pick the strongest signal
    if buy_signals and (not sell_signals or
       max(s[1] for s in buy_signals) >= max(s[1] for s in sell_signals)):
        best     = max(buy_signals, key=lambda x: x[1])
        desc, final_score, pat_score, vol_score = best
        return "BUY", final_score, desc, pat_score, vol_score

    elif sell_signals:
        best     = max(sell_signals, key=lambda x: x[1])
        desc, final_score, pat_score, vol_score = best
        return "SELL", final_score, desc, pat_score, vol_score

    return "NONE", 0, "", 0, 0

# ─── POSITION SIZING ──────────────────────────────────
def calculate_position(price, atr):
    sl_pts  = round(atr * 0.5, 2) if atr > 0 else 8
    tgt_pts = round(atr * 1.5, 2) if atr > 0 else 24
    risk    = account["balance"] * RISK_PCT
    qty     = max(1, int(risk / sl_pts)) if sl_pts > 0 else 1
    return qty, sl_pts, tgt_pts

# ─── PLACE ORDER ──────────────────────────────────────
def place_order(symbol, token, qty, side):
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
        return r.data.order_id
    except Exception as e:
        log(f"  Order failed: {e}")
        return None

# ─── ALERTS ───────────────────────────────────────────
def success_alert(symbol, pnl):
    print("\033[1m" + "=" * 50 + "\033[0m")
    print("\033[1m" + f"  TARGET HIT — {symbol}" + "\033[0m")
    print("\033[1m" + f"  Profit  : Rs.{pnl:.0f}" + "\033[0m")
    print("\033[1m" + f"  Balance : Rs.{account['balance']:,.0f}" + "\033[0m")
    print("\033[1m" + "=" * 50 + "\033[0m")
    try:
        from plyer import notification
        notification.notify(title=f"TARGET HIT — {symbol}",
                            message=f"Profit: Rs.{pnl:.0f}", timeout=10)
    except:
        pass

def sl_alert(symbol, pnl):
    print(f"\n  SL HIT — {symbol} | Loss: Rs.{abs(pnl):.0f} | Balance: Rs.{account['balance']:,.0f}")
    try:
        from plyer import notification
        notification.notify(title=f"SL HIT — {symbol}",
                            message=f"Loss: Rs.{abs(pnl):.0f}", timeout=10)
    except:
        pass

# ─── MONITOR POSITIONS ────────────────────────────────
def monitor_positions():
    for symbol, pos in list(open_positions.items()):
        try:
            quote = market_api.get_full_market_quote(
                WATCHLIST[symbol], "2.0"
            )
            key   = list(quote.data.keys())[0]
            price = quote.data[key].last_price
        except:
            continue

        if price <= pos["sl"]:
            pnl = -pos["sl_pts"] * pos["qty"]
            account["balance"]      += pnl
            account["daily_pnl"]    += pnl
            account["total_pnl"]    += pnl
            account["trades_today"] += 1
            account["losses_today"] += 1
            sl_alert(symbol, pnl)
            log(f"  SL HIT — {symbol} @ Rs.{price:.2f} | Loss: Rs.{abs(pnl):.0f}")
            place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
            closed_trades.append({
                "symbol" : symbol, "entry": pos["entry"],
                "exit"   : price,  "pnl": pnl,
                "result" : "SL",   "time": datetime.now().strftime("%H:%M")
            })
            del open_positions[symbol]
            save_account()

        elif price >= pos["target"]:
            pnl = pos["tgt_pts"] * pos["qty"]
            account["balance"]      += pnl
            account["daily_pnl"]    += pnl
            account["total_pnl"]    += pnl
            account["trades_today"] += 1
            account["wins_today"]   += 1
            success_alert(symbol, pnl)
            log(f"  TARGET HIT — {symbol} @ Rs.{price:.2f} | Profit: Rs.{pnl:.0f}")
            place_order(symbol, WATCHLIST[symbol], pos["qty"], "SELL")
            closed_trades.append({
                "symbol" : symbol, "entry": pos["entry"],
                "exit"   : price,  "pnl": pnl,
                "result" : "TARGET", "time": datetime.now().strftime("%H:%M")
            })
            del open_positions[symbol]
            save_account()

# ─── PRINT ACCOUNT ────────────────────────────────────
def print_account():
    total = account["trades_today"]
    wins  = account["wins_today"]
    wr    = round(wins / total * 100, 1) if total > 0 else 0
    log(f"\n{'─'*50}")
    log(f"  ACCOUNT — {datetime.now().strftime('%d %b %Y %H:%M')}")
    log(f"{'─'*50}")
    log(f"  Balance     : Rs.{account['balance']:,.0f}")
    log(f"  Daily P&L   : Rs.{account['daily_pnl']:+,.0f}")
    log(f"  Total P&L   : Rs.{account['total_pnl']:+,.0f}")
    log(f"  Trades today: {total} (W:{wins} L:{account['losses_today']})")
    log(f"  Win rate    : {wr}%")
    log(f"  Open trades : {len(open_positions)}")
    log(f"{'─'*50}")

# ─── MAIN SCAN ────────────────────────────────────────
def run_scan():
    now = datetime.now().strftime("%H:%M")

    if now < "09:15" or now > "15:25":
        log(f"  Market closed at {now}")
        return

    if account["daily_pnl"] <= -MAX_DAILY_LOSS:
        log(f"  KILL SWITCH — loss Rs.{abs(account['daily_pnl']):.0f}")
        return

    if now >= "15:25":
        log("  3:25pm — squaring off all positions")
        for symbol in list(open_positions.keys()):
            place_order(symbol, WATCHLIST[symbol],
                       open_positions[symbol]["qty"], "SELL")
            del open_positions[symbol]
        save_account()
        print_account()
        return

    log(f"\n{'='*50}")
    log(f"  PATTERN SCAN at {now} | Balance: Rs.{account['balance']:,.0f}")
    log(f"  Open: {len(open_positions)} | Daily P&L: Rs.{account['daily_pnl']:+,.0f}")
    log(f"{'='*50}")

    if open_positions:
        monitor_positions()

    signals_found = 0

    for symbol, token in WATCHLIST.items():

        if symbol in open_positions:
            continue

        if len(open_positions) >= MAX_POSITIONS:
            log(f"  Max {MAX_POSITIONS} positions reached")
            break

        # Fetch latest candles
        df = fetch_candles(token, days=3)
        if df is None or len(df) < 10:
            time.sleep(0.1)
            continue

        # Scan for forming patterns
        signal, confidence, desc, pat_score, vol_score = scan_patterns(df, symbol)

        # Skip if below minimum confidence
        if confidence < MIN_CONFIDENCE or signal == "NONE":
            time.sleep(0.1)
            continue

        price = float(df["close"].iloc[-1])
        atr   = float(df["high"].iloc[-1]) - float(df["low"].iloc[-1])
        qty, sl_pts, tgt_pts = calculate_position(price, atr)

        log(f"\n  {symbol} — Rs.{price:.2f}")
        log(f"  Pattern    : {desc}")
        log(f"  Confidence : {confidence}% (Pattern:{pat_score}% Vol:{vol_score}%)")
        log(f"  Signal     : {signal}")
        log(f"  Entry: Rs.{price:.2f} | SL: Rs.{round(price-sl_pts,2)} | TGT: Rs.{round(price+tgt_pts,2)} | Qty: {qty}")
        log(f"  Chart: https://in.tradingview.com/chart/?symbol=NSE%3A{symbol}")

        if signal == "BUY":
            order_id = place_order(symbol, token, qty, "BUY")
            if order_id:
                open_positions[symbol] = {
                    "entry"      : price,
                    "sl"         : round(price - sl_pts,  2),
                    "target"     : round(price + tgt_pts, 2),
                    "sl_pts"     : sl_pts,
                    "tgt_pts"    : tgt_pts,
                    "qty"        : qty,
                    "pattern"    : desc,
                    "confidence" : confidence
                }
                save_account()
                signals_found += 1

        elif signal == "SELL":
            log(f"  SELL pattern detected — logging only (long only mode)")

        time.sleep(0.3)

    log(f"\n  Patterns found: {signals_found}")
    print_account()

# ─── START ────────────────────────────────────────────
if __name__ == "__main__":
    load_account()

    log("=" * 50)
    log("  PATTERN ANTICIPATION BOT")
    log(f"  Balance      : Rs.{account['balance']:,.0f}")
    log(f"  Mode         : {'PAPER TRADING' if PAPER_TRADE else 'LIVE TRADING'}")
    log(f"  Min confidence: {MIN_CONFIDENCE}%")
    log(f"  Entry style  : ANTICIPATION (before pattern completes)")
    log(f"  Confirmation : Price action + Volume buildup")
    log(f"  Max positions: {MAX_POSITIONS}")
    log(f"  Patterns     : 11 buildup detectors")
    log(f"  Stocks       : {len(WATCHLIST)} Nifty 100")
    log("=" * 50)

    run_scan()
    schedule.every(5).minutes.do(run_scan)

    log("\n  Bot running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        if open_positions:
            monitor_positions()
        time.sleep(30)