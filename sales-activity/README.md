# 営業アクション分析（menou scope 機能候補）

Slack と Outlook の送受信ログから営業メンバーごとの行動を集計し、
**アクション量の可視化 → 効率の比較 → 振り返りと改善** を週次で回すためのダッシュボードです。
menou scope に組み込むかを判断するためのプロトタイプとして、単体でも動く構成にしています。

```
sales-activity/
├── dashboard/index.html     ダッシュボード本体（1ファイル完結。サンプルデータ内蔵）
├── collector/
│   ├── slack_collector.py   Slack 投稿メタデータ → data/events/slack.jsonl
│   ├── outlook_collector.py Outlook メール・予定メタデータ（Graph）→ data/events/mail.jsonl
│   ├── aggregate.py         イベント → メンバー×日の集計 data/activity.json
│   └── common.py            設定・分類・HTTP 共通処理
├── schema/activity.schema.json  ダッシュボードが読む JSON の定義
├── config.example.json      設定例（コピーして config.json を作る）
└── tests/                   python -m unittest discover -s tests
```

Python 3.10 以上の標準ライブラリだけで動きます（追加パッケージ不要）。

## ダッシュボードで見られるもの

| 区画 | 目的 | 主な指標 |
|---|---|---|
| KPI帯 | 期間全体の量と質を一目で | 総アクション、接触顧客数、商談、提案・見積、平均初回返信、顧客向け比率（すべて前期間比つき） |
| 週次アクション量 | 量の推移 | 社外メール／Slack案件チャンネル／Slack社内 の積み上げ |
| 成果につながる行動 | 量が成果に変わっているか | 商談・提案・見積の週次推移 |
| 効率マップ | メンバー間の比較 | 横：週あたりアクション量、縦：100アクションあたりの成果、中央値で4象限 |
| 活動時間帯 | 働き方・時間外の偏り | 曜日×時刻のヒートマップ（平日9時・18時に境界線） |
| 行動ファネル | どこで止まっているか | 新規接触 → 商談 → 提案 → 見積 と前段比 |
| メンバー別スコアカード | 個人ごとの現状 | 商談目標の達成状況（達成／あと少し／要対策／要支援）、転換率、返信時間、時間外比率 |
| 今期の気づき | 改善点の自動抽出 | 前期間・チーム平均との比較から「次の一手」まで提示 |
| 振り返りメモ（KPT） | 1on1・チーム会 | Keep / Problem / Try を書き、Slack 投稿用テキストとしてコピー |

期間（4/8/12週）・チーム・メンバーで全体を絞り込めます。表の行をクリックしてもメンバーで絞り込めます。

## 実データで動かす

1. `cp config.example.json config.json` で設定を作り、営業メンバーのメール・チーム・週あたり商談目標を書く。
   `internal_domains` に自社ドメイン、`deal_channel_prefixes` に案件チャンネルの命名規則を入れる。
2. Slack：Bot を作成し、スコープ `channels:read channels:history groups:read groups:history users:read users:read.email` を付与。
   集計したい案件・営業チャンネルに Bot を招待する。
   ```
   SLACK_BOT_TOKEN=xoxb-... python collector/slack_collector.py --config config.json --since 2026-07-01
   ```
3. Outlook：Entra ID にアプリを登録し、アプリケーション権限 `Mail.ReadBasic.All`（本文なし）と
   `Calendars.ReadBasic.All` に管理者同意。**アプリケーションアクセスポリシーで対象を営業メンバーのメールボックスに限定**する。
   ```
   MS_TENANT_ID=... MS_CLIENT_ID=... MS_CLIENT_SECRET=... \
     python collector/outlook_collector.py --config config.json --since 2026-07-01
   ```
4. 集計：`python collector/aggregate.py --config config.json --out data/activity.json`
5. `dashboard/index.html` をブラウザで開き、「集計データを読み込む」で `data/activity.json` を選ぶ。
   ファイルはブラウザ内だけで処理され、外部には送信されません。

週次で回す場合は 2〜4 を cron や GitHub Actions / Azure Pipelines のスケジュール実行に載せます。

## 指標の定義

- **アクション** = 社外メール送信 + Slack 投稿（案件チャンネル + 社内）
- **社外メール** = 宛先・CC に `internal_domains` 以外のドメインを含む送信メール
- **案件チャンネル** = `deal_channel_prefixes` に一致するチャンネル、`channel_accounts` で指定したチャンネル、Slack コネクト（社外共有）チャンネル
- **商談** = 社外参加者を含む予定（予定表を使わない場合は件名・案件チャンネル投稿のキーワード）
- **提案・見積** = 社外メールの件名・案件チャンネル投稿のキーワード（`keywords` で調整可）。同じ日・同じ顧客・同じ種類は1件にまとめる
- **新規接触** = その顧客ドメインが初めて現れた日。収集開始から14日間は数えない（`--warmup-days`）
- **初回返信時間** = 顧客からの受信から同じスレッドで最初に送信するまで。3日で頭打ち（`--reply-cap`）
- **時間外** = 平日 9 時前・18 時以降と土日のアクション
- **顧客向け比率** = (社外メール + 案件チャンネル投稿) ÷ アクション

## プライバシーと運用の前提

- 収集するのは日時・相手先ドメイン・分類タグのみ。メール本文と Slack 投稿本文は保存しません（Slack 本文はキーワード判定にだけ使い破棄）。
- 顧客ドメインは集計時に SHA-256 でハッシュ化します（`ACCOUNT_HASH_SALT` 環境変数でソルト指定）。
- `data/` と `config.json` は `.gitignore` 済みです。実データ・トークンはコミットしないでください。
- 個人の行動データを扱うため、**人事評価の直接材料にはせず、本人と上長の振り返り・業務改善に使う**前提です。
  運用前に対象メンバーへ目的・取得項目・閲覧者を説明し、同意を得てください。閲覧権限は本人・上長・営業責任者に絞ることを推奨します。

## menou scope に組み込む場合の論点

| 論点 | プロトタイプでの扱い | 組み込み時の案 |
|---|---|---|
| データ取得 | ローカルでスクリプト実行 | menou scope のバックエンドに Slack App / Graph 連携を持たせ、日次バッチで集計 |
| 保存 | JSON ファイル | 集計テーブル（メンバー×日）だけを保存し、イベントは集計後に破棄 |
| 閲覧権限 | ファイルを持つ人が閲覧 | 本人は自分のみ、マネージャーは配下のみ、の行レベル権限 |
| 振り返りメモ | ブラウザ内の下書き＋コピー | menou scope 側に週次 KPT として保存し、前週の Try の実施状況を翌週に表示 |
| 成果データ | キーワードによる推定 | CRM（商談・受注）と突き合わせ、行動量と受注の相関を検証 |

まずは 1 チーム・4〜8 週で試し、「気づき」が 1on1 で実際に使われるか、キーワード判定の精度が十分かを確認してから組み込みを判断するのがおすすめです。
