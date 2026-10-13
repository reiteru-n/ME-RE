import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

from aggregate import aggregate  # noqa: E402
from common import DEFAULT_KEYWORDS, classify, load_config  # noqa: E402
from slack_collector import channel_account  # noqa: E402


def make_config(tmp: Path):
    cfg = {
        "internal_domains": ["menou.co.jp"],
        "members": [{"id": "m1", "name": "テスト 一郎", "team": "A", "email": "ichiro@menou.co.jp", "target": 4}],
    }
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return load_config(p)


class ClassifyTest(unittest.TestCase):
    def test_tags_from_subject(self):
        self.assertEqual(classify("【御見積】年間ライセンスのご提案", DEFAULT_KEYWORDS), ["proposal", "quote"])
        self.assertEqual(classify("来週のお打合せ日程", DEFAULT_KEYWORDS), ["meeting"])
        self.assertEqual(classify(None, DEFAULT_KEYWORDS), [])


class ConfigTest(unittest.TestCase):
    def test_external_domain(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_config(Path(d))
        self.assertFalse(cfg.is_external("someone@menou.co.jp"))
        self.assertFalse(cfg.is_external("x@sub.menou.co.jp"))
        self.assertTrue(cfg.is_external("buyer@example.co.jp"))

    def test_deal_channel(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_config(Path(d))
        self.assertEqual(channel_account(cfg, {"name": "deal-acme"}), "acme")
        self.assertEqual(channel_account(cfg, {"name": "shared", "is_ext_shared": True}), "slack-connect:shared")
        self.assertIsNone(channel_account(cfg, {"name": "general"}))


class AggregateTest(unittest.TestCase):
    def test_daily_rollup(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_config(Path(d))
        ev = [
            {"member": "m1", "ts": "2026-10-01T00:30:00+00:00", "kind": "mail_in", "account": "acme.co.jp", "tags": [], "reply_minutes": 45},
            {"member": "m1", "ts": "2026-10-01T01:15:00+00:00", "kind": "mail_out", "account": "acme.co.jp", "tags": ["proposal"]},
            {"member": "m1", "ts": "2026-10-01T02:00:00+00:00", "kind": "slack_deal", "account": "acme", "tags": ["proposal"]},
            {"member": "m1", "ts": "2026-10-01T02:00:00+00:00", "kind": "mail_out", "account": "acme.co.jp", "tags": ["proposal"]},
            {"member": "m1", "ts": "2026-10-01T10:00:00+00:00", "kind": "slack_other", "account": None, "tags": []},
            {"member": "m1", "ts": "2026-10-01T05:00:00+00:00", "kind": "meeting", "account": "acme.co.jp", "tags": ["meeting"]},
            {"member": "zz", "ts": "2026-10-01T05:00:00+00:00", "kind": "mail_out", "account": "x.jp", "tags": []},
            {"member": "m1", "ts": "2026-10-20T01:00:00+00:00", "kind": "mail_out", "account": "newco.jp", "tags": []},
        ]
        out = aggregate(cfg, ev, hash_accounts=False)
        self.assertEqual(out["members"], [{"id": "m1", "name": "テスト 一郎", "team": "A", "target": 4}])
        day1, day2 = out["daily"]
        self.assertEqual(day1["date"], "2026-10-01")
        self.assertEqual((day1["mail_out"], day1["mail_in"], day1["slack_deal"], day1["slack_other"]), (2, 1, 1, 1))
        # 同じ顧客・同じ日の提案はメールでもSlackでも別顧客キーなので2件、メール同士は1件にまとめる
        self.assertEqual(day1["proposals"], 2)
        self.assertEqual(day1["meetings"], 1)
        self.assertEqual((day1["reply_n"], day1["reply_min"]), (1, 45))
        # JST に変換して時台を数える（10:15, 11:00, 19:00）
        self.assertEqual(day1["hours"][10] + day1["hours"][11] + day1["hours"][19], 4)
        self.assertEqual(day1["hours"][19], 1)
        self.assertEqual(day1["new_contacts"], 0)  # warmup 期間中
        self.assertEqual(day2["new_contacts"], 1)
        self.assertEqual(day1["accounts"], ["acme", "acme.co.jp"])

    def test_accounts_hashed_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_config(Path(d))
        out = aggregate(cfg, [{"member": "m1", "ts": "2026-10-01T01:00:00+00:00", "kind": "mail_out", "account": "acme.co.jp", "tags": []}])
        self.assertNotIn("acme.co.jp", json.dumps(out))
        self.assertEqual(len(out["daily"][0]["accounts"][0]), 12)


if __name__ == "__main__":
    unittest.main()
