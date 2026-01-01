@echo off
chcp 65001 > nul
echo ============================================
echo EDINET 一括ダウンロード (2020-2025)
echo ============================================
echo.

set SCRIPT=backend\edinet_dl_filtered.py
set ENV=backend\.env

echo 開始: %date% %time%
echo.

for %%Y in (2020 2021 2022 2023 2024 2025) do (
    echo.
    echo ========== %%Y年 開始 ==========
    python %SCRIPT% --env %ENV% --year %%Y --mode all --parallel 2
    echo ========== %%Y年 完了 ==========
    echo.
    timeout /t 5 /nobreak > nul
)

echo.
echo ============================================
echo 全年完了: %date% %time%
echo ============================================
pause