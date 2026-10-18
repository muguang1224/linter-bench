@echo off
setlocal
cd /d "%~dp0"

if not exist .git (
  git init
)
git add -A
git -c user.name="muguang1224" -c user.email="muguang1224@users.noreply.github.com" commit -m "linter-bench v0.1.0"
git tag -a v0.1.0 -m "linter-bench v0.1.0" 2>nul
git branch -M main

where gh >nul 2>nul
if %ERRORLEVEL%==0 (
  gh repo create linter-bench --public --description "A small, labeled benchmark for static checkers: precision, recall, F1, crashes." 2>nul
)

git remote remove origin 2>nul
git remote add origin https://github.com/muguang1224/linter-bench.git
git push -u origin main
git push origin v0.1.0

where gh >nul 2>nul
if %ERRORLEVEL%==0 (
  gh release create v0.1.0 --title "linter-bench v0.1.0" --notes "20 labeled Python cases; tool-agnostic adapters; precision/recall/F1 plus crashes. 102 tests; zero dependencies." 2>nul
)

echo.
echo Done. Verify:
echo   https://github.com/muguang1224/linter-bench
echo   https://github.com/muguang1224/linter-bench/releases/latest
pause
