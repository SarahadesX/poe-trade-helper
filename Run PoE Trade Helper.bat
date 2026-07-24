@echo off
cd /d "%~dp0"

REM --- Auto-update from GitHub (only if this folder was cloned with git) ---
git rev-parse --is-inside-work-tree >nul 2>nul
if %errorlevel%==0 (
  echo Checking for updates...
  git pull --ff-only
  echo.
) else (
  echo Not a git clone - skipping auto-update.
  echo To get automatic updates, clone the repo instead of copying the folder.
  echo.
)

REM --- First run: create your local config from the template ---
if not exist "config.json" if exist "config.example.json" copy "config.example.json" "config.json" >nul

python app.py
pause
