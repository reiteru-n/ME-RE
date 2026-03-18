# セッション引き継ぎ指示

## リポジトリ情報

- **作業ディレクトリ**: `/home/user/Zandaka`
- **リモート origin**: Azure DevOps Menou.Agents
  - URL: `https://dev.azure.com/SystemAdministrator0272/Menou.Agents/_git/Menou.Agents`

## 禁止事項

- `reiteru-n/Zandaka` への push は絶対に行わない

## ブランチ構成

| ブランチ | 用途 |
|---|---|
| `master` | メインブランチ |
| `newService_mailApp` | メールアプリ新サービス用 |
| `claude/azure-devops-setup-Q3taa` | 現在の作業ブランチ |

## git 操作ルール

- push は必ず `git push -u origin <branch>` を Azure DevOps へ実行する
- `reiteru-n/Zandaka` への push は行わない

## 未解決の問題

- スマホUI上に `reiteru-n/Zandaka` と表示される（Claude Code システム側の問題）
  - 新セッションで変わるか要確認
