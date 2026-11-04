from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional
import requests

BASE_URL = "https://api.twelvedata.com"
API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
TIMEOUT = 20
MIN_RR = 2.0

@dataclass
class Candle:
    ts: str
    o: float
    h: float
    l: float
    c: float


def get_candles(symbol: str, interval: str, outputsize: int = 120) -> List[Candle]:
    if not API_KEY:
        raise RuntimeError("Missing TWELVE_DATA_API_KEY")
    r = requests.get(f"{BASE_URL}/time_series", params={"symbol": symbol, "interval": interval, "outputsize": outputsize, "apikey": API_KEY, "format": "JSON"}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("status") == "error" or not data.get("values"):
        raise RuntimeError(data.get("message", f"No candles for {symbol}"))
    out = [Candle(x["datetime"], float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"])) for x in data["values"]]
    out.reverse()
    if len(out) < 25:
        raise RuntimeError(f"Insufficient candles for {symbol} {interval}: {len(out)}")
    if any(not (v.l <= v.o <= v.h and v.l <= v.c <= v.h and v.l > 0) for v in out):
        raise RuntimeError(f"Invalid OHLC candles for {symbol} {interval}")
    if any(out[i].ts >= out[i+1].ts for i in range(len(out)-1)):
        raise RuntimeError(f"Unordered/duplicate candle timestamps for {symbol} {interval}")
    print(f"DATA OK | {symbol} | {interval} | count={len(out)} | first={out[0].ts} | last={out[-1].ts} | close={out[-1].c:.5f}", flush=True)
    return out


def atr(c: List[Candle], period: int = 14) -> float:
    tr = [max(c[i].h-c[i].l, abs(c[i].h-c[i-1].c), abs(c[i].l-c[i-1].c)) for i in range(1, len(c))]
    return sum(tr[-period:]) / period


def levels(side: str, entry: float, stop: float) -> Optional[dict]:
    risk = entry-stop if side == "LONG" else stop-entry
    if risk <= 0: return None
    s = 1 if side == "LONG" else -1
    return {"side": side, "entry": entry, "sl": stop, "tp1": entry+s*risk, "tp2": entry+s*2*risk, "tp3": entry+s*3*risk, "rr": MIN_RR}


def atr_candle_breakout_xau() -> Optional[dict]:
    c = get_candles("XAU/USD", "15min", 100)
    a = atr(c[:-1])
    x = c[-1]
    prev = c[-2]
    body = abs(x.c-x.o)
    if body < 0.8*a: return None
    if x.c > prev.h + 0.05*a:
        z = levels("LONG", x.c, min(x.l, prev.l)-0.15*a)
    elif x.c < prev.l - 0.05*a:
        z = levels("SHORT", x.c, max(x.h, prev.h)+0.15*a)
    else: return None
    if z: z.update(strategy="ATR CANDLE BREAKOUT", symbol="XAUUSD", timeframe="15m", setup_id=f"ATR-XAU-{x.ts}")
    return z


def daily_range_breakout_usdjpy() -> Optional[dict]:
    d = get_candles("USD/JPY", "1day", 12)
    h = get_candles("USD/JPY", "1h", 80)
    prev = d[-2]
    x = h[-1]
    a = atr(h[:-1])
    buffer = 0.08*a
    if x.c > prev.h + buffer:
        z = levels("LONG", x.c, max(prev.h-0.35*a, x.l-0.15*a))
    elif x.c < prev.l - buffer:
        z = levels("SHORT", x.c, min(prev.l+0.35*a, x.h+0.15*a))
    else: return None
    if z: z.update(strategy="DAILY RANGE BREAKOUT", symbol="USDJPY", timeframe="1H", setup_id=f"DRB-JPY-{x.ts}")
    return z


def donchian_scalper_xau() -> Optional[dict]:
    c = get_candles("XAU/USD", "5min", 100)
    x = c[-1]
    prior = c[-21:-1]
    upper, lower = max(v.h for v in prior), min(v.l for v in prior)
    a = atr(c[:-1])
    if x.c > upper + 0.03*a:
        z = levels("LONG", x.c, max(upper-0.35*a, x.l-0.15*a))
    elif x.c < lower - 0.03*a:
        z = levels("SHORT", x.c, min(lower+0.35*a, x.h+0.15*a))
    else: return None
    if z: z.update(strategy="DONCHIAN CHANNEL SCALPER", symbol="XAUUSD", timeframe="5m", setup_id=f"DON-XAU-{x.ts}")
    return z


def scan_all() -> list[dict]:
    out=[]
    errors=[]
    for fn in (atr_candle_breakout_xau, daily_range_breakout_usdjpy, donchian_scalper_xau):
        try:
            s=fn()
            if s: out.append(s)
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
            print(f"{fn.__name__}: ERROR: {e}", flush=True)
    if errors:
        raise RuntimeError("Data/strategy scan failed: " + "; ".join(errors))
    return out


def main():
    print("XAUUSD / USDJPY STRATEGY SCANNER - SIGNALS ONLY")
    signals=scan_all()
    if not signals: print("NO NEW SETUP")
    for s in signals:
        print(f"{s['strategy']} | {s['symbol']} | {s['side']} | {s['timeframe']}")
        print(f"Entry {s['entry']:.5f} | SL {s['sl']:.5f} | TP1 {s['tp1']:.5f} | TP2 {s['tp2']:.5f} | TP3 {s['tp3']:.5f} | RR 1:{s['rr']:.0f}")

if __name__ == "__main__": main()
