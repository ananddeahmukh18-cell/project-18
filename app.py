"""
Trading Dashboard — Master Backend
Flask + KiteConnect + Simulated Fallback
"""

from flask import Flask, jsonify, request, render_template
import json, time, random, threading
from datetime import datetime

app = Flask(__name__)

# ─── Try to import KiteConnect ─────────────────────────────────────────────
try:
    from kiteconnect import KiteConnect
    KITE_AVAILABLE = True
except ImportError:
    KITE_AVAILABLE = False

# ─── Config (edit these) ───────────────────────────────────────────────────
API_KEY    = "your_api_key"
API_SECRET = "your_api_secret"
ACCESS_TOKEN = ""          # Set after login

kite = None
if KITE_AVAILABLE and API_KEY != "your_api_key":
    try:
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(ACCESS_TOKEN)
    except Exception as e:
        print(f"Kite init error: {e}")

# ─── Simulated State ───────────────────────────────────────────────────────
sim_state = {
    "balance": 100000.0,
    "invested": 0.0,
    "realized": 0.0,
    "positions": [],
    "orders": [],
    "order_counter": 1,
}

SIM_PRICES = {
    "RELIANCE": 2920.0, "INFY": 1580.0, "TCS": 3950.0,
    "HDFC": 1620.0,  "ICICIBANK": 1250.0, "SBIN": 825.0,
    "NIFTY": 24800.0, "BANKNIFTY": 53200.0,
}

def sim_tick():
    """Simulate price drift every 2s"""
    while True:
        for sym in SIM_PRICES:
            SIM_PRICES[sym] *= (1 + random.uniform(-0.0015, 0.0015))
            SIM_PRICES[sym] = round(SIM_PRICES[sym], 2)
        # Update unrealized
        for p in sim_state["positions"]:
            p["ltp"] = SIM_PRICES.get(p["sym"], p["ltp"])
        time.sleep(2)

threading.Thread(target=sim_tick, daemon=True).start()

# ─── Helpers ───────────────────────────────────────────────────────────────
def get_unrealized():
    total = 0.0
    for p in sim_state["positions"]:
        if p["side"] == "BUY":
            total += (p["ltp"] - p["entryPrice"]) * p["qty"]
        else:
            total += (p["entryPrice"] - p["ltp"]) * p["qty"]
    return round(total, 2)

# ─── Routes ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/account")
def api_account():
    if kite:
        try:
            margins = kite.margins()
            equity = margins.get("equity", {})
            net = equity.get("net", 0)
            used = equity.get("utilised", {}).get("debits", 0)
            positions = kite.positions()
            realized = sum(p.get("realised", 0) for p in positions.get("day", []))
            unrealized = sum(p.get("unrealised", 0) for p in positions.get("net", []))
            return jsonify({
                "status": "ok", "balance": net,
                "invested": used, "realized": realized, "unrealized": unrealized
            })
        except Exception as e:
            print(f"Kite error: {e}")

    # Simulated
    return jsonify({
        "status": "simulated",
        "balance": sim_state["balance"],
        "invested": sim_state["invested"],
        "realized": round(sim_state["realized"], 2),
        "unrealized": get_unrealized(),
    })

@app.route("/api/positions")
def api_positions():
    if kite:
        try:
            data = kite.positions()
            positions = []
            for p in data.get("net", []):
                if p["quantity"] != 0:
                    positions.append({
                        "sym": p["tradingsymbol"],
                        "side": "BUY" if p["quantity"] > 0 else "SELL",
                        "qty": abs(p["quantity"]),
                        "entryPrice": p["average_price"],
                        "ltp": p["last_price"],
                    })
            return jsonify({"status": "ok", "positions": positions})
        except Exception as e:
            print(f"Kite error: {e}")

    return jsonify({"status": "simulated", "positions": sim_state["positions"]})

@app.route("/api/orders")
def api_orders():
    if kite:
        try:
            orders = kite.orders()
            return jsonify({"status": "ok", "orders": orders})
        except Exception as e:
            print(f"Kite error: {e}")

    return jsonify({"status": "simulated", "orders": list(reversed(sim_state["orders"]))})

@app.route("/api/place_order", methods=["POST"])
def api_place_order():
    body = request.get_json()
    sym   = body.get("symbol", "").upper()
    side  = body.get("side", "BUY").upper()
    qty   = int(body.get("qty", 1))
    otype = body.get("type", "MARKET").upper()
    price = float(body.get("price", 0))

    if kite:
        try:
            txn = kite.TRANSACTION_TYPE_BUY if side == "BUY" else kite.TRANSACTION_TYPE_SELL
            oid = kite.place_order(
                tradingsymbol=sym,
                exchange=kite.EXCHANGE_NSE,
                transaction_type=txn,
                quantity=qty,
                order_type=kite.ORDER_TYPE_MARKET if otype == "MARKET" else kite.ORDER_TYPE_LIMIT,
                price=price if otype == "LIMIT" else None,
                product=kite.PRODUCT_MIS,
                variety=kite.VARIETY_REGULAR,
            )
            return jsonify({"status": "success", "order_id": oid})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    # Simulated order execution
    ltp = SIM_PRICES.get(sym, price if price else 1000.0)
    exec_price = ltp if otype == "MARKET" else price
    order_id = f"SIM{sim_state['order_counter']:05d}"
    sim_state["order_counter"] += 1
    cost = exec_price * qty

    # Check if closing existing position
    existing = next((p for p in sim_state["positions"] if p["sym"] == sym), None)
    if existing and existing["side"] != side:
        # Close / partial close
        close_qty = min(qty, existing["qty"])
        pnl = ((exec_price - existing["entryPrice"]) * close_qty
               if existing["side"] == "BUY"
               else (existing["entryPrice"] - exec_price) * close_qty)
        sim_state["realized"] += pnl
        sim_state["invested"] -= existing["entryPrice"] * close_qty
        existing["qty"] -= close_qty
        if existing["qty"] <= 0:
            sim_state["positions"].remove(existing)
    else:
        # Open new position
        if sim_state["balance"] - sim_state["invested"] < cost:
            return jsonify({"status": "error", "message": "Insufficient margin"})
        if existing:
            # Average
            total_qty = existing["qty"] + qty
            existing["entryPrice"] = (existing["entryPrice"] * existing["qty"] + exec_price * qty) / total_qty
            existing["qty"] = total_qty
            existing["ltp"] = ltp
        else:
            sim_state["positions"].append({
                "sym": sym, "side": side, "qty": qty,
                "entryPrice": exec_price, "ltp": ltp
            })
        sim_state["invested"] += cost

    order_rec = {
        "order_id": order_id, "sym": sym, "side": side,
        "qty": qty, "price": exec_price, "type": otype,
        "status": "COMPLETE", "time": datetime.now().strftime("%H:%M:%S")
    }
    sim_state["orders"].append(order_rec)

    return jsonify({"status": "success", "order_id": order_id, "exec_price": exec_price})

@app.route("/api/quote/<sym>")
def api_quote(sym):
    sym = sym.upper()
    if kite:
        try:
            q = kite.quote([f"NSE:{sym}"])
            d = q[f"NSE:{sym}"]
            return jsonify({
                "status": "ok", "sym": sym,
                "ltp": d["last_price"],
                "open": d["ohlc"]["open"], "high": d["ohlc"]["high"],
                "low": d["ohlc"]["low"], "close": d["ohlc"]["close"],
                "change": d["net_change"], "change_pct": d["net_change"] / d["ohlc"]["close"] * 100
            })
        except Exception as e:
            print(f"Quote error: {e}")

    ltp = SIM_PRICES.get(sym, 1000.0)
    base = ltp * 0.98
    return jsonify({
        "status": "simulated", "sym": sym, "ltp": round(ltp, 2),
        "open": round(base, 2), "high": round(ltp * 1.01, 2),
        "low": round(base * 0.99, 2), "close": round(base, 2),
        "change": round(ltp - base, 2),
        "change_pct": round((ltp - base) / base * 100, 2)
    })

@app.route("/api/prices")
def api_prices():
    """All simulated prices for watchlist"""
    return jsonify({"status": "simulated", "prices": SIM_PRICES})

@app.route("/api/cancel_order/<order_id>", methods=["DELETE"])
def api_cancel_order(order_id):
    if kite:
        try:
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    for o in sim_state["orders"]:
        if o["order_id"] == order_id and o["status"] == "PENDING":
            o["status"] = "CANCELLED"
            return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Order not found or not cancellable"})

import os

if __name__ == "__main__":
    print("╔════════════════════════════════════════╗")
    print("║  Trading Dashboard — Master v1.0       ║")
    print("╚════════════════════════════════════════╝")
    # Bind to 0.0.0.0 and grab the PORT environment variable from Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
