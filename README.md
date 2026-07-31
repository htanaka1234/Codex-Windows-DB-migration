# Codex Windows DB Migration

Codex DesktopでWindowsネイティブエージェントとWSLエージェントを切り替えたときに発生する、`state_5.sqlite` のmigration checksum不一致と履歴表示の不整合を調査および復旧するための手順書とPythonスクリプトです。

## 対象範囲

このリポジトリは、次の作業を支援します。

- `state_5.sqlite` の `integrity_check` と `quick_check`
- Windows版とWSL版のmigration checksum不一致の調査
- rollout JSONLに保存された `cwd` と `thread_source` の正規化
- `state_5.sqlite` の履歴メタデータの正規化
- `session_index.jsonl` と `.codex-global-state.json` の再構築
- Windows側Codex homeからWSL側Codex homeへの履歴移植

自動修復ツールではありません。
`apply` を実行する前に、dry-runの結果とバックアップ先を確認する必要があります。

## 前提条件

- Python 3.10以降
- Codex DesktopのデータディレクトリへアクセスできるWindowsまたはWSL環境
- 書き込み操作時にCodex Desktopを完全終了できること

外部Pythonパッケージは使用しません。

## 最初に読む文書

復旧の判断基準、WindowsネイティブからWSLへの移行、WSLからWindowsネイティブへの移行、ロールバックは[復旧手順書](docs/codex-state5-recovery-runbook.md)に記載しています。

チェックサムだけを書き換える前に、同手順書のバックアップとクリーンDB生成の手順を確認してください。

## 読み取り専用の監査

WindowsのPowerShellでは、次のコマンドでWindows側Codex homeを監査します。

```powershell
$CodexHome = Join-Path $env:USERPROFILE ".codex"
py -3 scripts\current_history_visibility_audit.py `
  --target-style windows `
  --codex-home $CodexHome
```

WSLでは、`WINDOWS_USERNAME` をWindowsのユーザー名に置き換えて実行します。

```sh
CODEX_HOME=/mnt/c/Users/WINDOWS_USERNAME/.codex
python3 scripts/current_history_visibility_audit.py \
  --target-style wsl \
  --codex-home "$CODEX_HOME"
```

監査では、少なくとも次の結果を確認します。

- `integrity_check` が `ok`
- `quick_check` が `ok`
- DBから参照されるrollout JSONLが存在する
- 表示対象スレッドと `session_index.jsonl` のIDが一致する

SQLiteの整合性検査が `ok` でない場合は、checksum切り替え手順を続けません。

## Windows形式へのdry-run

WSLからWindowsネイティブエージェントへ切り替える前に、PowerShellから更新対象を確認できます。

```powershell
$CodexHome = Join-Path $env:USERPROFILE ".codex"
$BackupRoot = "C:\path\to\codex-backups"

py -3 scripts\repair_rollout_session_meta.py dry-run `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot

py -3 scripts\normalize_history_cwd.py dry-run `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot

py -3 scripts\repair_ui_indexes.py dry-run `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot
```

`apply` の実行順序は、[WSLからWindowsネイティブへ切り替える場合](docs/codex-state5-recovery-runbook.md#wslからwindowsネイティブへ切り替える場合)に従います。

## `--backup-root` の役割

`--backup-root` は、`apply` が変更前のファイルやSQLiteスナップショットを保存する出力先です。
復旧対象を検索する入力ディレクトリではありません。
`dry-run` は、このディレクトリへファイルを書き込みません。

実行前に、Codex homeの外側へ書き込み可能な専用ディレクトリを作成してください。
Codex home自身やその配下を指定しないでください。

```powershell
$BackupRoot = "C:\CodexBackups\wsl-to-windows"
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
```

```sh
BACKUP_ROOT=/mnt/c/CodexBackups/wsl-to-windows
mkdir -p "$BACKUP_ROOT"
```

保存されるデータは、実行するスクリプトによって異なります。

| スクリプト | `apply` が保存する主なデータ |
| --- | --- |
| `repair_rollout_session_meta.py` | 変更対象となるrollout JSONLの原本 |
| `normalize_history_cwd.py` | 更新直前の `state_5.sqlite` の一貫したスナップショット |
| `repair_ui_indexes.py` | 更新前の `session_index.jsonl` と `.codex-global-state.json` |
| `migrate_windows_codex_home_to_wsl.py` | 変更前の移行先DB、sidecar、UI補助状態 |

バックアップには会話内容、スレッド名、ローカルパスなどが含まれる場合があります。
Codex homeと同等の機密データとして扱い、Gitへ追加したり不用意に共有したりしないでください。
スクリプトはバックアップを自動復元、自動削除、自動ローテーションしません。

ファイル名、復元方法、保持期間、切り替え前に手動作成する一式バックアップとの違いは、[バックアップルートの役割](docs/codex-state5-recovery-runbook.md#バックアップルートの役割)を参照してください。

## 主要スクリプト

| スクリプト | 役割 |
| --- | --- |
| `current_history_visibility_audit.py` | DB、rollout JSONL、UIインデックスの現在状態を監査する |
| `repair_rollout_session_meta.py` | rollout JSONLの `cwd` と `thread_source` を正規化する |
| `normalize_history_cwd.py` | DB内の履歴メタデータをWindows形式またはWSL形式へ揃える |
| `repair_ui_indexes.py` | `session_index.jsonl` とグローバルUI状態を再構築する |
| `migrate_windows_codex_home_to_wsl.py` | Windows側からWSL側へセッションと履歴行を移植する |
| `repair_state5.py` | SQLiteの検査と限定的な復旧操作を行う |
| `scan_codex_app.py` | Codexバイナリ内のmigration情報を調査する |

その他の `scripts/` 配下のファイルは、特定の不整合を調査するための補助スクリプトです。

## 環境変数

一部のスクリプトは、明示引数がない場合に次の環境変数を参照します。

| 変数 | 用途 |
| --- | --- |
| `CODEX_HOME` | Codex home |
| `CODEX_STATE_DB` | `state_5.sqlite` の完全パス |
| `CODEX_LOGS_DB` | `logs_2.sqlite` の完全パス |
| `CODEX_RECOVERY_WORKDIR` | バックアップと復旧作業の出力先 |
| `CODEX_WINDOWS_HOME` | Windows側Codex home |
| `CODEX_WSL_HOME` | WSL側Codex home |

パスを誤認しないため、復旧操作では環境変数だけに依存せず、可能な限り `--codex-home`、`--source-home`、`--dest-home`、`--backup-root` を明示してください。

## 安全上の注意

- Codexの稼働中に `state_5.sqlite`、WAL、SHMを移動または置換しないでください。
- `_sqlx_migrations.checksum` を推測で更新しないでください。
- `apply` の前にCodex home、`sessions`、DB、WAL、SHM、UI補助状態を退避してください。
- Windows版とWSL版で単一の `state_5.sqlite` を共有すると、改行コード差によるchecksum不一致が再発する場合があります。
- SQLite、JSONL、Codexのグローバル状態、復旧バックアップはGitへ追加しないでください。

## リポジトリ構成

```text
.
├── README.md
├── docs/
│   └── codex-state5-recovery-runbook.md
└── scripts/
    └── *.py
```

## ライセンス

このリポジトリにはライセンスが指定されていません。
再利用条件を明示する場合は、用途に合うライセンスを選択して `LICENSE` を追加してください。
