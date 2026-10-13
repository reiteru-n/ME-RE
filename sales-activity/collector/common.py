"""収集スクリプト共通の設定読み込み・分類・イベント出力。

イベントは1行1JSONで data/events/<source>.jsonl に書き出す。
本文は保存せず、日時・種別・相手先ドメイン・分類タグだけを残す。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data"

# 件名・投稿から成果系の行動を判定するキーワード（config の keywords で上書き可）
DEFAULT_KEYWORDS = {
    "meeting": ["商談", "打ち合わせ", "お打合せ", "打合せ", "アポ", "ミーティング", "面談", "訪問", "デモ"],
    "proposal": ["提案", "ご提案", "提案書", "proposal"],
    "quote": ["見積", "御見積", "お見積", "quotation", "quote"],
}


@dataclass
class Member:
    id: str
    name: str
    team: str
    email: str
    slack_id: str | None = None
    target: int = 5


@dataclass
class Config:
    members: list[Member]
    internal_domains: set[str]
    timezone: ZoneInfo
    deal_channel_prefixes: list[str] = field(default_factory=lambda: ["deal-", "案件-", "cust-"])
    channel_accounts: dict[str, str] = field(default_factory=dict)
    ignore_domains: set[str] = field(default_factory=set)
    keywords: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_KEYWORDS))
    data_dir: Path = DEFAULT_DATA_DIR

    def member_by_email(self, email: str | None) -> Member | None:
        if not email:
            return None
        e = email.lower()
        return next((m for m in self.members if m.email.lower() == e), None)

    def member_by_slack(self, uid: str | None) -> Member | None:
        return next((m for m in self.members if m.slack_id and m.slack_id == uid), None)

    def is_external(self, email_or_domain: str) -> bool:
        d = domain_of(email_or_domain)
        return bool(d) and d not in self.internal_domains and not any(d.endswith("." + i) for i in self.internal_domains)


def load_config(path: str | os.PathLike) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    members = [
        Member(
            id=str(m["id"]),
            name=m.get("name", m["id"]),
            team=m.get("team", "未設定"),
            email=m["email"],
            slack_id=m.get("slack_id"),
            target=int(m.get("target", 5)),
        )
        for m in raw["members"]
    ]
    kw = dict(DEFAULT_KEYWORDS)
    kw.update(raw.get("keywords", {}))
    data_dir = Path(raw["data_dir"]) if raw.get("data_dir") else DEFAULT_DATA_DIR
    if not data_dir.is_absolute():
        data_dir = (Path(path).resolve().parent / data_dir).resolve()
    return Config(
        members=members,
        internal_domains={d.lower() for d in raw["internal_domains"]},
        timezone=ZoneInfo(raw.get("timezone", "Asia/Tokyo")),
        deal_channel_prefixes=raw.get("deal_channel_prefixes", ["deal-", "案件-", "cust-"]),
        channel_accounts=raw.get("channel_accounts", {}),
        ignore_domains={d.lower() for d in raw.get("ignore_domains", [])},
        keywords=kw,
        data_dir=data_dir,
    )


def domain_of(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    return s.rsplit("@", 1)[-1] if "@" in s else s


def classify(text: str | None, keywords: dict[str, list[str]]) -> list[str]:
    """件名や投稿本文から meeting / proposal / quote のタグを返す（本文自体は保存しない）。"""
    if not text:
        return []
    t = text.lower()
    return [tag for tag, words in keywords.items() if any(w.lower() in t for w in words)]


def parse_since(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def write_events(cfg: Config, source: str, events: list[dict]) -> Path:
    out_dir = cfg.data_dir / "events"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{source}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def http_json(url: str, *, headers: dict[str, str] | None = None, data: dict | None = None, form: bool = False, retries: int = 5) -> dict:
    """429 / 5xx は Retry-After か指数バックオフで再試行する。"""
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = json.dumps(data).encode()
            hdrs.setdefault("Content-Type", "application/json")
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = float(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"request failed after {retries} attempts: {url}")
