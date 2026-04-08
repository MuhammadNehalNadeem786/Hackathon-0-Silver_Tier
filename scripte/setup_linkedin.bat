@echo off
REM LinkedIn Poster Setup Script for Windows
REM This script installs dependencies and tests browser automation

echo ======================================
echo LinkedIn Poster Setup
echo ======================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.13+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] Installing Playwright...
pip install playwright
if errorlevel 1 (
    echo ERROR: Failed to install Playwright
    pause
    exit /b 1
)
echo Playwright installed successfully!
echo.

echo [2/3] Installing Chromium browser for Playwright...
playwright install chromium
if errorlevel 1 (
    echo WARNING: Chromium installation failed, will try with default browser
)
echo Browser installed!
echo.

echo [3/3] Running DRY RUN test...
echo.
echo NOTE: This test will NOT post to LinkedIn, only simulate
echo.

python "%~dp0linkedin_poster.py" --vault "%~dp0..\AI_Employee_Vault" --dry-run

echo.
echo ======================================
echo Setup Complete!
echo ======================================
echo.
echo To use the LinkedIn Poster:
echo.
echo 1. Create a post file in Needs_Action folder:
echo    Example: POST_test.md
echo.
echo 2. The orchestrator will detect it and create an approval request
echo.
echo 3. Move the approval file to Approved folder to publish
echo.
echo IMPORTANT: Set your LinkedIn credentials in .env file:
echo   LINKEDIN_EMAIL=your_email@example.com
echo   LINKEDIN_PASSWORD=your_password
echo.
echo Set DRY_RUN=false in .env when ready to post for real
echo.
pause
