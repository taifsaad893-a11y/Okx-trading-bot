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
_CANDLE_CACHE = {}

@dataclass
class Candle:
    ts: str
    o: float
    h: float
    l: float
    c: float
    volume: Optional[float] = None


def candle_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def get_candles(symbol: str, interval: str, outputsize: int = 120) -> List[Candle]:
    cache_key = (symbol, interval, outputsize)
    if cache_key in _CANDLE_CACHE:
        return list(_CANDLE_CACHE[cache_key])
    if not API_KEY:
        raise RuntimeError("Missing TWELVE_DATA_API_KEY")
    r = requests.get(f"{BASE_URL}/time_series", params={"symbol": symbol, "interval": interval, "outputsize": outputsize, "apikey": API_KEY, "format": "JSON", "timezone": "UTC"}, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Twelve Data HTTP {r.status_code}")
    data = r.json()
    if data.get("status") == "error" or not data.get("values"):
        raise RuntimeError(f"Twelve Data API error {data.get('code', 'no values')} for {symbol}")
    out = [Candle(x["datetime"], float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]), float(x["volume"]) if x.get("volume") not in (None, "", "null") else None) for x in data["values"]]
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
    _CANDLE_CACHE[cache_key] = list(out)
    return out


def atr(c: List[Candle], period: int = 14) -> float:
    tr = [max(c[i].h-c[i].l, abs(c[i].h-c[i-1].c), abs(c[i].l-c[i-1].c)) for i in range(1, len(c))]
    return sum(tr[-period:]) / period


def levels(side: str, entry: float, stop: float) -> Optional[dict]:
    risk = entry-stop if side == "LONG" else stop-entry
    if risk <= 0: return None
    s = 1 if side == "LONG" else -1
    return {"side": side, "entry": entry, "sl": stop, "tp1": entry+s*risk, "tp2": entry+s*2*risk, "tp3": entry+s*3*risk, "rr": MIN_RR}


def ema(values: List[float], period: int) -> float:
    if len(values) < period: raise ValueError(f'Need {period} values for EMA')
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]: result = value * alpha + result * (1 - alpha)
    return result

def atr_candle_breakout_xau() -> Optional[dict]:
    # Closed 15m candle drives entries; 1H EMA, volume and S/R are quality factors only.
    c = get_candles('XAU/USD', '15min', 100)
    a = atr(c[:-1])
    x, prev = c[-1], c[-2]
    body = abs(x.c-x.o)
    if body < 0.8*a:
        print(f'ATR CHECK | {x.ts} | no setup: weak candle body', flush=True)
        return None
    side = None
    setup_type = None
    if x.c > prev.h + 0.05*a:
        side, setup_type = 'LONG', 'BREAKOUT'
    elif x.c < prev.l - 0.05*a:
        side, setup_type = 'SHORT', 'BREAKOUT'
    elif len(c) >= 4:
        ref = c[-3]
        if prev.c > ref.h + 0.05*a and x.l <= ref.h + 0.12*a and x.c > ref.h and x.c > x.o and body >= 0.4*a:
            side, setup_type = 'LONG', 'BREAKOUT RETEST'
        elif prev.c < ref.l - 0.05*a and x.h >= ref.l - 0.12*a and x.c < ref.l and x.c < x.o and body >= 0.4*a:
            side, setup_type = 'SHORT', 'BREAKOUT RETEST'
    if not side:
        print(f'ATR CHECK | {x.ts} | no confirmed breakout/retest', flush=True)
        return None
    stop = min(x.l, prev.l)-0.15*a if side == 'LONG' else max(x.h, prev.h)+0.15*a
    z = levels(side, x.c, stop)
    if not z: return None
    score = 5.0
    notes = []
    try:
        h1 = get_candles('XAU/USD', '1h', 240)
        closes = [v.c for v in h1]
        e50, e200 = ema(closes, 50), ema(closes, 200)
        aligned = (side == 'LONG' and closes[-1] > e50 > e200) or (side == 'SHORT' and closes[-1] < e50 < e200)
        if aligned: score += 1.5; notes.append('1H EMA50/200 aligned')
        else: notes.append('1H EMA not aligned (quality only)')
    except Exception as exc: notes.append('1H EMA unavailable: ' + str(exc)[:80])
    vols = [v.volume for v in c[-21:-1] if v.volume is not None]
    if x.volume is not None and len(vols) >= 10:
        avg_vol = sum(vols)/len(vols)
        if avg_vol > 0 and x.volume >= 1.15*avg_vol: score += 1.0; notes.append('volume > 1.15x average')
        else: notes.append('volume not expanded (quality only)')
    else: notes.append('volume unavailable from feed')
    recent = c[-41:-1]
    if side == 'LONG':
        levels_above = [v.h for v in recent if v.h > x.c]
        room = min(levels_above)-x.c if levels_above else None
    else:
        levels_below = [v.l for v in recent if v.l < x.c]
        room = x.c-max(levels_below) if levels_below else None
    if room is None or room >= abs(x.c-stop): score += 0.5; notes.append('S/R room acceptable')
    else: notes.append('nearby S/R: caution')
    z.update(strategy='ATR CANDLE BREAKOUT PRO', symbol='XAUUSD', timeframe='15m',
             setup_id=f'ATR-XAU-{x.ts}', closed_at=(candle_time(x.ts)+timedelta(minutes=15)).isoformat(),
             max_age_minutes=12, max_deviation=0.35*a, setup_type=setup_type,
             score=round(score,1), quality='A' if score >= 6.5 else 'B', analysis='; '.join(notes), atr=a)
    print(f'ATR SIGNAL | {side} | {setup_type} | score={score:.1f} | ' + z['analysis'], flush=True)
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


def scan_all() -> list[dict]:
    out=[]
    try:
        s=atr_candle_breakout_xau()
        if s: out.append(s)
    except Exception as e:
        print(f"atr_candle_breakout_xau: ERROR: {e}", flush=True)
        raise RuntimeError("Incomplete market scan; see strategy status above")
    return out


def main():
    print("XAUUSD STRATEGY SCANNER - SIGNALS ONLY")
    signals=scan_all()
    if not signals: print("NO NEW SETUP")
    for s in signals:
        print(f"{s['strategy']} | {s['symbol']} | {s['side']} | {s['timeframe']}")
        print(f"Entry {s['entry']:.5f} | SL {s['sl']:.5f} | TP1 {s['tp1']:.5f} | TP2 {s['tp2']:.5f} | TP3 {s['tp3']:.5f} | RR 1:{s['rr']:.0f}")

if __name__ == "__main__": main()
