from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import List, Optional

BASE_URL = "https://www.okx.com"
TIMEOUT = 15
INSTRUMENTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
CANDLE_LIMIT = 220
MIN_RR = 2.0


@dataclass
class Candle:
    ts: int
    o: float
    h: float
    l: float
    c: float
    vol: float


def get_closed_candles(inst_id: str, bar: str, limit: int = CANDLE_LIMIT) -> List[Candle]:
    r = requests.get(
        f"{BASE_URL}/api/v5/market/history-candles",
        params={"instId": inst_id, "bar": bar, "limit": str(limit)},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX error {payload.get('code')}: {payload.get('msg')}")
    out: List[Candle] = []
    for row in payload.get("data", []):
        if len(row) >= 9 and row[8] == "1":
            out.append(Candle(int(row[0]), *map(float, row[1:6])))
    out.sort(key=lambda x: x.ts)
    return out


def ema(values: List[float], period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    x = sum(values[:period]) / period
    for v in values[period:]:
        x = alpha * v + (1.0 - alpha) * x
    return x


def atr(c: List[Candle], period: int = 14) -> float:
    tr = [max(c[i].h-c[i].l, abs(c[i].h-c[i-1].c), abs(c[i].l-c[i-1].c)) for i in range(1, len(c))]
    x = sum(tr[:period]) / period
    for v in tr[period:]:
        x = ((x * (period - 1)) + v) / period
    return x


def trend(c: List[Candle]) -> str:
    closes = [x.c for x in c]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    if c[-1].c > e20 > e50:
        return "BULLISH"
    if c[-1].c < e20 < e50:
        return "BEARISH"
    return "MIXED"


def cisd(c: List[Candle]) -> Optional[str]:
    # Taif CISD: current close crosses the opening price of the first candle
    # in a sequence of at least two opposing candles.
    if len(c) < 4:
        return None
    last = c[-1]
    for n in range(2, 6):
        seq = c[-1-n:-1]
        if len(seq) != n:
            continue
        if all(x.c < x.o for x in seq) and last.c > seq[0].o:
            return "BULLISH"
        if all(x.c > x.o for x in seq) and last.c < seq[0].o:
            return "BEARISH"
    return None


def taif_signal(c15: List[Candle], c1h: List[Candle], c4h: List[Candle]) -> Optional[dict]:
    if min(len(c15), len(c1h), len(c4h)) < 60:
        return None

    last = c15[-1]
    a = atr(c15)
    t1, t4 = trend(c1h), trend(c4h)
    ci = cisd(c15)
    avg_vol = sum(x.vol for x in c15[-21:-1]) / 20
    vr = last.vol / avg_vol if avg_vol else 0.0

    # Recent levels are calculated BEFORE the signal candle.
    recent = c15[-21:-1]
    resistance = max(x.h for x in recent)
    support = min(x.l for x in recent)
    body = abs(last.c - last.o)

    # Liquidity sweep: Taif's preferred setup.
    long_sweep = last.l < support and last.c > support
    short_sweep = last.h > resistance and last.c < resistance

    # Ordinary breakout: closed 15m candle beyond the previous 20-candle range,
    # 0.10 ATR buffer, aligned with 1h trend, with no retest requirement.
    long_break = last.c > resistance + 0.10 * a and t1 == "BULLISH"
    short_break = last.c < support - 0.10 * a and t1 == "BEARISH"

    side = setup = None
    score = 0.0
    if long_sweep:
        side, setup, score = "LONG", "LIQUIDITY_SWEEP", 6.0
    elif short_sweep:
        side, setup, score = "SHORT", "LIQUIDITY_SWEEP", 6.0
    elif long_break:
        side, setup, score = "LONG", "BREAKOUT", 6.3
    elif short_break:
        side, setup, score = "SHORT", "BREAKOUT", 6.3
    else:
        return None

    wanted = "BULLISH" if side == "LONG" else "BEARISH"
    opposite = "BEARISH" if side == "LONG" else "BULLISH"

    if t1 == wanted:
        score += 0.7
    if t4 == wanted:
        score += 0.5
    if t1 == opposite and t4 == opposite:
        score -= 1.2
    if ci == wanted:
        score += 0.5
    if vr >= 1.5:
        score += 0.7
    elif vr >= 1.1:
        score += 0.3
    elif vr < 0.65:
        score -= 0.4

    # Strong breakout quality bonuses; CISD is context/bonus, not mandatory.
    if setup == "BREAKOUT":
        if vr >= 1.25:
            score += 0.3
        if body >= 0.55 * a:
            score += 0.3
        # Do not chase an already overextended breakout.
        level = resistance if side == "LONG" else support
        if abs(last.c - level) > 1.0 * a:
            return None

    entry = last.c
    if side == "LONG":
        sl = min(last.l, support) - 0.20 * a
        risk = entry - sl
        if risk <= 0:
            return None
        tp1, tp2, tp3 = entry + risk, entry + 2*risk, entry + 3*risk
    else:
        sl = max(last.h, resistance) + 0.20 * a
        risk = sl - entry
        if risk <= 0:
            return None
        tp1, tp2, tp3 = entry - risk, entry - 2*risk, entry - 3*risk

    grade = "A" if score >= 7.5 else "B" if score >= 6.0 else "C"
    if grade == "C":
        return None

    return {
        "side": side,
        "setup": setup,
        "grade": grade,
        "score": round(score, 1),
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr_tp2": MIN_RR,
        "trend_1h": t1,
        "trend_4h": t4,
        "cisd": ci or "NONE",
        "volume_ratio": vr,
    }


def main() -> None:
    print("OKX TAIF STRATEGY SCANNER - CLOSED CANDLES ONLY")
    print("Analysis only. No orders are placed.\n")
    for inst in INSTRUMENTS:
        try:
            c15 = get_closed_candles(inst, "15m")
            c1h = get_closed_candles(inst, "1H")
            c4h = get_closed_candles(inst, "4H")
            s = taif_signal(c15, c1h, c4h)
            print(f"=== {inst} ===")
            if not s:
                print("NO VALID TAIF SETUP\n")
                continue
            print(f"{s['grade']} | {s['side']} | {s['setup']} | score={s['score']}")
            print(f"Entry: {s['entry']:.8g}")
            print(f"SL:    {s['sl']:.8g}")
            print(f"TP1:   {s['tp1']:.8g} (1R)")
            print(f"TP2:   {s['tp2']:.8g} (2R)")
            print(f"TP3:   {s['tp3']:.8g} (3R)")
            print(f"1H={s['trend_1h']} | 4H={s['trend_4h']} | CISD={s['cisd']} | VOL={s['volume_ratio']:.2f}x\n")
        except Exception as exc:
            print(f"=== {inst} ===\nERROR: {exc}\n")


if __name__ == "__main__":
    main()
