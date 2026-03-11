"""
WebCode Terminal — Ultimate Mega Backend
Flask + Gunicorn + Real-Time Fundamentals + Kite/Paper Trading
"""

import os, random, time, threading
from datetime import datetime
from flask import Flask, jsonify, request, render_template
import yfinance as yf

app = Flask(__name__)

# ── KiteConnect (optional) ──
try:
    from kiteconnect import KiteConnect
    _KITE_LIB = True
except ImportError:
    _KITE_LIB = False

API_KEY      = os.environ.get("KITE_API_KEY", "")
API_SECRET   = os.environ.get("KITE_API_SECRET", "")
ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "")

kite = None
if _KITE_LIB and API_KEY and ACCESS_TOKEN:
    try:
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(ACCESS_TOKEN)
        print("KiteConnect: LIVE mode")
    except Exception as e:
        print(f"Kite init error: {e}")
else:
    print("KiteConnect: PAPER (simulated) mode")

# ── Market Data Engine ──
sim_state = {
    "balance": 100000.0, "invested": 0.0, "realized": 0.0,
    "positions": [], "orders": [], "order_counter": 1,
}

# Base prices (will be updated by yfinance in background)
SIM_PRICES = {
    "RELIANCE": 2920.0, "TCS": 3950.0, "INFY": 1580.0,
    "HDFC": 1620.0, "ICICIBANK": 1250.0, "SBIN": 825.0,
    "WIPRO": 590.0, "NIFTY": 24800.0, "BANKNIFTY": 53200.0,
    "SENSEX": 81500.0, "VIX": 14.2, "GOLD": 71500.0
}

def sync_real_prices():
    """Fetches real base prices from Yahoo Finance on boot"""
    mapping = {"RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS", "INFY": "INFY.NS", 
               "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
    for sym, yf_sym in mapping.items():
        try:
            tkr = yf.Ticker(yf_sym)
            hist = tkr.history(period="1d")
            if not hist.empty:
                SIM_PRICES[sym] = round(hist['Close'].iloc[-1], 2)
        except:
            pass

def _sim_tick():
    sync_real_prices() # Initial real-data sync
    while True:
        # Simulate live tick fluctuations around the base price
        for sym in list(SIM_PRICES.keys()):
            drift = 0.0005 if sym == "VIX" else 0.0015
            SIM_PRICES[sym] = round(SIM_PRICES[sym] * (1 + random.uniform(-drift, drift)), 2)
        for p in sim_state["positions"]:
            p["ltp"] = SIM_PRICES.get(p["sym"], p["ltp"])
        time.sleep(1.5) # Fast 1.5s tick rate for live feel

threading.Thread(target=_sim_tick, daemon=True).start()

def _get_unrealized():
    return round(sum((p["ltp"] - p["entryPrice"]) * p["qty"] if p["side"] == "BUY" else (p["entryPrice"] - p["ltp"]) * p["qty"] for p in sim_state["positions"]), 2)

# ── Endpoints ──
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/account")
def api_account():
    return jsonify({
        "status": "simulated", "balance": sim_state["balance"],
        "invested": sim_state["invested"], "realized": round(sim_state["realized"], 2),
        "unrealized": _get_unrealized(),
    })

@app.route("/api/positions")
def api_positions():
    return jsonify({"status": "simulated", "positions": sim_state["positions"]})

@app.route("/api/orders")
def api_orders():
    return jsonify({"status": "simulated", "orders": list(reversed(sim_state["orders"]))})

@app.route("/api/place_order", methods=["POST"])
def api_place_order():
    body = request.get_json(silent=True) or {}
    sym, side, qty, otype, price = str(body.get("symbol", "")).upper(), str(body.get("side", "BUY")).upper(), int(body.get("qty", 1)), str(body.get("type", "MARKET")).upper(), float(body.get("price", 0))

    if not sym or qty < 1: return jsonify({"status": "error", "message": "Invalid Input"}), 400

    ltp = SIM_PRICES.get(sym, price or 1000.0)
    exec_price = ltp if otype == "MARKET" else (price or ltp)
    order_id = f"SIM{sim_state['order_counter']:05d}"
    sim_state["order_counter"] += 1
    cost = exec_price * qty

    existing = next((p for p in sim_state["positions"] if p["sym"] == sym), None)
    if existing and existing["side"] != side:
        close_qty = min(qty, existing["qty"])
        pnl = ((exec_price - existing["entryPrice"]) * close_qty if existing["side"] == "BUY" else (existing["entryPrice"] - exec_price) * close_qty)
        sim_state["realized"] += pnl
        sim_state["invested"] -= existing["entryPrice"] * close_qty
        existing["qty"] -= close_qty
        if existing["qty"] <= 0: sim_state["positions"].remove(existing)
    else:
        if sim_state["balance"] - sim_state["invested"] < cost: return jsonify({"status": "error", "message": "Insufficient margin"})
        if existing:
            total_qty = existing["qty"] + qty
            existing["entryPrice"] = (existing["entryPrice"] * existing["qty"] + exec_price * qty) / total_qty
            existing["qty"] = total_qty
            existing["ltp"] = ltp
        else:
            sim_state["positions"].append({"sym": sym, "side": side, "qty": qty, "entryPrice": exec_price, "ltp": ltp})
        sim_state["invested"] += cost

    sim_state["orders"].append({"order_id": order_id, "sym": sym, "side": side, "qty": qty, "price": exec_price, "type": otype, "status": "COMPLETE", "time": datetime.now().strftime("%H:%M:%S")})
    return jsonify({"status": "success", "order_id": order_id, "exec_price": exec_price})

@app.route("/api/prices")
def api_prices():
    return jsonify({"status": "ok", "prices": dict(SIM_PRICES)})

@app.route("/api/fundamentals/<sym>")
def api_fundamentals(sym):
    try:
        yf_sym = f"{sym}.NS"
        info = yf.Ticker(yf_sym).info
        return jsonify({
            "status": "ok",
            "mcap": info.get("marketCap", "N/A"),
            "pe": info.get("trailingPE", "N/A"),
            "high52": info.get("fiftyTwoWeekHigh", "N/A"),
            "low52": info.get("fiftyTwoWeekLow", "N/A"),
            "div": info.get("dividendYield", "N/A")
        })
    except Exception as e:
        return jsonify({"status": "error", "message": "No data"})

@app.route("/health")
def health_check():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
