# server.py – Flask API for HedgePilot dashboard (no separate portfolio file required)
from flask import Flask, request, jsonify
from flask_cors import CORS

# ---------- 1. Import your actual strategy ----------
# Replace the import below with your real function, e.g.:
#   from strategy import evaluate_symbol
# If your file/function name differs, adjust accordingly.
try:
    from strategy import evaluate_symbol
except ImportError:
    # Fallback for when the real strategy module is not yet connected.
    def evaluate_symbol(symbol):
        return {
            "ticker": symbol,
            "company": f"{symbol} Inc.",
            "satisfies": True,
            "signal": "BUY",
            "reason": "fallback – connect your strategy.py"
        }

# ---------- 2. Built‑in portfolio (no file) ----------
# You can replace this list with a database query or any dynamic source later.
SAMPLE_PORTFOLIO = [
    {"ticker": "AAPL", "action": "BUY", "shares": 40, "price": 187.20, "ai_confidence": "high"},
    {"ticker": "NVDA", "action": "BUY", "shares": 15, "price": 124.50, "ai_confidence": "high"},
    {"ticker": "TSLA", "action": "SELL", "shares": 20, "price": 212.80, "ai_confidence": "medium"},
]

# ---------- 3. Flask app ----------
app = Flask(__name__)
CORS(app)  # Allows the dashboard HTML to call this API

@app.route('/api/check', methods=['GET'])
def check_strategy():
    symbol = request.args.get('symbol', '').strip().upper()
    if not symbol:
        return jsonify({"error": "Symbol parameter is required"}), 400
    try:
        result = evaluate_symbol(symbol)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Strategy evaluation failed: {str(e)}"}), 500

@app.route('/api/portfolio', methods=['GET'])
def portfolio():
    # Return the current portfolio (static for now)
    return jsonify({"portfolio": SAMPLE_PORTFOLIO})

if __name__ == '__main__':
    print("🚀 HedgePilot API running at http://localhost:5000")
    app.run(debug=True, port=5000)