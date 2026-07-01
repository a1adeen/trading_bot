# backtester.py
# VWAP + RSI + Volume Buildup strategy
# Dynamic SL = 1x ATR | Target = 2x ATR | R:R 1:2

import pandas as pd
from data_pipeline import load_from_db

# ─── CONFIG ───────────────────────────────────────────
INITIAL_CAPITAL = 50000
RISK_PCT        = 0.01
WATCHLIST       = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]

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

# ─── ADD ATR ──────────────────────────────────────────
def add_atr(df, period=14):
    df["tr"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = df["tr"].ewm(com=period - 1, adjust=False).mean()
    return df

# ─── SIGNAL CHECK ─────────────────────────────────────
def check_signal(df, i):
    if i < 15:
        return "NONE"

    price = df["close"].iloc[i]
    vwap  = df["vwap"].iloc[i]
    rsi   = df["rsi"].iloc[i]

    # RSI crossover in last 3 candles
    rsi_cross_up = any(
        df["rsi"].iloc[j-1] < 50 and df["rsi"].iloc[j] >= 50
        for j in range(max(1, i-2), i+1)
    )
    rsi_cross_down = any(
        df["rsi"].iloc[j-1] > 50 and df["rsi"].iloc[j] <= 50
        for j in range(max(1, i-2), i+1)
    )

    # Volume — at least above average (relaxed condition)
    avg_vol      = df["volume"].iloc[max(0,i-10):i].mean()
    vol_above_avg = df["volume"].iloc[i] > avg_vol * 0.8

    # BUY — price above VWAP + RSI crossed above 50 + decent volume
    if price > vwap and rsi_cross_up and rsi > 50 and vol_above_avg:
        return "BUY"

    # SELL — price below VWAP + RSI crossed below 50 + decent volume
    elif price < vwap and rsi_cross_down and rsi < 50 and vol_above_avg:
        return "SELL"

    return "NONE"

# ─── BACKTEST ONE STOCK ───────────────────────────────
def backtest_stock(symbol, capital):

    # Step 1 — load data
    df = load_from_db(symbol)
    if df is None or len(df) < 20:
        print(f"  ❌ {symbol} — not enough data")
        return [], capital

    # Step 2 — add all indicators
    df = add_vwap(df)
    df = add_rsi(df)
    df = add_atr(df)

    # Step 3 — debug first candle
    sample = df.iloc[20]
    print(f"  Sample candle — price:{sample['close']:.1f} "
          f"vwap:{sample['vwap']:.1f} "
          f"rsi:{sample['rsi']:.1f} "
          f"atr:{sample['atr']:.2f}")

    # Step 4 — count how many BUY setups exist
    buy_setups = sum(
        1 for i in range(15, len(df))
        if df["close"].iloc[i] > df["vwap"].iloc[i]
        and df["rsi"].iloc[i] > 50
    )
    print(f"  Potential setups: {buy_setups} out of {len(df)} candles")

    # Step 5 — run simulation
    trades   = []
    position = None

    for i in range(15, len(df)):
        row    = df.iloc[i]
        price  = row["close"]
        atr    = row["atr"]
        signal = check_signal(df, i)

        # SL and Target based on ATR
        sl_pts  = round(atr * 1.0, 2)
        tgt_pts = round(atr * 2.0, 2)

        # ── Manage open position ──
        if position:

            # Stop loss hit
            if price <= position["sl"]:
                pnl = -position["sl_pts"] * position["qty"]
                capital += pnl
                trades.append({
                    "symbol"     : symbol,
                    "entry_date" : position["entry_date"],
                    "exit_date"  : row["date"],
                    "entry"      : position["entry"],
                    "exit"       : price,
                    "sl"         : position["sl"],
                    "target"     : position["target"],
                    "qty"        : position["qty"],
                    "pnl"        : round(pnl, 2),
                    "result"     : "SL HIT"
                })
                position = None

            # Target hit
            elif price >= position["target"]:
                pnl = position["tgt_pts"] * position["qty"]
                capital += pnl
                trades.append({
                    "symbol"     : symbol,
                    "entry_date" : position["entry_date"],
                    "exit_date"  : row["date"],
                    "entry"      : position["entry"],
                    "exit"       : price,
                    "sl"         : position["sl"],
                    "target"     : position["target"],
                    "qty"        : position["qty"],
                    "pnl"        : round(pnl, 2),
                    "result"     : "TARGET HIT"
                })
                position = None

            # Manual exit on sell signal
            elif signal == "SELL":
                pnl = (price - position["entry"]) * position["qty"]
                capital += pnl
                trades.append({
                    "symbol"     : symbol,
                    "entry_date" : position["entry_date"],
                    "exit_date"  : row["date"],
                    "entry"      : position["entry"],
                    "exit"       : price,
                    "sl"         : position["sl"],
                    "target"     : position["target"],
                    "qty"        : position["qty"],
                    "pnl"        : round(pnl, 2),
                    "result"     : "MANUAL EXIT"
                })
                position = None

        # ── Open new trade on BUY signal ──
        if signal == "BUY" and position is None and sl_pts > 0:
            risk_amount = capital * RISK_PCT
            qty         = max(1, int(risk_amount / sl_pts))
            if qty > 0 and capital > qty * price:
                position = {
                    "entry_date" : row["date"],
                    "entry"      : price,
                    "sl"         : round(price - sl_pts,  2),
                    "target"     : round(price + tgt_pts, 2),
                    "sl_pts"     : sl_pts,
                    "tgt_pts"    : tgt_pts,
                    "qty"        : qty
                }

    return trades, capital

# ─── PERFORMANCE REPORT ───────────────────────────────
def print_report(all_trades, final_capital):
    if not all_trades:
        print("\n  ❌ No trades generated across all stocks.")
        print("  Reason: Strategy conditions not met in this period.")
        return

    df           = pd.DataFrame(all_trades)
    total_trades = len(df)
    winners      = df[df["pnl"] > 0]
    losers       = df[df["pnl"] <= 0]
    win_rate     = len(winners) / total_trades * 100
    total_pnl    = df["pnl"].sum()
    avg_win      = winners["pnl"].mean() if len(winners) > 0 else 0
    avg_loss     = losers["pnl"].mean()  if len(losers)  > 0 else 0
    target_hits  = len(df[df["result"] == "TARGET HIT"])
    sl_hits      = len(df[df["result"] == "SL HIT"])
    cumulative   = df["pnl"].cumsum()
    max_dd       = (cumulative - cumulative.cummax()).min()

    print(f"\n{'='*50}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*50}")
    print(f"  Initial Capital : ₹{INITIAL_CAPITAL:,.0f}")
    print(f"  Final Capital   : ₹{final_capital:,.0f}")
    print(f"  Total P&L       : ₹{total_pnl:,.0f}")
    print(f"  Return          : {(total_pnl/INITIAL_CAPITAL)*100:.2f}%")
    print(f"{'─'*50}")
    print(f"  Total Trades    : {total_trades}")
    print(f"  Target Hits     : {target_hits} ✅")
    print(f"  SL Hits         : {sl_hits} ❌")
    print(f"  Win Rate        : {win_rate:.1f}%")
    print(f"  Avg Win         : ₹{avg_win:,.0f}")
    print(f"  Avg Loss        : ₹{avg_loss:,.0f}")
    print(f"  Max Drawdown    : ₹{max_dd:,.0f}")
    print(f"{'─'*50}")
    print(f"\n  PER STOCK BREAKDOWN:")
    print(f"  {'Symbol':<12}{'Trades':>7}{'Wins':>6}{'Win%':>7}{'P&L':>10}")
    print(f"  {'─'*44}")

    for symbol in WATCHLIST:
        s = df[df["symbol"] == symbol]
        if len(s) == 0:
            continue
        w   = len(s[s["pnl"] > 0])
        wr  = w / len(s) * 100
        pnl = s["pnl"].sum()
        print(f"  {symbol:<12}{len(s):>7}{w:>6}{wr:>6.1f}%  ₹{pnl:>8,.0f}")

    print(f"{'='*50}")
    df.to_csv("backtest_results.csv", index=False)
    print(f"\n  📄 Saved to backtest_results.csv")

# ─── MAIN ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  BACKTESTER — VWAP + RSI + VOLUME")
    print(f"  Capital   : ₹{INITIAL_CAPITAL:,}")
    print(f"  SL        : 1x ATR (dynamic)")
    print(f"  Target    : 2x ATR (dynamic)")
    print(f"  Risk/trade: {RISK_PCT*100}% of capital")
    print("=" * 50)

    all_trades = []
    capital    = INITIAL_CAPITAL

    for symbol in WATCHLIST:
        print(f"\n  Running {symbol}...")
        trades, capital = backtest_stock(symbol, capital)
        if trades:
            wins = len([t for t in trades if t["pnl"] > 0])
            pnl  = sum(t["pnl"] for t in trades)
            print(f"  ✅ {symbol} — {len(trades)} trades | "
                  f"{wins} wins | ₹{pnl:,.0f} P&L")
            all_trades.extend(trades)
        else:
            print(f"  ⚠️  {symbol} — no trades found")

    print_report(all_trades, capital)