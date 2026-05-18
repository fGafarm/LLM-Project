# PC不要・GitHub Actions + R2 で毎日自動更新セットアップ手順

PCがオフでも毎朝6:00 JST に EDINET → xbrl_store → frontend → Cloudflare Pages デプロイまで完全自動化する手順。

## 構成

```
[GitHub Actions cron 06:00 JST]
        ↓
[ubuntu-latest runner]
  ├─ checkout fGafarm/LLM-Project (このリポジトリ・daily_update.py 等)
  ├─ checkout fGafarm/kinmyakucode (frontend, push 先)
  ├─ R2 から xbrl_store / fixed_assets_store / land_price_data / pdf_xbrl をダウンロード
  ├─ daily_update.py 実行 (EDINET取込 → xbrl抽出 → generate_all_companies → metrics_summary)
  ├─ R2 に xbrl_store / fixed_assets_store / pdf_xbrl の変更分をアップロード
  ├─ kinmyakucode へ frontend/public/data/ を commit + push
        ↓
[Cloudflare Pages 自動デプロイ → kinmyakucode.com 更新]
```

## ユーザー作業 (約30分)

### 1. Cloudflare R2 バケット作成

- Cloudflare ダッシュボード → R2 → **Create bucket**
- バケット名: `kinmyaku-data` (任意)
- ロケーション: 自動 (APAC推奨)

### 2. R2 API トークン作成

- R2 → **Manage R2 API Tokens** → **Create API token**
- Permission: **Object Read & Write**
- Specify bucket: `kinmyaku-data` (上で作ったもの)
- TTL: なし

→ 表示される **Access Key ID** と **Secret Access Key** をメモ。  
→ **Account ID** はダッシュボード右側に表示されてる32文字の文字列。

### 3. GitHub Personal Access Token 作成 (kinmyakucode 用)

GitHub Actions が `fGafarm/kinmyakucode` に push するために必要。

- GitHub Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token (classic)
- Name: `kinmyaku-daily-bot`
- Expiration: No expiration
- Scopes: `repo` (Full control of private repositories) にチェック
- Generate → 表示される `ghp_xxxxx` をメモ

### 4. GitHub Secrets を `fGafarm/LLM-Project` に追加

- リポジトリ → Settings → Secrets and variables → **Actions** → **New repository secret**

| Name | Value |
|------|-------|
| `R2_ACCOUNT_ID` | Cloudflare の Account ID (32文字) |
| `R2_ACCESS_KEY_ID` | 手順2で出た Access Key ID |
| `R2_SECRET_ACCESS_KEY` | 手順2で出た Secret Access Key |
| `R2_BUCKET` | `kinmyaku-data` |
| `EDINET_API_KEY` | 現在の `backend/.env` の `EDINET_API_KEY=...` の値 |
| `FUDOUSAN_API_KEY` | 現在の `backend/.env` の `Fudousan_API_KEY=...` の値 |
| `KINMYAKU_PUSH_TOKEN` | 手順3の GitHub PAT (`ghp_xxxxx`) |

### 5. ローカル → R2 初回アップロード (1回だけ)

PC で以下を実行 (約10-30分):

```powershell
# プロジェクトルートで
cd "C:\Users\shun nabeno\Desktop\Local LLM Project"

# R2 認証情報を環境変数に
$env:R2_ACCOUNT_ID="xxxx"
$env:R2_ACCESS_KEY_ID="xxxx"
$env:R2_SECRET_ACCESS_KEY="xxxx"
$env:R2_BUCKET="kinmyaku-data"

# boto3 インストール (未インストールなら)
pip install boto3

# 全データを R2 へアップロード (約 2GB)
# 注: canonical xbrl_store は financial_analysis_system/xbrl_store/
python r2_sync.py upload-all financial_analysis_system/xbrl_store fixed_assets_store land_price_data
```

(`E:\PDF\PDF+XBRL` の EDINET ZIPは R2 に上げない設計。GH Actions runner 上で毎日 EDINET から新規ZIPだけダウンロード → 抽出後はランナー停止と共に消える。古いZIPは不要なので xbrl_store の JSON のみ R2 に保存。)

### 6. LLM-Project リポジトリへコード push

```bash
cd "C:\Users\shun nabeno\Desktop\Local LLM Project"
git add daily_update.py r2_sync.py requirements-daily.txt .gitignore .github/
git add financial_analysis_system/xbrl_batch_extractor.py
git add financial_analysis_system/extract_setsubi.py  # 存在すれば
git add financial_analysis_system/calculate_hidden_assets.py  # 存在すれば
git add financial_analysis_system/run_quarterly_batch.py
git commit -m "feat: GH Actions daily auto-update workflow + R2 sync"
git push
```

### 7. 動作テスト

- GitHub → fGafarm/LLM-Project → **Actions** → **Daily Stock Data Update** → **Run workflow**
- 約30-60分後に成功すれば成功
- ログは Artifacts から `daily-update-log` をダウンロードして確認

### 8. 既存の Windows Task Scheduler を無効化 (任意)

PC不要にするなら、ローカルのスケジュールタスクは止めておく:

```powershell
Disable-ScheduledTask -TaskName "DailyStockFlowUpdate"
```

## トラブルシューティング

- **R2 アップロード途中で止まった**: `r2_sync.py upload-all` を再実行。同サイズのファイルはスキップされる。
- **GH Actions の checkout で 403**: KINMYAKU_PUSH_TOKEN の権限を確認 (repo スコープ必須)。
- **EDINET docs 0件**: 土日祝はEDINET発表なし。月曜の実行で土・日分も取り込まれる。
- **`daily-update-log` Artifact 内のログを確認**: 90分でタイムアウト。手動実行で再試行可能。

## コスト

- **Cloudflare R2**: 10GB ストレージ + 10M GET + 1M PUT / 月が無料 → 現状データ ~2GB なので無料枠内
- **GitHub Actions**: パブリックリポジトリなら無制限。プライベートでも 2000分/月 無料 → 1日30分実行で月900分。無料枠内
- **合計**: **¥0/月**
