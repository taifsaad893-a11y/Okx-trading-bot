import base64
import json
import os
from datetime import datetime, timezone
import requests
from scanner import scan_all, get_candles, candle_time

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
GH_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
STATE_PATH = "signal_state.json"
GH_URL = f"https://api.github.com/repos/{REPO}/contents/{STATE_PATH}"
HEADERS = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
_PRICE_CACHE = {}


def telegram_health():
    for method, params in (("getMe", {}), ("getChat", {"chat_id": CHAT})):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}",
                              json=params, timeout=20)
            if not r.ok or r.json().get("ok") is not True:
                raise RuntimeError("Telegram rejected health check")
        except Exception:
            raise RuntimeError(f"TELEGRAM CHECK FAILED | {method}") from None
    print("TELEGRAM CHECK OK | bot and destination accessible; no test message sent", flush=True)


def load_state():
    r = requests.get(GH_URL, headers=HEADERS, timeout=20)
    if r.status_code == 404:
        return {"sent": [], "active": {}}, None
    r.raise_for_status()
    payload = r.json()
    return json.loads(base64.b64decode(payload["content"])), payload["sha"]


def save_state(state, sha):
    body = {"message": "Persist scanner signal tracking [skip ci]",
            "content": base64.b64encode(json.dumps(state, ensure_ascii=False).encode()).decode()}
    if sha:
        body["sha"] = sha
    r = requests.put(GH_URL, headers=HEADERS, json=body, timeout=20)
    r.raise_for_status()
    return r.json()["content"]["sha"]


def send(message):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": CHAT, "text": message}, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Telegram send HTTP {r.status_code}")
    if not r.json().get("ok"):
        raise RuntimeError("Telegram send failed")


def current_price(symbol):
    if symbol in _PRICE_CACHE:
        return _PRICE_CACHE[symbol]
    # Live reference, not the signal candle close.
    api_symbol = "XAU/USD" if symbol == "XAUUSD" else "USD/JPY"
    r = requests.get("https://api.twelvedata.com/price",
                     params={"symbol": api_symbol, "apikey": os.environ["TWELVE_DATA_API_KEY"]},
                     timeout=20)
    if not r.ok:
        raise RuntimeError(f"Twelve Data price HTTP {r.status_code}")
    result = r.json()
    if "price" not in result:
        raise RuntimeError(f"Current price unavailable for {symbol}: code={result.get('code', 'unknown')}")
    _PRICE_CACHE[symbol] = float(result["price"])
    return _PRICE_CACHE[symbol]


def track_active(state, persist=None):
    changed = False
    for sid, trade in list(state["active"].items()):
        if trade["symbol"] != "XAUUSD":
            continue  # USDJPY monitoring disabled; retain historical state.
        interval = "5min" if trade["symbol"] == "XAUUSD" else "1h"
        symbol = "XAU/USD" if trade["symbol"] == "XAUUSD" else "USD/JPY"
        bars = get_candles(symbol, interval, 100)
        opened = datetime.fromisoformat(trade["opened_at"])
        bars = [b for b in bars if candle_time(b.ts) >= opened]
        side = trade["side"]
        for bar in bars:
            # Conservatively assume SL hit first when SL and TP touched in same bar.
            sl_hit = bar.l <= trade["sl"] if side == "LONG" else bar.h >= trade["sl"]
            if sl_hit:
                send(f"🛑 ضرب الستوب | {trade['symbol']} | {trade['strategy']}\nID: {sid}\nSL: {trade['sl']:.5f}")
                del state["active"][sid]
                changed = True
                if persist:
                    persist(state)
                break
            for level in ("tp1", "tp2", "tp3"):
                hit = bar.h >= trade[level] if side == "LONG" else bar.l <= trade[level]
                if hit and level not in trade["hit"]:
                    send(f"🎯 تحقق {level.upper()} | {trade['symbol']} | {trade['strategy']}\nID: {sid}\nالسعر: {trade[level]:.5f}")
                    trade["hit"].append(level)
                    changed = True
                    if level == "tp3":
                        del state["active"][sid]
                    if persist:
                        persist(state)
            if "tp3" in trade["hit"]:
                state["active"].pop(sid, None)
                changed = True
                break
        if sid in state["active"]:
            state["active"][sid] = trade
    return changed


def main():
    telegram_health()
    state, sha = load_state()
    def persist(current):
        nonlocal sha
        current["sent"] = current["sent"][-300:]
        sha = save_state(current, sha)
        print("STATE SAVED | notification progress committed", flush=True)
    state.setdefault("sent", [])
    state.setdefault("active", {})
    # Prioritize fresh entry signals; tracking data may hit the provider quota.
    signals = scan_all()
    try:
        changed = track_active(state, persist)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 429:
            raise
        print("TRACKING DEFERRED | Twelve Data rate limit (429); preserving active trades", flush=True)
        changed = False
    now = datetime.now(timezone.utc)
    for signal in signals:
        sid = signal["setup_id"]
        if sid in state["sent"]:
            continue
        closed_at = datetime.fromisoformat(signal["closed_at"])
        age_minutes = (now - closed_at).total_seconds() / 60
        if age_minutes < 0 or age_minutes > signal["max_age_minutes"]:
            print(f"SKIP LATE | {sid} | age={age_minutes:.1f}m", flush=True)
            continue
        price = current_price(signal["symbol"])
        if abs(price - signal["entry"]) > signal["max_deviation"]:
            print(f"SKIP MOVED | {sid} | signal={signal['entry']:.5f} now={price:.5f}", flush=True)
            continue
        # Don't issue a trade that already touched SL or TP1.
        if ((signal["side"] == "LONG" and (price <= signal["sl"] or price >= signal["tp1"])) or
            (signal["side"] == "SHORT" and (price >= signal["sl"] or price <= signal["tp1"]))):
            print(f"SKIP EXPIRED | {sid} | now={price:.5f}", flush=True)
            continue
        message = (f"🚨 صفقة جديدة\nالاستراتيجية: {signal['strategy']}\n"
                   f"الرمز: {signal['symbol']} | الفريم: {signal['timeframe']}\n"
                   f"الاتجاه: {signal['side']}\n"
                   f"Entry: {signal['entry']:.5f} | السعر الآن: {price:.5f}\n"
                   f"SL: {signal['sl']:.5f}\nTP1: {signal['tp1']:.5f}\n"
                   f"TP2: {signal['tp2']:.5f}\nTP3: {signal['tp3']:.5f}\n"
                   f"ID: {sid}\n⚠️ إشارة فقط — لا يوجد تنفيذ تلقائي")
        send(message)
        state["sent"].append(sid)
        state["active"][sid] = {k: signal[k] for k in ("symbol", "strategy", "side", "entry", "sl", "tp1", "tp2", "tp3")}
        state["active"][sid].update(opened_at=now.isoformat(), hit=[])
        changed = True
        persist(state)
        print(f"Alert sent: {sid}", flush=True)
    if changed:
        state["sent"] = state["sent"][-300:]
        persist(state)
    else:
        print("No new alert / target event", flush=True)


if __name__ == "__main__":
    main()

