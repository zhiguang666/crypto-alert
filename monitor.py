# -*- coding: utf-8 -*-
"""
加密货币异动监测（GitHub Actions 云端版）

由 GitHub Actions 定时触发（默认每 5 分钟），检测主流币涨跌异动，
异动时通过 PushPlus 发送微信消息。无需本地电脑，部署后云端自动运行。

依赖：仅用 Python 标准库，无需 pip install。
状态：state.json 记录上次价格与推送时间（用于计算短期波动 + 冷却去重）。
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

# ---- 配置 ----
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
ALERT_24H = 50.0      # 24小时涨跌幅阈值 %
ALERT_5M = 2.0        # 5分钟波动阈值 %
COOLDOWN_MINUTES = 5  # 同一币种推送冷却（分钟）

TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

COIN_NAMES = {
    "BTCUSDT": "比特币", "ETHUSDT": "以太坊", "BNBUSDT": "币安币", "SOLUSDT": "Solana",
    "XRPUSDT": "瑞波", "DOGEUSDT": "狗狗币", "ADAUSDT": "艾达币", "LINKUSDT": "Chainlink",
    "AVAXUSDT": "Avalanche", "DOTUSDT": "波卡", "TRXUSDT": "波场", "TONUSDT": "Toncoin",
}

CN_TZ = timezone(timedelta(hours=8))


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8")


def fetch_tickers() -> list:
    url = "https://data-api.binance.vision/api/v3/ticker/24hr?symbols=" + json.dumps(COINS)
    return json.loads(http_get(url))


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_price": {}, "last_alert": {}}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def push_wechat(title: str, content: str) -> dict:
    if not TOKEN:
        print("[提示] 未配置 PUSHPLUS_TOKEN，跳过推送")
        return {"code": -1}
    data = json.dumps({
        "token": TOKEN, "title": title, "content": content, "template": "html",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://www.pushplus.plus/send", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    if p >= 0.01:
        return f"{p:.4f}"
    return f"{p:.6f}"


def fmt_pct(p: float) -> str:
    return ("+" if p >= 0 else "") + f"{p:.2f}%"


def main() -> None:
    state = load_state()
    first_run = not state.get("last_price")
    now = time.time()

    tickers = fetch_tickers()
    print(f"[{datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')}] 获取 {len(tickers)} 个币种行情")

    for t in tickers:
        sym = t["symbol"]
        if sym not in COINS:
            continue
        price = float(t["lastPrice"])
        open_p = float(t["openPrice"])
        high = float(t["highPrice"])
        low = float(t["lowPrice"])
        pct24h = (price - open_p) / open_p * 100 if open_p else 0.0

        # 5 分钟波动（对比上次记录的价格）
        last_p = state["last_price"].get(sym)
        pct5m = (price - last_p) / last_p * 100 if last_p else None

        # 判定异动
        triggers = []
        if abs(pct24h) >= ALERT_24H:
            triggers.append(("24小时", pct24h))
        if pct5m is not None and abs(pct5m) >= ALERT_5M:
            triggers.append(("5分钟", pct5m))

        # 更新价格快照（无论是否推送都要更新）
        state["last_price"][sym] = price

        if not triggers:
            continue

        triggers.sort(key=lambda x: abs(x[1]), reverse=True)
        label, pct = triggers[0]
        up = pct >= 0
        name = COIN_NAMES.get(sym, sym)

        # 首次运行：只初始化冷却时间戳，不推送（避免把历史异动全推一遍）
        if first_run:
            state["last_alert"][sym] = now
            continue

        # 冷却检查
        last_alert = state["last_alert"].get(sym, 0)
        if now - last_alert < COOLDOWN_MINUTES * 60:
            continue
        state["last_alert"][sym] = now

        # 推送
        print(f"  异动: {name}({sym}) {('拉升' if up else '跳水')} {label} {fmt_pct(pct)} 现价 ${fmt_price(price)}")
        title = f"{'🚨' if up else '⚠️'} {name} {('拉升' if up else '跳水')} {fmt_pct(pct)}"
        content = (
            f"<h3>{'📈' if up else '📉'} {name}（{sym}）{'拉升' if up else '跳水'}</h3>"
            f"<p>异动幅度：<b>{fmt_pct(pct)}</b>（{label}）</p>"
            f"<p>当前价格：<b>${fmt_price(price)}</b></p>"
            f"<p>24小时涨跌：{fmt_pct(pct24h)}</p>"
            f"<p>24h 最高/最低：${fmt_price(high)} / ${fmt_price(low)}</p>"
            f"<p style='color:#999'>时间：{datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')}</p>"
        )
        res = push_wechat(title, content)
        print(f"  推送结果: {res.get('code')} {res.get('msg') or ''}")

    save_state(state)
    print("完成一轮监测")


if __name__ == "__main__":
    main()
