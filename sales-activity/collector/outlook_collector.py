"""Outlook（Microsoft Graph）のメール・予定のメタデータを収集して data/events/mail.jsonl に書き出す。

Entra ID のアプリ登録（クライアント資格情報）で、次のアプリケーション権限に管理者同意が必要:
    Mail.ReadBasic.All   … 本文を含まないメールのメタデータ
    Calendars.ReadBasic.All … 社外参加者を含む予定を商談として数える（--no-calendar で省略可）
対象メールボックスはアプリケーションアクセスポリシーで営業メンバーに限定すること。

資格情報は環境変数で渡す: MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET

使い方:
    python collector/outlook_collector.py --config config.json --since 2026-07-01
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

from common import Config, Member, classify, domain_of, http_json, load_config, parse_since, write_events

GRAPH = "https://graph.microsoft.com/v1.0"


def get_token() -> str:
    tenant, cid, secret = (os.environ.get(k) for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET"))
    if not all((tenant, cid, secret)):
        sys.exit("MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET を環境変数に設定してください。")
    res = http_json(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"client_id": cid, "client_secret": secret, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
        form=True,
    )
    return res["access_token"]


def pages(token: str, url: str, extra_headers: dict[str, str] | None = None):
    headers = {"Authorization": f"Bearer {token}", **(extra_headers or {})}
    while url:
        res = http_json(url, headers=headers)
        yield from res.get("value", [])
        url = res.get("@odata.nextLink")


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def external_domains(cfg: Config, addrs: list[str]) -> list[str]:
    seen: list[str] = []
    for a in addrs:
        d = domain_of(a)
        if d and cfg.is_external(d) and d not in cfg.ignore_domains and d not in seen:
            seen.append(d)
    return seen


def addresses(recips: list[dict] | None) -> list[str]:
    return [((r or {}).get("emailAddress") or {}).get("address", "") for r in recips or []]


def collect_member(cfg: Config, token: str, m: Member, since: datetime, calendar: bool) -> list[dict]:
    user = urllib.parse.quote(m.email)
    iso_since = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events: list[dict] = []

    sent = list(pages(token, f"{GRAPH}/users/{user}/mailFolders/sentitems/messages?" + urllib.parse.urlencode({
        "$filter": f"sentDateTime ge {iso_since}",
        "$select": "sentDateTime,toRecipients,ccRecipients,subject,conversationId",
        "$top": "100",
    })))
    inbox = list(pages(token, f"{GRAPH}/users/{user}/mailFolders/inbox/messages?" + urllib.parse.urlencode({
        "$filter": f"receivedDateTime ge {iso_since}",
        "$select": "receivedDateTime,from,subject,conversationId",
        "$top": "100",
    })))

    # 同じスレッドで自分が送った時刻（返信時間の計算用）
    sent_by_conv: dict[str, list[datetime]] = {}
    for msg in sent:
        doms = external_domains(cfg, addresses(msg.get("toRecipients")) + addresses(msg.get("ccRecipients")))
        t = ts(msg["sentDateTime"])
        sent_by_conv.setdefault(msg.get("conversationId") or "", []).append(t)
        if not doms:
            continue
        events.append({"source": "mail", "member": m.id, "ts": t.isoformat(), "kind": "mail_out", "account": doms[0],
                       "tags": [x for x in classify(msg.get("subject"), cfg.keywords) if x != "meeting" or not calendar]})
    for v in sent_by_conv.values():
        v.sort()

    for msg in inbox:
        frm = ((msg.get("from") or {}).get("emailAddress") or {}).get("address", "")
        doms = external_domains(cfg, [frm])
        if not doms:
            continue
        t = ts(msg["receivedDateTime"])
        reply = next((s for s in sent_by_conv.get(msg.get("conversationId") or "", []) if s > t), None)
        events.append({"source": "mail", "member": m.id, "ts": t.isoformat(), "kind": "mail_in", "account": doms[0], "tags": [],
                       "reply_minutes": round((reply - t).total_seconds() / 60) if reply else None})

    if calendar:
        end = datetime.now(timezone.utc) + timedelta(days=1)
        for ev in pages(token, f"{GRAPH}/users/{user}/calendarView?" + urllib.parse.urlencode({
            "startDateTime": iso_since, "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$select": "start,attendees,isCancelled,showAs", "$top": "100",
        }), extra_headers={"Prefer": 'outlook.timezone="UTC"'}):
            if ev.get("isCancelled"):
                continue
            doms = external_domains(cfg, addresses(ev.get("attendees")))
            if not doms:
                continue
            start = ts(ev["start"]["dateTime"].split(".")[0] + "+00:00")
            events.append({"source": "calendar", "member": m.id, "ts": start.isoformat(), "kind": "meeting", "account": doms[0], "tags": ["meeting"]})
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--since", required=True, help="収集開始日 (例: 2026-07-01)")
    ap.add_argument("--no-calendar", action="store_true", help="予定表を使わず件名キーワードで商談を判定する")
    args = ap.parse_args()
    cfg = load_config(args.config)
    token = get_token()
    since = parse_since(args.since)
    events: list[dict] = []
    for m in cfg.members:
        try:
            got = collect_member(cfg, token, m, since, calendar=not args.no_calendar)
        except Exception as e:  # 1人分の失敗で全体を止めない
            print(f"warning: {m.name} <{m.email}> の取得に失敗: {e}", file=sys.stderr)
            continue
        events.extend(got)
        print(f"{m.name}: {len(got)} 件")
    path = write_events(cfg, "mail", events)
    print(f"{len(events)} 件のメール・予定イベントを {path} に書き出しました。")


if __name__ == "__main__":
    main()
