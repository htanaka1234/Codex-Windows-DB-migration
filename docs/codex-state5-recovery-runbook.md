# Codex `state_5.sqlite` 復旧手順書

この手順書は、Codex Desktop の `state_5.sqlite` checksum / migration
不整合と、その後の「DB上には履歴があるのにUIにセッション履歴が出ない」
問題を復旧するための一般化手順です。

`state_5.sqlite` だけを直しても復旧は完了しません。Codex は
`%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl` を再スキャンして
`state_5.sqlite` のスレッドメタデータを再構築または補修します。そのため、
JSONL 側の `session_meta` が古い形式のままだと、DBを直しても再起動後に
古い値へ戻されます。

また、Codex app のエージェントを Windows ネイティブから WSL へ切り替えた
場合は、UI が履歴一覧を問い合わせる `cwd` フィルタも Windows 形式
`C:\...` から WSL 形式 `/mnt/c/...` へ変わります。`thread/list` は `cwd`
の完全一致で絞り込むため、復旧済み履歴であっても3層のパス表記が
Windows形式のままだと、WSLエージェント時のUIには表示されません。

## パス表記

この手順書では、環境依存のパスを以下のように表記します。

```text
%CODEX_HOME%        = %USERPROFILE%\.codex
%RECOVERY_WORKDIR%  = 復旧作業用ディレクトリ
%WORKSPACE_ROOT%    = 対象プロジェクトのWindows側ワークスペースルート
%PROJECT_NAME%      = 対象プロジェクト名
%TARGET_STYLE%      = windows または wsl
```

例:

```text
%CODEX_HOME%\state_5.sqlite
%RECOVERY_WORKDIR%\.tmp\current_history_visibility_audit.py
%WORKSPACE_ROOT%\%PROJECT_NAME%
```

`%RECOVERY_WORKDIR%` は、バックアップや補助スクリプトを置く任意の安全な
作業ディレクトリです。

エージェント別の推奨 `TARGET_STYLE`:

- Windows ネイティブエージェント: `windows`
- WSL エージェント: `wsl`

`wsl` の場合、`C:\src\project` や `\\?\C:\src\project` は
`/mnt/c/src/project` に正規化します。`windows` の場合、DB内の `threads.cwd`
は `\\?\C:\...`、rollout JSONL と UI補助状態は `C:\...` へ正規化します。

WSL上からライブの `%USERPROFILE%\.codex` を直す場合は、以下のように明示
しておくと誤って復旧作業ディレクトリだけを処理せずに済みます。

```sh
python3 .tmp/repair_rollout_session_meta.py dry-run --target-style wsl --codex-home /mnt/c/Users/WINDOWS_USERNAME/.codex --backup-root /mnt/c/src/.codex_bak
python3 .tmp/normalize_history_cwd.py dry-run --target-style wsl --codex-home /mnt/c/Users/WINDOWS_USERNAME/.codex --backup-root /mnt/c/src/.codex_bak
python3 .tmp/repair_ui_indexes.py dry-run --target-style wsl --codex-home /mnt/c/Users/WINDOWS_USERNAME/.codex --backup-root /mnt/c/src/.codex_bak
```

ただし、Codex app を WSL エージェントへ切り替えた後は、WSL側の
`/home/WSL_USERNAME/.codex` が新しいCodex homeとして使われる場合があります。
その場合、Windows側 `%USERPROFILE%\.codex` を正規化してもUIには出ません。
次の2つを比較してください。

```sh
python3 .tmp/current_history_visibility_audit.py --target-style wsl --codex-home /mnt/c/Users/WINDOWS_USERNAME/.codex
python3 .tmp/current_history_visibility_audit.py --target-style wsl --codex-home /home/WSL_USERNAME/.codex
```

Windows側に復旧済み履歴があり、WSL側が空に近い場合は、Codex appを完全終了
してからWSL側Codex homeへ移植します。

```sh
python3 .tmp/migrate_windows_codex_home_to_wsl.py dry-run --source-home /mnt/c/Users/WINDOWS_USERNAME/.codex --dest-home /home/WSL_USERNAME/.codex --backup-root /mnt/c/src/.codex_bak
python3 .tmp/migrate_windows_codex_home_to_wsl.py apply --source-home /mnt/c/Users/WINDOWS_USERNAME/.codex --dest-home /home/WSL_USERNAME/.codex --backup-root /mnt/c/src/.codex_bak
```

この移植は以下を行います。

- Windows側の有効な `sessions/**/*.jsonl` をWSL側へコピー
- Windows側 `state_5.sqlite` の履歴行をWSL側 `state_5.sqlite` へ追加
- `threads.rollout_path` を `/home/WSL_USERNAME/.codex/sessions/...` に補正
- WSL側の既存セッションを保持したまま `session_index.jsonl` を再構築
- WSL側の最小 `.codex-global-state.json` を作成または更新

DBを書き換えるため、`apply` はCodex appが完全終了している状態でのみ実行
してください。

## バックアップルートの役割

各スクリプトの `--backup-root` は、`apply` が変更前のデータを保存する出力先です。
復旧対象を検索する入力ディレクトリではなく、指定先のファイルが自動的にライブ環境へ反映されることもありません。
`dry-run` は変更予定を表示するだけで、バックアップルートを作成または更新しません。

### 指定するディレクトリ

バックアップルートには、次の条件を満たすディレクトリを指定します。

- Codex homeの外側にある。
- 実行ユーザーが読み書きできる。
- DBと変更対象ファイルを保存できる空き容量がある。
- どの端末、Codex home、切り替え作業のバックアップか識別できる。

`%CODEX_HOME%` 自身や、その配下の `sessions` などを指定してはいけません。
ライブデータと退避データを取り違えたり、後続のスキャンやコピーの対象に含めたりする原因になります。

`apply` の前にディレクトリを作成します。
一部のスクリプトは親ディレクトリが存在することを前提とするため、この手順は省略しません。

PowerShell:

```powershell
$BackupRoot = "C:\CodexBackups\wsl-to-windows"
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
```

コマンドプロンプト:

```bat
set "BACKUP_ROOT=C:\CodexBackups\wsl-to-windows"
if not exist "%BACKUP_ROOT%" mkdir "%BACKUP_ROOT%"
```

WSLシェル:

```sh
BACKUP_ROOT=/mnt/c/CodexBackups/wsl-to-windows
mkdir -p "$BACKUP_ROOT"
```

同じ切り替え作業の各スクリプトには、同じバックアップルートを指定できます。
ただし、各スクリプトは個別の実行時刻でバックアップを作るため、ディレクトリ全体が単一時点のスナップショットになるわけではありません。
一括して元の状態へ戻せる基準点が必要な場合は、後述の切り替え前バックアップも作成します。

### `apply` が保存するデータ

代表的な保存内容は次のとおりです。
タイムスタンプは実際の実行日時へ置き換わります。

```text
BACKUP_ROOT/
├── rollout-session-meta-backup-windows-YYYYMMDD-HHMMSS/
│   └── sessions/YYYY/MM/DD/rollout-SESSION_ID.jsonl
├── state_5.live-before-cwd-normalize-windows-YYYYMMDD-HHMMSS.sqlite
├── session_index.before-ui-index-repair-windows-YYYYMMDD-HHMMSS.jsonl
├── codex-global-state.before-ui-index-repair-windows-YYYYMMDD-HHMMSS.json
├── state_5.sqlite.before-wsl-home-migrate-YYYYMMDD-HHMMSS
├── state_5.sqlite-wal.before-wsl-home-migrate-YYYYMMDD-HHMMSS
├── state_5.sqlite-shm.before-wsl-home-migrate-YYYYMMDD-HHMMSS
├── session_index.jsonl.before-wsl-home-migrate-YYYYMMDD-HHMMSS
└── .codex-global-state.json.before-wsl-home-migrate-YYYYMMDD-HHMMSS
```

各スクリプトの保存動作は次のとおりです。

| スクリプト | 保存対象 | 保存単位と注意点 |
| --- | --- | --- |
| `repair_rollout_session_meta.py` | 実際に変更するrollout JSONL | Codex homeからの相対パスを保った専用ディレクトリへ原本をコピーする |
| `normalize_history_cwd.py` | 更新直前のライブDB | SQLite Backup APIで一貫した `state_5.sqlite` スナップショットを1ファイル作成する |
| `repair_ui_indexes.py` | `session_index.jsonl` と `.codex-global-state.json` | UI補助状態を書き換える直前の原本を個別にコピーする |
| `migrate_windows_codex_home_to_wsl.py` | 変更前の移行先DB、WAL、SHM、UI補助状態 | 存在する移行先ファイルだけを個別にコピーする |

変更対象が0件の場合は、そのスクリプトのバックアップが作成されないことがあります。
`migrate_windows_codex_home_to_wsl.py` が移行元からコピーするセッションJSONLは、バックアップルートではなく移行先の `sessions` へ直接追加されます。
この移行を元に戻す可能性がある場合は、移行先Codex homeの一式バックアップも事前に作成してください。

### バックアップに含まれる機密データ

バックアップには、次の情報が含まれる場合があります。

- rollout JSONL内の会話内容、ツール実行記録、作業ディレクトリ
- DB内のスレッド名、先頭メッセージ、rolloutパス、アーカイブ状態
- `session_index.jsonl` 内のスレッドIDと表示名
- `.codex-global-state.json` 内のワークスペースルートとUI状態

バックアップルートはCodex homeと同等の機密データとして扱います。
Gitへ追加せず、公開リポジトリ、共有フォルダー、一般利用のクラウド同期先へ不用意に置かないでください。

### 実行後に行う操作

`apply` の出力に表示されたバックアップパスを、実行したコマンドとともに記録します。
検証が終わるまではバックアップを編集、移動、改名しません。
スクリプトはバックアップの自動復元、自動削除、自動ローテーションを行いません。

ロールバック時はCodexを完全終了し、同じスクリプトと同じ実行時刻に対応するファイルを戻します。
rollout JSONLは、バックアップディレクトリ内の相対パスと同じ位置へ戻します。
DBスナップショットを戻す場合は、ライブDB、WAL、SHMを別の場所へ退避してから `state_5.sqlite` として配置し、古いsidecarを組み合わせません。
UI補助状態は、同じ実行に対応する `session_index.jsonl` と `.codex-global-state.json` を対として戻します。

WSL版のmigration checksumを持つDBをWindowsネイティブ版へ戻すなど、実行環境と異なる形式のDBを直接復元してはいけません。
WSLからWindowsネイティブへの切り替え全体を戻す場合は、各スクリプトの個別バックアップではなく、後述の `wsl-before-windows-native-...` を基準にします。

バックアップは、少なくとも切り替え後のCodex再起動、履歴表示、新規スレッド作成、再起動後の再確認が成功するまで保持します。
その後の削除や長期保管は、組織の保持方針に従って手動で行います。

## 症状

- Codex 起動時に SQLite migration checksum エラーが出る。
- `state_5.sqlite` を削除または再作成すると起動はするが、過去セッションが
  UIに表示されない。
- `state_5.sqlite` の `threads` には履歴行がある。
- `sessions\**\rollout-*.jsonl` も存在する。
- `threads.cwd` や `threads.thread_source` をDB上で直しても、Codex再起動後
  または一覧取得後に一部が元へ戻る。
- Windowsネイティブエージェントでは見えていた履歴が、WSLエージェントへ
  切り替えた後にUIから消える。
- Codex app更新後に新規作成した履歴も、DBや `session_index.jsonl` には
  存在するのにUIのプロジェクト履歴に出ない。

## やってはいけないこと

- 古いバックアップDBを、そのまま現在の `%CODEX_HOME%\state_5.sqlite` に
  差し替えない。アプリの `_sqlx_migrations` checksum と合わず起動不能に
  なることがある。
- `_sqlx_migrations.checksum` を推測で書き換えない。
- `state_5.sqlite` だけを直して復旧完了と判断しない。
- Codex起動中に `state_5.sqlite-wal` / `state_5.sqlite-shm` を削除、移動、
  リネームしない。

## 対象ファイル

ライブ状態:

```text
%CODEX_HOME%\state_5.sqlite
%CODEX_HOME%\state_5.sqlite-wal
%CODEX_HOME%\state_5.sqlite-shm
%CODEX_HOME%\sessions\**\rollout-*.jsonl
%CODEX_HOME%\session_index.jsonl
%CODEX_HOME%\.codex-global-state.json
```

補助スクリプト配置例:

```text
%RECOVERY_WORKDIR%\.tmp\current_history_visibility_audit.py
%RECOVERY_WORKDIR%\.tmp\repair_rollout_session_meta.py
%RECOVERY_WORKDIR%\.tmp\normalize_history_cwd.py
%RECOVERY_WORKDIR%\.tmp\repair_ui_indexes.py
%RECOVERY_WORKDIR%\.tmp\transplant_state5.py
```

## WSLからWindowsネイティブへ切り替える場合

この節は、WSLエージェントが使用した `%USERPROFILE%\.codex\state_5.sqlite` をWindowsネイティブエージェントが開くと、SQLx migration checksumエラーになる場合の復旧手順です。

この不一致は、SQLiteファイルの物理破損を意味しません。
Windows版とWSL版のCodexバイナリが、意味的には同じmigration SQLをそれぞれCRLFとLFで保持している場合、SQLxが計算するSHA-384は異なります。
WSL版のチェックサムが保存されたDBをWindows版が検証すると、適用済みmigrationのチェックサム不一致として起動を拒否します。

この手順では、Windowsネイティブエージェントを切り替え後の正本とします。
WSL用DBの `_sqlx_migrations.checksum` は書き換えず、Windowsネイティブ版にクリーンDBを生成させた後、rollout JSONLから履歴を再構築します。

### 事前条件

- 復旧作業はWSLシェル、PowerShell、コマンドプロンプトのいずれかから実行する。
- 作業途中でターミナルを開き直した場合は、パス変数とバックアップ先変数を再設定する。
- `%RECOVERY_WORKDIR%` には、このリポジトリの `scripts` ディレクトリがある。
- `%CODEX_HOME%` はWindows側の `%USERPROFILE%\.codex` を指す。
- 作業開始後、手順中で明示的に起動する場合を除いてCodexを完全終了しておく。
- Windowsネイティブエージェントへ切り替える設定が完了している。

以下の例では、`WINDOWS_USERNAME` と3つのパスを環境に合わせて変更します。

```sh
CODEX_HOME=/mnt/c/Users/WINDOWS_USERNAME/.codex
RECOVERY_WORKDIR=/mnt/c/src/recover_codex_sessions
BACKUP_ROOT=/mnt/c/src/.codex_bak
```

PowerShellでは、同じパスをWindows形式で設定します。

```powershell
$CodexHome = Join-Path $env:USERPROFILE ".codex"
$RecoveryWorkdir = "C:\src\recover_codex_sessions"
$BackupRoot = "C:\src\.codex_bak"
```

コマンドプロンプトでは、次の環境変数を設定します。

```bat
set "CODEX_HOME=%USERPROFILE%\.codex"
set "RECOVERY_WORKDIR=C:\src\recover_codex_sessions"
set "BACKUP_ROOT=C:\src\.codex_bak"
```

Windows側のコマンド例はPython Launcherの `py -3` を使用します。
`py -3 --version` が失敗する環境では、各コマンドの `py -3` を `python` に置き換えます。

### 1. 切り替え前の状態を監査する

WSL用DBを退避する前に、SQLite自体の整合性と履歴の所在を確認します。

WSLシェル:

```sh
cd "$RECOVERY_WORKDIR"
python3 scripts/current_history_visibility_audit.py \
  --target-style wsl \
  --codex-home "$CODEX_HOME"
```

PowerShell:

```powershell
Set-Location $RecoveryWorkdir
py -3 scripts\current_history_visibility_audit.py `
  --target-style wsl `
  --codex-home $CodexHome
```

コマンドプロンプト:

```bat
cd /d "%RECOVERY_WORKDIR%"
py -3 scripts\current_history_visibility_audit.py --target-style wsl --codex-home "%CODEX_HOME%"
```

次の項目を記録します。

- `integrity_check` が `ok`
- `quick_check` が `ok`
- 表示対象ユーザースレッド数
- `session_index.jsonl` に存在しないスレッドID
- DBから参照されるrollout JSONLの欠損数

`integrity_check` または `quick_check` が `ok` でない場合は、この切り替え手順を続けません。
物理破損したDBから履歴を移植する手順と、migration checksumだけが異なるDBを切り替える手順は分けて扱う必要があります。

### 2. Codexを完全終了する

すべてのCodexウィンドウを閉じ、Codex、`codex.exe`、WSL側の `codex` プロセスが残っていないことを確認します。
確認できない場合はWindowsを再起動し、Codexを開かずに次の手順へ進みます。

PowerShellでは、次のコマンドが何も返さないことを確認します。

```powershell
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "*codex*" }
```

コマンドプロンプトでは、次のコマンドがCodexプロセスを返さないことを確認します。

```bat
tasklist | findstr /i codex
```

Codexの稼働中に `state_5.sqlite-wal` または `state_5.sqlite-shm` を移動すると、未チェックポイントの更新を失う可能性があります。

### 3. Windows形式へ変換する前の全状態を保存する

DBだけでなく、履歴の再構築に使うJSONLとUI補助状態も保存します。

WSLシェル:

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
SWITCH_BACKUP="$BACKUP_ROOT/wsl-before-windows-native-$STAMP"
mkdir -p "$SWITCH_BACKUP"
cp -a "$CODEX_HOME/state_5.sqlite" "$SWITCH_BACKUP/"
test ! -e "$CODEX_HOME/state_5.sqlite-wal" || cp -a "$CODEX_HOME/state_5.sqlite-wal" "$SWITCH_BACKUP/"
test ! -e "$CODEX_HOME/state_5.sqlite-shm" || cp -a "$CODEX_HOME/state_5.sqlite-shm" "$SWITCH_BACKUP/"
cp -a "$CODEX_HOME/sessions" "$SWITCH_BACKUP/"
test ! -e "$CODEX_HOME/session_index.jsonl" || cp -a "$CODEX_HOME/session_index.jsonl" "$SWITCH_BACKUP/"
test ! -e "$CODEX_HOME/.codex-global-state.json" || cp -a "$CODEX_HOME/.codex-global-state.json" "$SWITCH_BACKUP/"
```

PowerShell:

```powershell
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$SwitchBackup = Join-Path $BackupRoot "wsl-before-windows-native-$Stamp"
New-Item -ItemType Directory -Path $SwitchBackup -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $CodexHome "state_5.sqlite") -Destination $SwitchBackup
foreach ($Name in @("state_5.sqlite-wal", "state_5.sqlite-shm", "session_index.jsonl", ".codex-global-state.json")) {
    $Source = Join-Path $CodexHome $Name
    if (Test-Path -LiteralPath $Source) {
        Copy-Item -LiteralPath $Source -Destination $SwitchBackup
    }
}
Copy-Item -LiteralPath (Join-Path $CodexHome "sessions") -Destination (Join-Path $SwitchBackup "sessions") -Recurse
```

コマンドプロンプトでは、`STAMP` を実行時刻に置き換えてから実行します。

```bat
set "STAMP=YYYYMMDD-HHMMSS"
set "SWITCH_BACKUP=%BACKUP_ROOT%\wsl-before-windows-native-%STAMP%"
mkdir "%SWITCH_BACKUP%"
copy /y "%CODEX_HOME%\state_5.sqlite" "%SWITCH_BACKUP%\"
if exist "%CODEX_HOME%\state_5.sqlite-wal" copy /y "%CODEX_HOME%\state_5.sqlite-wal" "%SWITCH_BACKUP%\"
if exist "%CODEX_HOME%\state_5.sqlite-shm" copy /y "%CODEX_HOME%\state_5.sqlite-shm" "%SWITCH_BACKUP%\"
if exist "%CODEX_HOME%\session_index.jsonl" copy /y "%CODEX_HOME%\session_index.jsonl" "%SWITCH_BACKUP%\"
if exist "%CODEX_HOME%\.codex-global-state.json" copy /y "%CODEX_HOME%\.codex-global-state.json" "%SWITCH_BACKUP%\"
xcopy "%CODEX_HOME%\sessions" "%SWITCH_BACKUP%\sessions\" /E /I /H /K /Y
```

存在しない補助ファイルがある場合は、そのファイルのコピーだけを省略します。
バックアップ先に `state_5.sqlite` と `sessions` が存在することを確認してから次へ進みます。

### 4. rollout JSONLをWindows形式へ変換する

クリーンDBを生成する前に、履歴の正本であるrollout JSONLをWindows形式へ揃えます。
先にJSONLを直すことで、Windowsネイティブ版による初回スキャン時に `threads.cwd` がWSL形式へ戻ることを防ぎます。

WSLシェル:

```sh
cd "$RECOVERY_WORKDIR"
python3 scripts/repair_rollout_session_meta.py dry-run \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
```

PowerShell:

```powershell
Set-Location $RecoveryWorkdir
py -3 scripts\repair_rollout_session_meta.py dry-run `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot
```

コマンドプロンプト:

```bat
cd /d "%RECOVERY_WORKDIR%"
py -3 scripts\repair_rollout_session_meta.py dry-run --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
```

ドライランの `files_to_update` とJSON parse errorを確認します。
更新対象が妥当であれば適用します。

WSLシェル:

```sh
python3 scripts/repair_rollout_session_meta.py apply \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
```

PowerShell:

```powershell
py -3 scripts\repair_rollout_session_meta.py apply `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot
```

コマンドプロンプト:

```bat
py -3 scripts\repair_rollout_session_meta.py apply --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
```

適用後に同じドライランを再実行し、`files_to_update` が `0` になることを確認します。
この段階ではWSL用 `state_5.sqlite` の `threads.cwd` や `_sqlx_migrations` を変更しません。

### 5. WSL用DBをライブ位置から退避する

Windowsネイティブ版にクリーンDBを作らせるため、DB本体とsidecarを同じバックアップディレクトリへ移します。
削除はしません。

WSLシェル:

```sh
test ! -e "$CODEX_HOME/state_5.sqlite" || mv "$CODEX_HOME/state_5.sqlite" "$SWITCH_BACKUP/state_5.sqlite.wsl-live"
test ! -e "$CODEX_HOME/state_5.sqlite-wal" || mv "$CODEX_HOME/state_5.sqlite-wal" "$SWITCH_BACKUP/state_5.sqlite-wal.wsl-live"
test ! -e "$CODEX_HOME/state_5.sqlite-shm" || mv "$CODEX_HOME/state_5.sqlite-shm" "$SWITCH_BACKUP/state_5.sqlite-shm.wsl-live"
```

PowerShell:

```powershell
foreach ($Name in @("state_5.sqlite", "state_5.sqlite-wal", "state_5.sqlite-shm")) {
    $Source = Join-Path $CodexHome $Name
    if (Test-Path -LiteralPath $Source) {
        Move-Item -LiteralPath $Source -Destination (Join-Path $SwitchBackup "$Name.wsl-live")
    }
}
```

コマンドプロンプト:

```bat
if exist "%CODEX_HOME%\state_5.sqlite" move /y "%CODEX_HOME%\state_5.sqlite" "%SWITCH_BACKUP%\state_5.sqlite.wsl-live"
if exist "%CODEX_HOME%\state_5.sqlite-wal" move /y "%CODEX_HOME%\state_5.sqlite-wal" "%SWITCH_BACKUP%\state_5.sqlite-wal.wsl-live"
if exist "%CODEX_HOME%\state_5.sqlite-shm" move /y "%CODEX_HOME%\state_5.sqlite-shm" "%SWITCH_BACKUP%\state_5.sqlite-shm.wsl-live"
```

`%CODEX_HOME%` に `state_5.sqlite`、`state_5.sqlite-wal`、`state_5.sqlite-shm` が残っていないことを確認します。

### 6. Windowsネイティブ版にクリーンDBを生成させる

Codexのエージェント設定がWindowsネイティブであることを確認してから、Codexを一度起動します。
ホーム画面またはプロジェクト画面まで到達し、`%CODEX_HOME%\state_5.sqlite` が新しく作成されたことを確認します。

その後、Codexを再び完全終了します。
ここで生成されたDBは、Windowsネイティブ版が持つCRLF形式のmigrationチェックサムを正としています。

PowerShellでは、完全終了後に次のコマンドが `True` を返すことを確認します。

```powershell
Test-Path -LiteralPath (Join-Path $CodexHome "state_5.sqlite")
```

コマンドプロンプトでは、完全終了後に次のコマンドがファイル情報を返すことを確認します。

```bat
dir "%CODEX_HOME%\state_5.sqlite"
```

この段階で再びchecksumエラーになる場合は、次のいずれかが残っています。

- `state_5.sqlite` またはsidecarの退避漏れ
- Codexが別の `CODEX_HOME` を参照している
- Windowsネイティブ版ではなくWSL版のバックエンドが起動している

原因を解消せずに `_sqlx_migrations.checksum` を直接更新してはいけません。

### 7. 新しいDBの履歴を監査する

Windowsネイティブ版が生成したDBを対象に監査します。

WSLシェル:

```sh
cd "$RECOVERY_WORKDIR"
python3 scripts/current_history_visibility_audit.py \
  --target-style windows \
  --codex-home "$CODEX_HOME"
```

PowerShell:

```powershell
Set-Location $RecoveryWorkdir
py -3 scripts\current_history_visibility_audit.py `
  --target-style windows `
  --codex-home $CodexHome
```

コマンドプロンプト:

```bat
cd /d "%RECOVERY_WORKDIR%"
py -3 scripts\current_history_visibility_audit.py --target-style windows --codex-home "%CODEX_HOME%"
```

切り替え前に記録した表示対象ユーザースレッド数と比較します。
Codexがrollout JSONLを再スキャン済みであれば、旧DBから `threads` 行を直接移植する必要はありません。

履歴行が不足している場合は、次の点を先に確認します。

- 対応する `sessions/**/*.jsonl` が存在する
- JSONL先頭の `type` が `session_meta`
- `payload.cwd` が `C:\...` 形式
- `payload.thread_source` がユーザースレッドでは `user`
- JSONLの解析エラーがない

JSONLが正常でも再構築されない場合だけ、退避したDBを移植元として `threads` 行の移植を検討します。
移植先DBの `_sqlx_migrations` とスキーマは変更せず、移植前に双方の `sqlite_schema` が互換であることを確認します。

### 8. DB内の履歴メタデータをWindows形式へ揃える

新しいDBに残ったWSL形式の `cwd` と欠損した `thread_source` を確認します。

WSLシェル:

```sh
python3 scripts/normalize_history_cwd.py dry-run \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
```

PowerShell:

```powershell
py -3 scripts\normalize_history_cwd.py dry-run `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot
```

コマンドプロンプト:

```bat
py -3 scripts\normalize_history_cwd.py dry-run --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
```

更新対象が妥当であれば適用し、再度ドライランします。

WSLシェル:

```sh
python3 scripts/normalize_history_cwd.py apply \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
```

PowerShell:

```powershell
py -3 scripts\normalize_history_cwd.py apply `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot
```

コマンドプロンプト:

```bat
py -3 scripts\normalize_history_cwd.py apply --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
```

次の2項目が `0` になれば、DB内の履歴メタデータはWindows形式です。

```json
"cwd_rows_to_update": 0,
"thread_source_rows_to_update": 0
```

### 9. UI補助状態をWindows形式へ揃える

新しいDBの表示対象スレッドを基準に、`session_index.jsonl` と `.codex-global-state.json` を再構築します。

WSLシェル:

```sh
python3 scripts/repair_ui_indexes.py dry-run \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
```

PowerShell:

```powershell
py -3 scripts\repair_ui_indexes.py dry-run `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot
```

コマンドプロンプト:

```bat
py -3 scripts\repair_ui_indexes.py dry-run --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
```

更新対象が妥当であれば適用します。

WSLシェル:

```sh
python3 scripts/repair_ui_indexes.py apply \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
```

PowerShell:

```powershell
py -3 scripts\repair_ui_indexes.py apply `
  --target-style windows `
  --codex-home $CodexHome `
  --backup-root $BackupRoot
```

コマンドプロンプト:

```bat
py -3 scripts\repair_ui_indexes.py apply --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
```

### 10. 切り替え結果を検証する

3つの修復スクリプトをドライランで再実行します。

WSLシェル:

```sh
python3 scripts/repair_rollout_session_meta.py dry-run \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
python3 scripts/normalize_history_cwd.py dry-run \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
python3 scripts/repair_ui_indexes.py dry-run \
  --target-style windows \
  --codex-home "$CODEX_HOME" \
  --backup-root "$BACKUP_ROOT"
python3 scripts/current_history_visibility_audit.py \
  --target-style windows \
  --codex-home "$CODEX_HOME"
```

PowerShell:

```powershell
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
py -3 scripts\current_history_visibility_audit.py `
  --target-style windows `
  --codex-home $CodexHome
```

コマンドプロンプト:

```bat
py -3 scripts\repair_rollout_session_meta.py dry-run --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
py -3 scripts\normalize_history_cwd.py dry-run --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
py -3 scripts\repair_ui_indexes.py dry-run --target-style windows --codex-home "%CODEX_HOME%" --backup-root "%BACKUP_ROOT%"
py -3 scripts\current_history_visibility_audit.py --target-style windows --codex-home "%CODEX_HOME%"
```

期待結果は次のとおりです。

- rollout側の `files_to_update` が `0`
- DB側の `cwd_rows_to_update` が `0`
- DB側の `thread_source_rows_to_update` が `0`
- SQLite `integrity_check` が `ok`
- SQLite `quick_check` が `ok`
- `vscode_thread_source_null_rows` が空
- `session_index_compare.live_visible_user_ids_missing_from_index` が `0`
- `session_index_compare.source_ids_missing_from_index` が `0`

最後にCodexをWindowsネイティブエージェントとして起動し、次を確認します。

- checksumエラーが出ない
- 切り替え前と同数のユーザースレッドが表示される
- Windows形式のワークスペースごとに履歴が表示される
- 新しいスレッドを作成し、再起動後も表示される

### 切り替えを取り消す場合

Windowsネイティブ版で新規スレッドを作成する前であれば、切り替え前の状態へ戻せます。

1. Codexを完全終了する。
2. 新しく生成された `state_5.sqlite` とsidecarを別の退避先へ移す。
3. `SWITCH_BACKUP` の `.wsl-live` ファイルを元の名前で `%CODEX_HOME%` へ戻す。
4. `repair_rollout_session_meta.py` が作成したJSONLバックアップを戻す。
5. `session_index.jsonl` と `.codex-global-state.json` を `SWITCH_BACKUP` から戻す。
6. WSLエージェント設定へ戻してCodexを起動する。

Windowsネイティブ版で新規スレッドを作成した後は、単純なロールバックを行うとそのスレッドを失います。
新規rollout JSONLを退避先へ統合してから戻す必要があります。

### 再発を防ぐ

Windows版とWSL版が同じ `%CODEX_HOME%\state_5.sqlite` を使い続ける構成では、エージェントを再び切り替えたときにchecksum不一致が再発します。
単一DBのチェックサムを切り替えのたびに書き換える運用は、実行中バイナリとDBの対応を判別しにくくし、誤ったDBへ書き込む危険を増やします。

継続的に両方のエージェントを使う場合は、Windowsネイティブ用とWSL用で `CODEX_HOME` を分離します。
共有する対象は `sessions` のrollout JSONLに限定し、各環境の `state_5.sqlite`、sidecar、`session_index.jsonl`、`.codex-global-state.json` はそれぞれの環境に生成させます。

## 共通復旧手順

### 1. Codexを完全終了する

すべてのCodexウィンドウを閉じます。DBやJSONLを書き換える前に、Codex
プロセスが残っていないことを確認してください。不安があればOSを再起動し、
Codexを開く前に復旧作業を実施します。

### 2. ライブ状態をバックアップする

編集前に、以下をタイムスタンプ付きで退避します。

```text
%CODEX_HOME%\state_5.sqlite*
%CODEX_HOME%\session_index.jsonl
%CODEX_HOME%\.codex-global-state.json
%CODEX_HOME%\sessions
```

壊れたDBや当時のバックアップDBも、上書きせず別名で保持します。

### 3. migration checksum エラー時は、現行版のクリーンDBを作る

Codexが SQLx migration checksum エラーで起動できない場合は、古いDBを直接
直すより、現行Codexに合うDBを作らせるのが安全です。

1. `state_5.sqlite`, `state_5.sqlite-wal`, `state_5.sqlite-shm` を退避する。
2. ライブの `state_5.sqlite*` を `%CODEX_HOME%` から外へ移動する。
3. Codexを一度起動し、現行アプリにクリーンな `state_5.sqlite` を作らせる。
4. Codexを完全終了する。

これで古い `_sqlx_migrations` checksum を引きずらずに済みます。

### 4. DBに履歴行が存在するか確認する

```cmd
cd /d %RECOVERY_WORKDIR%
python .tmp\current_history_visibility_audit.py
```

確認ポイント:

- `integrity_check` が `ok`
- `quick_check` が `ok`
- `source='vscode' AND thread_source='user'` にユーザー履歴がある
- `vscode_thread_source_null_rows` が空
- `session_index_compare.source_ids_missing_from_index` が `0`

クリーンDBに古い履歴行が存在しない場合だけ、移植ヘルパーを使います。

```cmd
python .tmp\transplant_state5.py analyze
python .tmp\transplant_state5.py merge-live
```

多くの場合、CodexがJSONLから履歴行を再構築済みであれば、旧DBからの
`threads` 移植は不要です。

### 5. 先に rollout JSONL の `session_meta` を直す

今回の系統の問題では、ここが主原因になりやすいです。

旧JSONLの例:

```json
{"cwd":"/mnt/c/path/to/%PROJECT_NAME%","source":"vscode"}
```

Windowsネイティブエージェント時にCodexが期待する形式:

```json
{"cwd":"C:\\path\\to\\%PROJECT_NAME%","source":"vscode","thread_source":"user"}
```

WSLエージェント時にCodexが期待する形式:

```json
{"cwd":"/mnt/c/path/to/%PROJECT_NAME%","source":"vscode","thread_source":"user"}
```

まずドライランします。

```cmd
python .tmp\repair_rollout_session_meta.py dry-run --target-style %TARGET_STYLE%
```

`files_to_update` が0でなければ適用します。

```cmd
python .tmp\repair_rollout_session_meta.py apply --target-style %TARGET_STYLE%
```

このスクリプトは、JSONLの先頭行が `type=session_meta` の場合だけ、その
`payload` を修正します。本文の会話イベントには触りません。

修正内容:

- `payload.cwd`: `--target-style windows` なら `<DRIVE>:\...` へ変換
- `payload.cwd`: `--target-style wsl` なら `/mnt/<drive>/...` へ変換
- `payload.thread_source`: `source="vscode"` なら `user`
- `payload.thread_source`: `source={"subagent":...}` なら `subagent`

バックアップは `%RECOVERY_WORKDIR%\rollout-session-meta-backup-*` に作られます。

空または壊れた `rollout-*.jsonl` があるとJSON parse errorが出ます。DBから
参照されていないファイルであれば、履歴表示の直接原因とは限りません。

### 6. 修正済みJSONLに合わせて `state_5.sqlite` を同期する

JSONLを直した後、DBキャッシュ側を同期します。

```cmd
python .tmp\normalize_history_cwd.py dry-run --target-style %TARGET_STYLE%
python .tmp\normalize_history_cwd.py apply --target-style %TARGET_STYLE%
```

このスクリプトは `threads.cwd` を対象エージェントの現行形式へ揃えます。

Windowsネイティブエージェントの場合:

```text
\\?\C:\path\to\%PROJECT_NAME%
```

WSLエージェントの場合:

```text
/mnt/c/path/to/%PROJECT_NAME%
```

また、`source='vscode' AND thread_source IS NULL` の行を
`thread_source='user'` に補正します。

適用後、再度ドライランして以下になればDB側は完了です。

```json
"cwd_rows_to_update": 0,
"thread_source_rows_to_update": 0
```

### 7. UI補助インデックスを修復する

DBとJSONLを直してもUIに出ない場合、`session_index.jsonl` と
`.codex-global-state.json` の補助状態も同期します。

```cmd
python .tmp\repair_ui_indexes.py dry-run --target-style %TARGET_STYLE%
python .tmp\repair_ui_indexes.py apply --target-style %TARGET_STYLE%
```

このスクリプトは以下を行います。

- `session_index.jsonl` を表示可能なユーザースレッドから再構築
- `.codex-global-state.json` の `project-order` を対象形式へ正規化
- `.codex-global-state.json` の `active-workspace-roots` を正規化
- `.codex-global-state.json` の `electron-saved-workspace-roots` を正規化
- `.codex-global-state.json` の `thread-workspace-root-hints` を再構築

期待状態:

```text
session_index rows == live visible user ids
source_ids_missing_from_index == 0
thread_workspace_root_hints count == visible user thread count
```

### 8. 最終確認

以下を実行します。

```cmd
python .tmp\repair_rollout_session_meta.py dry-run --target-style %TARGET_STYLE%
python .tmp\normalize_history_cwd.py dry-run --target-style %TARGET_STYLE%
python .tmp\repair_ui_indexes.py dry-run --target-style %TARGET_STYLE%
python .tmp\current_history_visibility_audit.py --target-style %TARGET_STYLE%
```

期待結果:

- rollout側の `files_to_update` が `0`
- DB側の `cwd_rows_to_update` が `0`
- DB側の `thread_source_rows_to_update` が `0`
- SQLite `integrity_check` が `ok`
- SQLite `quick_check` が `ok`
- `vscode_thread_source_null_rows` が空
- `session_index_compare.live_visible_user_ids_missing_from_index` が `0`
- `session_index_compare.source_ids_missing_from_index` が `0`

最後にCodexを通常起動し、UI上で履歴が表示されるか確認します。

## なぜこの順番なのか

Codexの `thread/list` は `cwd` の完全一致フィルタを使います。また
`useStateDbOnly` が真でない限り、rollout JSONL をスキャンしてDBのスレッド
メタデータを修復できます。

そのため、次の3層を一致させる必要があります。

1. `%CODEX_HOME%\sessions\**\rollout-*.jsonl` 先頭の `session_meta.payload`
2. `%CODEX_HOME%\state_5.sqlite` の `threads` 行
3. UI補助状態の `%CODEX_HOME%\session_index.jsonl` と
   `%CODEX_HOME%\.codex-global-state.json`

DBだけを直すと、JSONL側の古い値で上書きされます。JSONLとDBを直しても、
UI補助インデックスが古いと表示やプロジェクト紐付けが崩れる可能性があります。

WindowsネイティブエージェントとWSLエージェントを切り替える場合も同じです。
同じ物理ディレクトリでも、Windows側では `C:\src\project`、WSL側では
`/mnt/c/src/project` と表記されます。どちらか一方の形式に全層を揃えないと、
DB上には履歴があり `session_index.jsonl` にもIDがあるのに、UIのプロジェクト
履歴からは外れます。

## ロールバック

各 `apply` は `%RECOVERY_WORKDIR%` 配下にバックアップを作成します。
ロールバックする場合はCodexを完全終了し、必要なファイルを戻します。

- JSONLメタデータ:
  `rollout-session-meta-backup-*` から `%CODEX_HOME%\sessions` 配下へ戻す
- DB:
  `state_5.live-before-*.sqlite` を `%CODEX_HOME%\state_5.sqlite` へ戻す
- UIインデックス:
  `session_index.before-ui-index-repair-*.jsonl` を
  `%CODEX_HOME%\session_index.jsonl` へ戻す
- グローバル状態:
  `codex-global-state.before-ui-index-repair-*.json` を
  `%CODEX_HOME%\.codex-global-state.json` へ戻す

`state_5.sqlite` を戻す場合、`state_5.sqlite-wal` と `state_5.sqlite-shm` の
削除または退避は、Codexが完全終了している状態でのみ実施します。
