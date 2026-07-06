@echo off
rem ============================================================
rem 週次ディープ監査 (OPS.md §3) — 毎週日曜 09:00 推奨
rem 登録 (ユーザーが手動で1回実行):
rem   schtasks /Create /TN WeeklyDeepAudit /SC WEEKLY /D SUN /ST 09:00 ^
rem     /TR "\"C:\Users\shun nabeno\Desktop\Local LLM Project\weekly_audit.bat\""
rem ============================================================
cd /d "%~dp0"
set PY=C:\Users\shun nabeno\AppData\Local\Python\bin\python.exe
set LOG=logs\weekly_audit_%date:~0,4%%date:~5,2%%date:~8,2%.log

echo ===== WEEKLY DEEP AUDIT %date% %time% ===== > "%LOG%"
"%PY%" health_check.py --full >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo ----- yuho_audit --days 14 ----- >> "%LOG%"
"%PY%" yuho_audit.py --days 14 >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo ----- audit_yuho_coverage (全量) ----- >> "%LOG%"
"%PY%" financial_analysis_system\audit_yuho_coverage.py --years 2025,2026 >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo ----- rotate_logs (60日超をarchiveへ) ----- >> "%LOG%"
"%PY%" rotate_logs.py --apply >> "%LOG%" 2>&1
echo ===== DONE %time% ===== >> "%LOG%"
