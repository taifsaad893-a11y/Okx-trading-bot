from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import List

BASE_URL = "https://www.okx.com"
TIMEOUT = 15
INSTRUMENTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
TIMEFRAMES = ["15m", "1H", "4H"]
CANDLE_LIMIT = 220


@dataclass
class Candle:
    ts: int
    o: float
    h: float
    l: float
    c: float
    vol: float


def get_closed_candles(inst_id: str, bar: str, limit: int = CANDLE_LIMIT) -> List[Candle]:
    url = f"{BASE_URL}/api/v5/market/history-candles"
    r = requests.get(url, params={"instId": inst_id, "bar": bar, "limit": str(limit)}, timeout=TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX error {payload.get('code')}: {payload.get('msg')}")

    candles: List[Candle] = []
    for row in payload.get("data", []):
        # OKX candle: ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm
        if len(row) >= 9 and row[8] == "1":
            candles.append(Candle(int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
    candles.sort(key=lambda x: x.ts)
    return candles


def ema(values: List[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values")
    alpha = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError("Not enough values for RSI")
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = ((avg_gain * (period - 1)) + g) / period
        avg_loss = ((avg_loss * (period - 1)) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise ValueError("Not enough candles for ATR")
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    result = sum(trs[:period]) / period
    for tr in trs[period:]:
        result = ((result * (period - 1)) + tr) / period
    return result


def support_resistance(candles: List[Candle], lookback: int = 50) -> tuple[float, float]:
    window = candles[-lookback:]
    return min(x.l for x in window), max(x.h for x in window)


def structure(candles: List[Candle], swing: int = 10) -> str:
    if len(candles) < swing * 2:
        return "UNKNOWN"
    prev = candles[-swing * 2:-swing]
    recent = candles[-swing:]
    prev_high, prev_low = max(x.h for x in prev), min(x.l for x in prev)
    recent_high, recent_low = max(x.h for x in recent), min(x.l for x in recent)
    if recent_high > prev_high and recent_low > prev_low:
        return "BULLISH"
    if recent_high < prev_high and recent_low < prev_low:
        return "BEARISH"
    return "RANGE/MIXED"


def analyze(inst_id: str, bar: str) -> dict:
    candles = get_closed_candles(inst_id, bar)
    if len(candles) < 205:
        raise RuntimeError(f"Only {len(candles)} closed candles returned")
    closes = [x.c for x in candles]
    last = candles[-1]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    rv = rsi(closes, 14)
    av = atr(candles, 14)
    support, resistance = support_resistance(candles, 50)
    st = structure(candles)
    trend = "BULLISH" if last.c > e20 > e50 > e200 else "BEARISH" if last.c < e20 < e50 < e200 else "MIXED"
    avg_vol = sum(x.vol for x in candles[-21:-1]) / 20
    vol_ratio = (last.vol / avg_vol) if avg_vol else 0.0
    return {
        "price": last.c,
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "rsi": rv,
        "atr": av,
        "support": support,
        "resistance": resistance,
        "structure": st,
        "trend": trend,
        "volume_ratio": vol_ratio,
    }


def main() -> None:
    print("OKX MARKET SCANNER - CLOSED CANDLES ONLY")
    print("Analysis only: no order placement.\n")
    for inst_id in INSTRUMENTS:
        print(f"=== {inst_id} ===")
        for bar in TIMEFRAMES:
            try:
                x = analyze(inst_id, bar)
                print(
                    f"{bar:>3} | price={x['price']:.6g} | trend={x['trend']} | structure={x['structure']} | "
                    f"RSI={x['rsi']:.1f} | ATR={x['atr']:.6g} | vol={x['volume_ratio']:.2f}x | "
                    f"S={x['support']:.6g} R={x['resistance']:.6g} | "
                    f"EMA20/50/200={x['ema20']:.6g}/{x['ema50']:.6g}/{x['ema200']:.6g}"
                )
            except Exception as exc:
                print(f"{bar:>3} | ERROR: {exc}")
        print()


if __name__ == "__main__":
    main()
