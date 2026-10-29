from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode

import requests

from scanner import INSTRUMENTS, get_closed_candles, taif_signal

BASE_URL = "https://www.okx.com"
TIMEOUT = 15
DEMO_RISK_USDT = Decimal(os.environ.get("DEMO_RISK_USDT", "5"))
DEMO_MAX_NOTIONAL_USDT = Decimal(os.environ.get("DEMO_MAX_NOTIONAL_USDT", "100"))


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def signature(secret: str, ts: str, method: str, path: str, body: str = "") -> str:
    msg = f"{ts}{method.upper()}{path}{body}"
    digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def private_request(method: str, path: str, params: dict | None = None, payload: dict | None = None) -> dict[str, Any]:
    api_key = os.environ["OKX_API_KEY"]
    secret = os.environ["OKX_SECRET_KEY"]
    passphrase = os.environ["OKX_PASSPHRASE"]
    method = method.upper()
    request_path = path
    if params:
        request_path += "?" + urlencode(params)
    body = json.dumps(payload, separators=(",", ":")) if payload is not None else ""
    ts = timestamp()
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": signature(secret, ts, method, request_path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "x-simulated-trading": "1",
    }
    r = requests.request(method, BASE_URL + request_path, headers=headers, data=body or None, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX API error code={data.get('code')} msg={data.get('msg')}")
    return data


def instrument(inst_id: str) -> dict:
    r = requests.get(BASE_URL + "/api/v5/public/instruments", params={"instType": "SWAP", "instId": inst_id}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0" or not data.get("data"):
        raise RuntimeError(f"Instrument unavailable: {inst_id}")
    return data["data"][0]


def current_positions() -> set[str]:
    data = private_request("GET", "/api/v5/account/positions", params={"instType": "SWAP"})
    out = set()
    for p in data.get("data", []):
        try:
            if Decimal(p.get("pos") or "0") != 0:
                out.add(p["instId"])
        except Exception:
            pass
    return out


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def contracts_for_signal(inst: dict, signal: dict) -> str:
    entry = Decimal(str(signal["entry"]))
    sl = Decimal(str(signal["sl"]))
    risk_per_coin = abs(entry - sl)
    if risk_per_coin <= 0:
        raise RuntimeError("Invalid signal risk")

    ct_val = Decimal(inst.get("ctVal") or "1")
    lot = Decimal(inst.get("lotSz") or "1")
    min_sz = Decimal(inst.get("minSz") or lot)

    # Linear USDT swaps: contract coin exposure is ctVal.
    risk_per_contract = risk_per_coin * ct_val
    by_risk = DEMO_RISK_USDT / risk_per_contract
    by_notional = DEMO_MAX_NOTIONAL_USDT / (entry * ct_val)
    size = floor_step(min(by_risk, by_notional), lot)
    if size < min_sz:
        size = min_sz
    return format(size, "f")


def place_demo_market(inst_id: str, signal: dict) -> dict:
    inst = instrument(inst_id)
    size = contracts_for_signal(inst, signal)
    side = "buy" if signal["side"] == "LONG" else "sell"

    # Hard safety: this file always sends the OKX simulated-trading header.
    order = {
        "instId": inst_id,
        "tdMode": "cross",
        "side": side,
        "ordType": "market",
        "sz": size,
    }
    result = private_request("POST", "/api/v5/trade/order", payload=order)
    return {"size": size, "response": result}


def main() -> None:
    if os.environ.get("ENABLE_DEMO_ORDERS") != "YES":
        print("DEMO ORDER EXECUTION DISABLED. Set ENABLE_DEMO_ORDERS=YES to permit simulated OKX orders.")
        return

    required = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
    missing = [x for x in required if not os.getenv(x)]
    if missing:
        raise RuntimeError("Missing secrets: " + ", ".join(missing))

    positions = current_positions()
    for inst_id in INSTRUMENTS:
        if inst_id in positions:
            print(f"{inst_id}: SKIP - demo position already open")
            continue

        c15 = get_closed_candles(inst_id, "15m")
        c1h = get_closed_candles(inst_id, "1H")
        c4h = get_closed_candles(inst_id, "4H")
        signal = taif_signal(c15, c1h, c4h)
        if not signal:
            print(f"{inst_id}: NO SETUP")
            continue

        # Only A setups auto-enter demo. B remains analysis-only.
        if signal["grade"] != "A":
            print(f"{inst_id}: {signal['grade']} {signal['side']} {signal['setup']} - NOT AUTO-ENTERED")
            continue

        result = place_demo_market(inst_id, signal)
        print(
            f"{inst_id}: DEMO ORDER SENT | {signal['side']} {signal['setup']} | "
            f"size={result['size']} | entry_ref={signal['entry']:.8g} | SL_ref={signal['sl']:.8g} | TP2_ref={signal['tp2']:.8g}"
        )
        print(json.dumps(result["response"], ensure_ascii=False))


if __name__ == "__main__":
    main()
