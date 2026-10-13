"""data/events/*.jsonl をメンバー×日に集計し、ダッシュボードが読む activity.json を作る。

集計ルール（ダッシュボードの「指標の定義」と対応）:
- アクション = mail_out + slack_deal + slack_other。hours はこの3種を現地時刻の時台ごとに数える。
- 商談・提案・見積 = タグ付きイベントを (メンバー, 日, 顧客, タグ) で重複排除した件数。
  予定表の社外参加者付き予定、件名・案件チャンネル投稿のキーワードが元になる。
- 新規接触 = その顧客が全イベントの中で初めて現れた日（収集開始から --warmup-days 日間は数えない）。
- 返信時間 = 顧客からの受信から同スレッドの初回送信まで。--reply-cap 分を上限に丸める。
- accounts は既定で SHA-256 のハッシュに置き換える（顧客名をファイルに残さない）。

使い方:
    python collector/aggregate.py --config config.json --out data/activity.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import Config, load_config

ACTION_KINDS = {"mail_out", "slack_deal", "slack_other"}
OUTCOME_TAGS = {"meeting": "meetings", "proposal": "proposals", "quote": "quotes"}
FIELDS = ["mail_out", "mail_in", "slack_deal", "slack_other", "meetings", "proposals", "quotes", "new_contacts", "reply_n", "reply_min"]


def read_events(events_dir: Path) -> list[dict]:
    out: list[dict] = []
    for p in sorted(events_dir.glob("*.jsonl")):
        with p.open(encoding="utf-8") as f:
            out.extend(json.loads(line) for line in f if line.strip())
    return out


def aggregate(cfg: Config, events: list[dict], *, warmup_days: int = 14, reply_cap: int = 4320, hash_accounts: bool = True, salt: str = "") -> dict:
    known = {m.id for m in cfg.members}
    tz = cfg.timezone
    for e in events:
        e["_t"] = datetime.fromisoformat(e["ts"]).astimezone(tz)
    events = sorted((e for e in events if e.get("member") in known), key=lambda e: e["_t"])

    def acc_key(a: str) -> str:
        return hashlib.sha256((salt + a).encode()).hexdigest()[:12] if hash_accounts else a

    days: dict[tuple[str, str], dict] = defaultdict(lambda: {**{f: 0 for f in FIELDS}, "accounts": set(), "hours": [0] * 24})
    seen_outcomes: set[tuple] = set()
    first_seen: dict[str, datetime] = {}
    start = events[0]["_t"] if events else None

    for e in events:
        t = e["_t"]
        date = t.date().isoformat()
        d = days[(e["member"], date)]
        kind = e.get("kind")
        account = e.get("account")
        if kind in ACTION_KINDS:
            d[kind] += 1
            d["hours"][t.hour] += 1
        elif kind == "mail_in":
            d["mail_in"] += 1
            if e.get("reply_minutes") is not None:
                d["reply_n"] += 1
                d["reply_min"] += min(int(e["reply_minutes"]), reply_cap)
        if account:
            d["accounts"].add(acc_key(account))
            if account not in first_seen:
                first_seen[account] = t
                if kind in ACTION_KINDS | {"meeting"} and start and t - start >= timedelta(days=warmup_days):
                    d["new_contacts"] += 1
        for tag in e.get("tags") or []:
            field = OUTCOME_TAGS.get(tag)
            if not field:
                continue
            key = (e["member"], date, account, tag)
            if key in seen_outcomes:
                continue
            seen_outcomes.add(key)
            d[field] += 1

    daily = [
        {"member": mid, "date": date, **{f: v[f] for f in FIELDS}, "accounts": sorted(v["accounts"]), "hours": v["hours"]}
        for (mid, date), v in sorted(days.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    ]
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).astimezone(tz).isoformat(timespec="seconds"),
        "timezone": str(tz),
        "members": [{"id": m.id, "name": m.name, "team": m.team, "target": m.target} for m in cfg.members],
        "daily": daily,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", help="出力先（既定: <data_dir>/activity.json）")
    ap.add_argument("--warmup-days", type=int, default=14)
    ap.add_argument("--reply-cap", type=int, default=4320, help="返信時間の上限（分）。既定は3日")
    ap.add_argument("--plain-accounts", action="store_true", help="顧客ドメインをハッシュせずに出力する")
    args = ap.parse_args()
    cfg = load_config(args.config)
    events = read_events(cfg.data_dir / "events")
    result = aggregate(cfg, events, warmup_days=args.warmup_days, reply_cap=args.reply_cap,
                       hash_accounts=not args.plain_accounts, salt=os.environ.get("ACCOUNT_HASH_SALT", ""))
    out = Path(args.out) if args.out else cfg.data_dir / "activity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"{len(events)} 件のイベントから {len(result['daily'])} 行を集計し、{out} に書き出しました。")


if __name__ == "__main__":
    main()
