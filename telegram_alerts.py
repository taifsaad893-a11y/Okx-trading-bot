import json
import os
import requests
from scanner import scan_all

TOKEN=os.environ["TELEGRAM_BOT_TOKEN"]
CHAT=os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE="signal_state.json"

def load_state():
    try:
        with open(STATE_FILE, "r") as f: return json.load(f)
    except Exception: return {"sent": []}

def save_state(st):
    with open(STATE_FILE, "w") as f: json.dump(st, f)

def send(text):
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id":CHAT,"text":text}, timeout=20)
    r.raise_for_status()
    if not r.json().get("ok"): raise RuntimeError("Telegram send failed")

def main():
    st=load_state(); sent=set(st.get("sent", [])); changed=False
    for s in scan_all():
        sid=s["setup_id"]
        if sid in sent: continue
        text=(f"🚨 صفقة جديدة\n\n"
              f"الاستراتيجية: {s['strategy']}\n"
              f"الرمز: {s['symbol']} | الفريم: {s['timeframe']}\n"
              f"الاتجاه: {s['side']}\n\n"
              f"Entry: {s['entry']:.5f}\nSL: {s['sl']:.5f}\n"
              f"TP1: {s['tp1']:.5f} (1R)\nTP2: {s['tp2']:.5f} (2R)\nTP3: {s['tp3']:.5f} (3R)\n"
              f"R:R المستهدف: 1:{s['rr']:.0f}\n\nID: {sid}\n⚠️ إشارة فقط — لا يتم تنفيذ أي أمر تلقائياً.")
        send(text); sent.add(sid); changed=True; print(f"Alert sent: {sid}")
    if changed:
        st["sent"]=list(sent)[-300:]; save_state(st)
    else: print("No new alert")

if __name__ == "__main__": main()
