# restore_from_ssd.ps1 — 別PCで F:\LLM-Project-Backup からgit外データを復元する
#
# 前提: 先にリポジトリをcloneしておくこと
#   git clone https://github.com/fGafarm/LLM-Project.git
#   cd LLM-Project
#   git clone https://github.com/fGafarm/kinmyakucode.git StockFlow
#
# 使い方 (プロジェクトルートで実行):
#   powershell -ExecutionPolicy Bypass -File .\restore_from_ssd.ps1
#   SSDのドライブレターが違う場合:  ... -File .\restore_from_ssd.ps1 -BackupRoot "E:\LLM-Project-Backup"
#
# 注意: 復元は既存ファイルを上書きする (バックアップ側が正)。逆向き (PC→SSD) は backup_to_ssd.ps1 を使うこと。

param(
    [string]$BackupRoot = "F:\LLM-Project-Backup"
)

# このスクリプト自身の場所 = プロジェクトルート
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path $BackupRoot)) {
    Write-Error "バックアップが見つかりません: $BackupRoot (SSDは挿さっていますか? -BackupRoot で場所指定も可)"
    exit 1
}

$dirs = @(
    "backend\keys",
    "financial_analysis_system\xbrl_store",
    "financial_analysis_system\company_data",
    "fixed_assets_store",
    "land_price_data",
    "pdf_xbrl",
    "tw_financial_analysis\tw_xbrl_store",
    "tw_financial_analysis\raw_mops",
    "us_financial_analysis\us_xbrl_store"
)
$files = @(
    "backend\.env"
)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$failed = @()

foreach ($d in $dirs) {
    $from = Join-Path $BackupRoot $d
    $to   = Join-Path $proj $d
    if (-not (Test-Path $from)) {
        Write-Warning "SKIP (バックアップに無い): $d"
        continue
    }
    Write-Host ">> $d"
    robocopy $from $to /E /R:1 /W:1 /MT:16 /NFL /NDL /NP /NJH | Out-Null
    if ($LASTEXITCODE -ge 8) { $failed += $d }
}

foreach ($f in $files) {
    $from = Join-Path $BackupRoot $f
    if (Test-Path $from) {
        $to = Join-Path $proj $f
        New-Item -ItemType Directory -Force (Split-Path $to) | Out-Null
        Copy-Item $from $to -Force
        Write-Host ">> $f"
    }
}

# Claude Code メモリ: このPC上のプロジェクトパスから保存先スラッグを組み立てて復元
#   (~/.claude/projects/<パス由来スラッグ>/memory — PCごとにスラッグが違うため動的に生成)
$memSrc = Join-Path $BackupRoot "_claude_memory"
if (Test-Path $memSrc) {
    $slug = ($proj -replace '[:\\ ]', '-') -replace '-+', '-'
    $slug = $slug.ToLower().Trim('-')
    $memDst = Join-Path $env:USERPROFILE ".claude\projects\$slug\memory"
    Write-Host ">> claude memory -> $memDst"
    robocopy $memSrc $memDst /E /R:1 /W:1 /NFL /NDL /NP /NJH | Out-Null
    if ($LASTEXITCODE -ge 8) { $failed += "_claude_memory" }
}

$sw.Stop()
$mins = [math]::Round($sw.Elapsed.TotalMinutes, 1)
if ($failed.Count -gt 0) {
    Write-Host "`n*** FAILED ($mins min): $($failed -join ', ')" -ForegroundColor Red
    exit 1
} else {
    Write-Host "`nOK ($mins min) — 復元完了。次: requirements-daily.txt の pip install、StockFlow/frontend の npm ci" -ForegroundColor Green
    exit 0
}
