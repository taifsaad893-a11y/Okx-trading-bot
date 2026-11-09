from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
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


def candle_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def get_candles(symbol: str, interval: str, outputsize: int = 120) -> List[Candle]:
    if not API_KEY:
        raise RuntimeError("Missing TWELVE_DATA_API_KEY")
    r = requests.get(f"{BASE_URL}/time_series", params={"symbol": symbol, "interval": interval, "outputsize": outputsize, "apikey": API_KEY, "format": "JSON", "timezone": "UTC"}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("status") == "error" or not data.get("values"):
        raise RuntimeError(data.get("message", f"No candles for {symbol}"))
    out = [Candle(x["datetime"], float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"])) for x in data["values"]]
    out.reverse()
    min_count = 10 if interval == '1day' else 25
    if len(out) < min_count:
        raise RuntimeError(f"Insufficient candles for {symbol} {interval}: {len(out)}")
    if any(not (v.l <= v.o <= v.h and v.l <= v.c <= v.h and v.l > 0) for v in out):
        raise RuntimeError(f"Invalid OHLC candles for {symbol} {interval}")
    if any(out[i].ts >= out[i+1].ts for i in range(len(out)-1)):
        raise RuntimeError(f"Unordered/duplicate candle timestamps for {symbol} {interval}")
    # Twelve Data timestamps are explicitly requested in UTC. Never trade on a forming candle.
    duration = {"5min": timedelta(minutes=5), "15min": timedelta(minutes=15), "1h": timedelta(hours=1), "1day": timedelta(days=1)}[interval]
    now = datetime.now(timezone.utc)
    if interval != "1day" and candle_time(out[-1].ts) > now + timedelta(minutes=2):
        raise RuntimeError(f"Future candle timestamp for {symbol} {interval}: {out[-1].ts} UTC")
    if interval != "1day":
        out = [v for v in out if candle_time(v.ts) + duration <= now - timedelta(seconds=30)]
        if len(out) < min_count:
            raise RuntimeError(f"Insufficient closed candles for {symbol} {interval}")
        lag = now - (candle_time(out[-1].ts) + duration)
        allowed_lag = {"5min": timedelta(minutes=20), "15min": timedelta(minutes=25), "1h": timedelta(minutes=75)}[interval]
        if lag > allowed_lag:
            raise RuntimeError(f"Stale candles for {symbol} {interval}: lag={lag}")
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
    if body < 0.8*a:
        print(f"ATR CHECK | candle={x.ts} | NO SETUP: weak body | body={body:.4f} required={0.8*a:.4f} atr={a:.4f}", flush=True)
        return None
    if x.c > prev.h + 0.05*a:
        z = levels("LONG", x.c, min(x.l, prev.l)-0.15*a)
    elif x.c < prev.l - 0.05*a:
        z = levels("SHORT", x.c, max(x.h, prev.h)+0.15*a)
    else:
        print(f"ATR CHECK | candle={x.ts} | NO SETUP: no breakout | close={x.c:.4f} long_above={prev.h+0.05*a:.4f} short_below={prev.l-0.05*a:.4f}", flush=True)
        return None
    if z:
        print(f"ATR SIGNAL | candle={x.ts} | {z['side']} | entry={z['entry']:.4f} | atr={a:.4f}", flush=True)
    if z: z.update(strategy="ATR CANDLE BREAKOUT", symbol="XAUUSD", timeframe="15m", setup_id=f"ATR-XAU-{x.ts}", closed_at=(candle_time(x.ts)+timedelta(minutes=15)).isoformat(), max_age_minutes=12, max_deviation=0.35*a)
    return z


def daily_range_breakout_usdjpy() -> Optional[dict]:
    d = get_candles("USD/JPY", "1day", 12)
    h = get_candles("USD/JPY", "1h", 80)
    # Daily bars may be labelled with the next calendar day before UTC midnight.
    # Use the latest fully past UTC daily bar as the breakout reference.
    completed_days = [v for v in d if candle_time(v.ts).date() < datetime.now(timezone.utc).date()]
    if not completed_days:
        raise RuntimeError("No completed USDJPY daily reference candle")
    prev = completed_days[-1]
    x = h[-1]
    a = atr(h[:-1])
    buffer = 0.08*a
    if x.c > prev.h + buffer:
        z = levels("LONG", x.c, max(prev.h-0.35*a, x.l-0.15*a))
    elif x.c < prev.l - buffer:
        z = levels("SHORT", x.c, min(prev.l+0.35*a, x.h+0.15*a))
    else: return None
    if z: z.update(strategy="DAILY RANGE BREAKOUT", symbol="USDJPY", timeframe="1H", setup_id=f"DRB-JPY-{x.ts}", closed_at=(candle_time(x.ts)+timedelta(hours=1)).isoformat(), max_age_minutes=17, max_deviation=0.35*a)
    return z


def donchian_scalper_xau() -> Optional[dict]:
    c = get_candles("XAU/USD", "5min", 100)
    # Scan the three most recent CLOSED 5m bars, not only the last bar.
    # A 15m scheduled scan would otherwise miss breakouts between checks.
    now = datetime.now(timezone.utc)
    for i in range(len(c) - 1, max(20, len(c) - 4), -1):
        x = c[i]
        if now - (candle_time(x.ts) + timedelta(minutes=5)) > timedelta(minutes=16):
            continue
        prior = c[i-20:i]
        upper, lower = max(v.h for v in prior), min(v.l for v in prior)
        a = atr(c[:i])
        if x.c > upper + 0.03*a:
            z = levels("LONG", x.c, max(upper-0.35*a, x.l-0.15*a))
        elif x.c < lower - 0.03*a:
            z = levels("SHORT", x.c, min(lower+0.35*a, x.h+0.15*a))
        else:
            continue
        if z:
            z.update(strategy="DONCHIAN CHANNEL SCALPER", symbol="XAUUSD",
                     timeframe="5m", setup_id=f"DON-XAU-{x.ts}",
                     closed_at=(candle_time(x.ts)+timedelta(minutes=5)).isoformat(),
                     max_age_minutes=7, max_deviation=0.35*a)
            print(f"DONCHIAN SIGNAL | {x.ts} | {z['side']} | entry={z['entry']:.5f}", flush=True)
            return z
    print("DONCHIAN | checked last 3 closed 5m candles | no fresh breakout", flush=True)
    return None

def scan_all() -> list[dict]:
    out=[]
    errors=[]
    for fn in (atr_candle_breakout_xau, donchian_scalper_xau):
        try:
            s=fn()
            if s: out.append(s)
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
            print(f"{fn.__name__}: ERROR: {e}", flush=True)
    if errors:
        print("PARTIAL SCAN | " + "; ".join(errors) + f" | valid_signals={len(out)}", flush=True)
        if not out:
            print("SCAN WARNING | No signal returned; at least one strategy could not be checked", flush=True)
    return out


def main():
    print("XAUUSD STRATEGY SCANNER - SIGNALS ONLY")
    signals=scan_all()
    if not signals: print("NO NEW SETUP")
    for s in signals:
        print(f"{s['strategy']} | {s['symbol']} | {s['side']} | {s['timeframe']}")
        print(f"Entry {s['entry']:.5f} | SL {s['sl']:.5f} | TP1 {s['tp1']:.5f} | TP2 {s['tp2']:.5f} | TP3 {s['tp3']:.5f} | RR 1:{s['rr']:.0f}")

if __name__ == "__main__": main()
