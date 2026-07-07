# check_signals.py
import json

with open("signal_log.json") as f:
    lines = f.readlines()

print(f"Total data points saved: {len(lines)}")
print()

# Show last 10 entries
for line in lines[-10:]:
    d = json.loads(line)
    print(f"{d['symbol']:<12} price={d['price']} vwap={d['vwap']} rsi={d['rsi']:.1f} dom={d['dom_ratio']:.2f} above_vwap={d['above_vwap']}")