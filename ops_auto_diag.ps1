# ============================================================
# ops_auto_diag.ps1 — 異常時のみ Claude を起動する2段構え自動診断 (OPS.md §8)
#
# 動作: health_check.py --json を実行 → 全緑なら何もせず終了 (Claude起動なし = API費ゼロ)
#       異常 (exit != 0) の時だけ claude -p で診断レポートを logs\ops_advice.md に書かせる。
#       Claude は読み取り専用の診断のみ。修復コマンドは提案止まり (実行しない)。
#
# ⚠️ 有効化は課金を伴うためユーザー判断。登録コマンド (手動で1回):
#   schtasks /Create /TN OpsAutoDiag /SC DAILY /ST 07:30 ^
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\Users\shun nabeno\Desktop\Local LLM Project\ops_auto_diag.ps1\""
# ============================================================
Set-Location "$PSScriptRoot"
$py = "C:\Users\shun nabeno\AppData\Local\Python\bin\python.exe"

& $py health_check.py --json | Out-File -Encoding utf8 "logs\health_last.json"
$code = $LASTEXITCODE
"health_check exit=$code at $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-File -Append -Encoding utf8 "logs\ops_auto_diag.log"

if ($code -ne 0) {
    # 異常時のみ Claude を headless 起動 (課金発生ポイント)
    claude -p ("health_check が exit " + $code + " を返した。logs\health_last.json を読み、OPS.md の対応表に従って " +
        "各 CRIT/WARN の診断と対処コマンド案を logs\ops_advice.md に日本語で書け。" +
        "git commit/push・タスク有効化・再抽出・ファイル削除など状態を変える操作は絶対に実行するな (提案のみ)。") `
        --allowedTools "Read,Glob,Grep" 2>&1 | Out-File -Append -Encoding utf8 "logs\ops_auto_diag.log"
}
