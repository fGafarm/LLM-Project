@echo off
echo Starting S&P500 FY2024 batch analysis...
echo Started at: %date% %time%
cd /d "C:\Users\shun nabeno\Desktop\Local LLM Project\us_financial_analysis"
"C:\Users\shun nabeno\AppData\Local\Python\bin\python.exe" -u us_analyzer.py --list sp500.txt --year 2024 --skip-existing
echo.
echo Finished at: %date% %time%
pause
