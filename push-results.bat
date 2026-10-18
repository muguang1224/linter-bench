@echo off
setlocal
cd /d "%~dp0"
git add -A
git -c user.name="muguang1224" -c user.email="muguang1224@users.noreply.github.com" commit -m "docs: add real-tool results and write-up" 2>nul
git push origin main
echo.
echo Done. Verify:
echo   https://github.com/muguang1224/linter-bench/tree/main/results/2026-10-18
pause
