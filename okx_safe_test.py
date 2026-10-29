import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Any

import requests

BASE_URL = "https://www.okx.com"
TIMEOUT = 15
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sign(secret_key: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    payload = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def private_get(request_path: str) -> dict[str, Any]:
    api_key = os.environ["OKX_API_KEY"]
    secret_key = os.environ["OKX_SECRET_KEY"]
    passphrase = os.environ["OKX_PASSPHRASE"]
    timestamp = iso_timestamp()
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign(secret_key, timestamp, "GET", request_path),
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "x-simulated-trading": "1",
    }
    response = requests.get(BASE_URL + request_path, headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX private API error: code={data.get('code')} msg={data.get('msg')}")
    return data


def public_get(request_path: str) -> dict[str, Any]:
    response = requests.get(BASE_URL + request_path, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX public API error: code={data.get('code')} msg={data.get('msg')}")
    return data


def print_demo_balance() -> None:
    data = private_get("/api/v5/account/balance")
    accounts = data.get("data", [])
    print("OKX Demo authentication: SUCCESS")
    if not accounts:
        print("Demo balance: no account data returned")
        return
    account = accounts[0]
    print(f"Total equity: {account.get('totalEq', 'N/A')}")
    nonzero = []
    for detail in account.get("details", []):
        try:
            if float(detail.get("eq") or 0) != 0:
                nonzero.append((detail.get("ccy"), detail.get("eq"), detail.get("availEq")))
        except (TypeError, ValueError):
            pass
    for ccy, equity, available in nonzero:
        print(f"  {ccy}: equity={equity} available={available}")


def print_market_prices() -> None:
    print("\nPublic swap prices:")
    for inst_id in INSTRUMENTS:
        data = public_get(f"/api/v5/market/ticker?instId={inst_id}")
        ticker = data["data"][0]
        print(
            f"  {inst_id}: last={ticker.get('last')} "
            f"bid={ticker.get('bidPx')} ask={ticker.get('askPx')} ts={ticker.get('ts')}"
        )


def main() -> None:
    required = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing required GitHub secrets: " + ", ".join(missing))

    print("SAFE MODE: OKX simulated/demo API only. No order endpoints are called.")
    print_demo_balance()
    print_market_prices()
    print("\nSafe OKX demo test completed successfully.")


if __name__ == "__main__":
    main()
