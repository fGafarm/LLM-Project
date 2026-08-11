# backup_to_ssd.ps1 — git管理外のデータストア・秘密情報を外付けSSD (F:) へバックアップ
#
# 使い方:  powershell -ExecutionPolicy Bypass -File .\backup_to_ssd.ps1
#
# 設計: robocopy /E (追記型・削除同期なし)。
#   ソース側でファイルが消えても (パイプラインのサイレント死等) バックアップは残る。
#   完全ミラーが欲しい場合のみ /E を /MIR に変えること (事故時にバックアップごと消えるリスクあり)。

$src = "C:\Users\shun nabeno\Desktop\Local LLM Project"
$dst = "F:\LLM-Project-Backup"

# git管理外の保存対象 (プロジェクトルートからの相対パス)
$dirs = @(
    "backend\keys",                                  # APIキーJSON (gitignore対象)
    "financial_analysis_system\xbrl_store",          # 正史store (R2同期・約1万社)
    "financial_analysis_system\company_data",
    "fixed_assets_store",
    "land_price_data",
    "pdf_xbrl",
    "tw_financial_analysis\tw_xbrl_store",
    "tw_financial_analysis\raw_mops",                # 台湾MOPS生データ (再取得コスト大)
    "us_financial_analysis\us_xbrl_store"
)
$files = @(
    "backend\.env"                                   # Fudousan_API_KEY 等
)

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $dst "_logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "backup_$stamp.log"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$failed = @()

foreach ($d in $dirs) {
    $from = Join-Path $src $d
    $to   = Join-Path $dst $d
    if (-not (Test-Path $from)) {
        Add-Content $log "SKIP (not found): $d"
        Write-Warning "SKIP (not found): $d"
        continue
    }
    Write-Host ">> $d"
    robocopy $from $to /E /R:1 /W:1 /MT:16 /NFL /NDL /NP /NJH | Select-Object -Last 8 | Add-Content $log
    # robocopy終了コード: 0-7=成功 (何をコピーしたかの違い), 8以上=失敗あり
    if ($LASTEXITCODE -ge 8) { $failed += $d }
    Add-Content $log "EXIT $LASTEXITCODE : $d`r`n"
}

# Claude Code の永続メモリ (プロジェクト外 ~/.claude 配下、git非管理)
$claudeMem = Join-Path $env:USERPROFILE ".claude\projects\c--Users-shun-nabeno-Desktop-Local-LLM-Project\memory"
if (Test-Path $claudeMem) {
    Write-Host ">> claude memory"
    robocopy $claudeMem (Join-Path $dst "_claude_memory") /E /R:1 /W:1 /NFL /NDL /NP /NJH | Select-Object -Last 8 | Add-Content $log
    if ($LASTEXITCODE -ge 8) { $failed += "_claude_memory" }
    Add-Content $log "EXIT $LASTEXITCODE : _claude_memory`r`n"
}

foreach ($f in $files) {
    $from = Join-Path $src $f
    $to   = Join-Path $dst $f
    if (-not (Test-Path $from)) {
        Add-Content $log "SKIP (not found): $f"
        continue
    }
    New-Item -ItemType Directory -Force (Split-Path $to) | Out-Null
    Copy-Item $from $to -Force
    Add-Content $log "COPY: $f"
}

$sw.Stop()
$mins = [math]::Round($sw.Elapsed.TotalMinutes, 1)
if ($failed.Count -gt 0) {
    Write-Host "`n*** FAILED ($mins min): $($failed -join ', ') — 詳細: $log" -ForegroundColor Red
    exit 1
} else {
    Write-Host "`nOK ($mins min) — ログ: $log" -ForegroundColor Green
    exit 0
}
