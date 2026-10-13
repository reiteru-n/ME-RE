"""Slack の投稿メタデータを収集して data/events/slack.jsonl に書き出す。

必要なスコープ（Bot Token）: channels:read, channels:history, groups:read, groups:history,
users:read, users:read.email
トークンは環境変数 SLACK_BOT_TOKEN で渡す（config やリポジトリには書かない）。
Bot が参加しているチャンネルだけが対象になる。

使い方:
    SLACK_BOT_TOKEN=xoxb-... python collector/slack_collector.py --config config.json --since 2026-07-01
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from datetime import datetime, timezone

from common import Config, classify, http_json, load_config, parse_since, write_events

API = "https://slack.com/api/"


def call(token: str, method: str, **params) -> dict:
    url = API + method + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    res = http_json(url, headers={"Authorization": f"Bearer {token}"})
    if not res.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {res.get('error')}")
    return res


def paginate(token: str, method: str, key: str, **params):
    cursor = None
    while True:
        res = call(token, method, cursor=cursor, limit=200, **params)
        yield from res.get(key, [])
        cursor = (res.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return


def resolve_slack_ids(cfg: Config, token: str) -> None:
    """config に slack_id が無いメンバーをメールアドレスで照合する。"""
    missing = {m.email.lower(): m for m in cfg.members if not m.slack_id}
    if not missing:
        return
    for u in paginate(token, "users.list", "members"):
        email = ((u.get("profile") or {}).get("email") or "").lower()
        if email in missing:
            missing[email].slack_id = u["id"]
    for m in cfg.members:
        if not m.slack_id:
            print(f"warning: Slack ユーザーが見つかりません: {m.name} <{m.email}>", file=sys.stderr)


def channel_account(cfg: Config, ch: dict) -> str | None:
    """案件チャンネルなら顧客キーを返す。Slack Connect（社外共有）も案件扱い。"""
    name = ch.get("name", "")
    if name in cfg.channel_accounts:
        return cfg.channel_accounts[name]
    for p in cfg.deal_channel_prefixes:
        if name.startswith(p):
            return name[len(p):] or name
    if ch.get("is_ext_shared"):
        return f"slack-connect:{name}"
    return None


def collect(cfg: Config, token: str, since: datetime) -> list[dict]:
    resolve_slack_ids(cfg, token)
    by_id = {m.slack_id: m for m in cfg.members if m.slack_id}
    events: list[dict] = []
    channels = [c for c in paginate(token, "conversations.list", "channels", types="public_channel,private_channel", exclude_archived="true") if c.get("is_member")]
    for ch in channels:
        account = channel_account(cfg, ch)
        for msg in paginate(token, "conversations.history", "messages", channel=ch["id"], oldest=f"{since.timestamp():.6f}"):
            m = by_id.get(msg.get("user"))
            if not m or msg.get("subtype") in {"channel_join", "channel_leave", "bot_message"}:
                continue
            ts = datetime.fromtimestamp(float(msg["ts"]), tz=timezone.utc)
            events.append({
                "source": "slack",
                "member": m.id,
                "ts": ts.isoformat(),
                "kind": "slack_deal" if account else "slack_other",
                "account": account,
                "tags": classify(msg.get("text"), cfg.keywords) if account else [],
            })
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--since", required=True, help="収集開始日 (例: 2026-07-01)")
    args = ap.parse_args()
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        sys.exit("SLACK_BOT_TOKEN を環境変数に設定してください。")
    cfg = load_config(args.config)
    events = collect(cfg, token, parse_since(args.since))
    path = write_events(cfg, "slack", events)
    print(f"{len(events)} 件の Slack イベントを {path} に書き出しました。")


if __name__ == "__main__":
    main()
