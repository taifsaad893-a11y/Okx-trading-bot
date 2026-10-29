import os
import requests

from scanner import INSTRUMENTS, get_closed_candles, taif_signal

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")


def main() -> None:
    found = False
    for inst_id in INSTRUMENTS:
        c15 = get_closed_candles(inst_id, "15m")
        c1h = get_closed_candles(inst_id, "1H")
        c4h = get_closed_candles(inst_id, "4H")
        signal = taif_signal(c15, c1h, c4h)

        if not signal or signal["grade"] != "A":
            continue

        found = True
        text = (
            f"🚨 OKX DEMO — A SETUP\n\n"
            f"Symbol: {inst_id}\n"
            f"Direction: {signal['side']}\n"
            f"Setup: {signal['setup']}\n"
            f"Score: {signal['score']}\n\n"
            f"Entry: {signal['entry']:.8g}\n"
            f"SL: {signal['sl']:.8g}\n"
            f"TP1: {signal['tp1']:.8g}\n"
            f"TP2: {signal['tp2']:.8g}\n"
            f"TP3: {signal['tp3']:.8g}\n\n"
            f"1H: {signal['trend_1h']} | 4H: {signal['trend_4h']}\n"
            f"CISD: {signal['cisd']} | VOL: {signal['volume_ratio']:.2f}x\n\n"
            f"⚠️ Signal only — no order was placed."
        )
        send_message(text)
        print(f"Telegram alert sent: {inst_id}")

    if not found:
        print("No A-grade setup. No Telegram alert sent.")


if __name__ == "__main__":
    main()
