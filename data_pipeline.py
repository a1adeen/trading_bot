# data_pipeline.py
# Fetches NSE stock data using Upstox API
# Computes EMA, RSI, ATR indicators
# Stores everything in local SQLite database

import upstox_client
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from config import ACCESS_TOKEN

# ─── CONFIG ───────────────────────────────────────────
DB_PATH = "market_data.db"

# NSE stock tokens — add/remove stocks here
WATCHLIST = {
    "RELIANCE"  : "NSE_EQ|INE002A01018",
    "TCS"       : "NSE_EQ|INE467B01029",
    "INFY"      : "NSE_EQ|INE009A01021",
    "HDFCBANK"  : "NSE_EQ|INE040A01034",
    "ICICIBANK" : "NSE_EQ|INE090A01021"
}

# ─── CONNECT TO UPSTOX ────────────────────────────────
configuration = upstox_client.Configuration()
configuration.access_token = ACCESS_TOKEN
api_client = upstox_client.ApiClient(configuration)
history_api = upstox_client.HistoryApi(api_client)

# ─── FETCH DATA ───────────────────────────────────────
def fetch_ohlcv(symbol, token, days=365):
    """Fetch historical daily candles from Upstox."""
    to_date   = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"  Fetching {symbol}...")

    try:
        data = history_api.get_historical_candle_data1(
            token,
            "30minute",
            to_date,
            from_date,
            "2.0"
        )

        candles = data.data.candles
        df = pd.DataFrame(candles, columns=[
            "date", "open", "high", "low", "close", "volume", "oi"
        ])

        df["date"]   = pd.to_datetime(df["date"])
        df["symbol"] = symbol
        df = df.sort_values("date").reset_index(drop=True)

        print(f"  ✅ {symbol} — {len(df)} candles fetched")
        return df

    except Exception as e:
        print(f"  ❌ {symbol} failed: {e}")
        return None

# ─── COMPUTE INDICATORS ───────────────────────────────
def add_indicators(df):
    """Add EMA, RSI, ATR indicators manually using pandas."""

    # EMA 20 and EMA 50
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()

    # RSI 14
    delta     = df["close"].diff()
    gain      = delta.clip(lower=0)
    loss      = -delta.clip(upper=0)
    avg_gain  = gain.ewm(com=13, adjust=False).mean()
    avg_loss  = loss.ewm(com=13, adjust=False).mean()
    rs        = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR 14
    df["tr"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = df["tr"].ewm(com=13, adjust=False).mean()

    # Volume moving average 20
    df["volume_ma"] = df["volume"].rolling(20).mean()

    # Drop temp column
    df = df.drop(columns=["tr"])

    return df

# ─── SAVE TO DATABASE ─────────────────────────────────
def save_to_db(df, table="ohlcv"):
    """Save dataframe to SQLite database."""
    conn = sqlite3.connect(DB_PATH)

    # Remove existing data for this symbol then insert fresh
    conn.execute(
        f"DELETE FROM {table} WHERE symbol=?",
        (df["symbol"].iloc[0],)
    ) if table in [
        row[0] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ] else None

    df.to_sql(table, conn, if_exists="append", index=False)
    conn.close()

# ─── LOAD FROM DATABASE ───────────────────────────────
def load_from_db(symbol, table="ohlcv"):
    """Load symbol data from SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    df   = pd.read_sql(
        f"SELECT * FROM {table} WHERE symbol=? ORDER BY date",
        conn,
        params=(symbol,)
    )
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df

# ─── MAIN RUN ─────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 45)
    print("   DATA PIPELINE STARTING")
    print("=" * 45)

    for symbol, token in WATCHLIST.items():
        # Fetch raw data
        df = fetch_ohlcv(symbol, token, days=60)
        if df is None:
            continue

        # Add indicators
        df = add_indicators(df)

        # Save to database
        save_to_db(df)
        print(f"  💾 {symbol} saved to database")

    print("\n" + "=" * 45)
    print("   PIPELINE COMPLETE — VERIFYING DATA")
    print("=" * 45)

    # Verify — print last 5 rows of RELIANCE
    df_check = load_from_db("RELIANCE")
    print(f"\nRELIANCE — last 5 rows:")
    print(df_check[["date","close","ema_20","ema_50","rsi","atr"]].tail(5).to_string(index=False))
    print(f"\nTotal candles in DB : {len(df_check)}")
    print(f"Date range         : {df_check['date'].iloc[0].date()} to {df_check['date'].iloc[-1].date()}")
    print("\n✅ Data pipeline working perfectly!")