"""
TradeCore / WebCode Terminal — Master Backend
Flask + Gunicorn (Render-compatible)
KiteConnect optional — falls back to paper simulation
"""

import os
import random
import time
import threading
from datetime import datetime
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

# ── KiteConnect (optional) ─────────────────────────────────────────────────
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

# ── Simulated state ────────────────────────────────────────────────────────
sim_state = {
    "balance": 100000.0, "invested": 0.0, "realized": 0.0,
    "positions": [], "orders": [], "order_counter": 1,
}

SIM_PRICES = {
    "RELIANCE": 2920.0, "INFY": 1580.0, "TCS": 3950.0,
    "HDFC": 1620.0, "ICICIBANK": 1250.0, "SBIN": 825.0,
    "WIPRO": 590.0, "NIFTY": 24800.0, "BANKNIFTY": 53200.0,
    "SENSEX": 81500.0, "VIX": 14.2,
}

_ticker_started = False

def _sim_tick():
    while True:
        for sym in list(SIM_PRICES.keys()):
            drift = 0.0005 if sym == "VIX" else 0.0015
            SIM_PRICES[sym] = round(SIM_PRICES[sym] * (1 + random.uniform(-drift, drift)), 2)
        for p in sim_state["positions"]:
            p["ltp"] = SIM_PRICES.get(p["sym"], p["ltp"])
        time.sleep(2)

def _start_ticker():
    global _ticker_started
    if not _ticker_started:
        threading.Thread(target=_sim_tick, daemon=True).start()
        _ticker_started = True

_start_ticker()

def _get_unrealized():
    total = 0.0
    for p in sim_state["positions"]:
        if p["side"] == "BUY":
            total += (p["ltp"] - p["entryPrice"]) * p["qty"]
        else:
            total += (p["entryPrice"] - p["ltp"]) * p["qty"]
    return round(total, 2)

# ── Routes ─────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/account")
def api_account():
    if kite:
        try:
            margins = kite.margins()
            equity = margins.get("equity", {})
            pos = kite.positions()
            return jsonify({
                "status": "ok",
                "balance": equity.get("net", 0),
                "invested": equity.get("utilised", {}).get("debits", 0),
                "realized": sum(p.get("realised", 0) for p in pos.get("day", [])),
                "unrealized": sum(p.get("unrealised", 0) for p in pos.get("net", [])),
            })
        except Exception as e:
            print(f"Kite account error: {e}")
    return jsonify({
        "status": "simulated",
        "balance": sim_state["balance"],
        "invested": sim_state["invested"],
        "realized": round(sim_state["realized"], 2),
        "unrealized": _get_unrealized(),
    })

@app.route("/api/positions")
def api_positions():
    if kite:
        try:
            data = kite.positions()
            return jsonify({"status": "ok", "positions": [
                {"sym": p["tradingsymbol"], "side": "BUY" if p["quantity"] > 0 else "SELL",
                 "qty": abs(p["quantity"]), "entryPrice": p["average_price"], "ltp": p["last_price"]}
                for p in data.get("net", []) if p["quantity"] != 0
            ]})
        except Exception as e:
            print(f"Kite positions error: {e}")
    return jsonify({"status": "simulated", "positions": sim_state["positions"]})

@app.route("/api/orders")
def api_orders():
    if kite:
        try:
            return jsonify({"status": "ok", "orders": kite.orders()})
        except Exception as e:
            print(f"Kite orders error: {e}")
    return jsonify({"status": "simulated", "orders": list(reversed(sim_state["orders"]))})

@app.route("/api/place_order", methods=["POST"])
def api_place_order():
    body = request.get_json(silent=True) or {}
    sym = str(body.get("symbol", "")).upper()
    side = str(body.get("side", "BUY")).upper()
    qty = int(body.get("qty", 1))
    otype = str(body.get("type", "MARKET")).upper()
    price = float(body.get("price", 0))

    if not sym or qty < 1:
        return jsonify({"status": "error", "message": "Invalid symbol or quantity"}), 400

    if kite:
        try:
            txn = kite.TRANSACTION_TYPE_BUY if side == "BUY" else kite.TRANSACTION_TYPE_SELL
            oid = kite.place_order(
                tradingsymbol=sym, exchange=kite.EXCHANGE_NSE,
                transaction_type=txn, quantity=qty,
                order_type=kite.ORDER_TYPE_MARKET if otype == "MARKET" else kite.ORDER_TYPE_LIMIT,
                price=price if otype == "LIMIT" else None,
                product=kite.PRODUCT_MIS, variety=kite.VARIETY_REGULAR,
            )
            return jsonify({"status": "success", "order_id": oid})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    ltp = SIM_PRICES.get(sym, price or 1000.0)
    exec_price = ltp if otype == "MARKET" else (price or ltp)
    order_id = f"SIM{sim_state['order_counter']:05d}"
    sim_state["order_counter"] += 1
    cost = exec_price * qty

    existing = next((p for p in sim_state["positions"] if p["sym"] == sym), None)
    if existing and existing["side"] != side:
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
        if sim_state["balance"] - sim_state["invested"] < cost:
            return jsonify({"status": "error", "message": "Insufficient margin"})
        if existing:
            total_qty = existing["qty"] + qty
            existing["entryPrice"] = (existing["entryPrice"] * existing["qty"] + exec_price * qty) / total_qty
            existing["qty"] = total_qty
            existing["ltp"] = ltp
        else:
            sim_state["positions"].append({"sym": sym, "side": side, "qty": qty, "entryPrice": exec_price, "ltp": ltp})
        sim_state["invested"] += cost

    sim_state["orders"].append({
        "order_id": order_id, "sym": sym, "side": side, "qty": qty,
        "price": exec_price, "type": otype, "status": "COMPLETE",
        "time": datetime.now().strftime("%H:%M:%S"),
    })
    return jsonify({"status": "success", "order_id": order_id, "exec_price": exec_price})

@app.route("/api/quote/<path:sym>")
def api_quote(sym):
    sym = sym.upper()
    if kite:
        try:
            q = kite.quote([f"NSE:{sym}"])
            d = q[f"NSE:{sym}"]
            return jsonify({
                "status": "ok", "sym": sym, "ltp": d["last_price"],
                "open": d["ohlc"]["open"], "high": d["ohlc"]["high"],
                "low": d["ohlc"]["low"], "close": d["ohlc"]["close"],
                "change": d["net_change"],
                "change_pct": round(d["net_change"] / d["ohlc"]["close"] * 100, 2),
            })
        except Exception as e:
            print(f"Quote error: {e}")
    ltp = SIM_PRICES.get(sym, 1000.0)
    base = round(ltp * 0.98, 2)
    return jsonify({
        "status": "simulated", "sym": sym, "ltp": round(ltp, 2),
        "open": base, "high": round(ltp * 1.01, 2), "low": round(base * 0.99, 2), "close": base,
        "change": round(ltp - base, 2), "change_pct": round((ltp - base) / base * 100, 2),
    })

@app.route("/api/prices")
def api_prices():
    return jsonify({"status": "simulated", "prices": dict(SIM_PRICES)})

@app.route("/api/cancel_order/<path:order_id>", methods=["DELETE"])
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
    return jsonify({"status": "error", "message": "Not cancellable"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
