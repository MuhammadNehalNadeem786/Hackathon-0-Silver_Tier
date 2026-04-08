@echo off
REM Gmail Watcher Setup Script for Windows
REM This script installs dependencies and guides you through OAuth setup

echo ======================================
echo Gmail Watcher Setup
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

echo [1/4] Installing Python dependencies...
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib python-dotenv
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo Dependencies installed successfully!
echo.

echo [2/4] Checking credentials.json...
if not exist "%~dp0..\credentials.json" (
    echo ERROR: credentials.json not found in project root
    echo Please download it from Google Cloud Console
    pause
    exit /b 1
)
echo credentials.json found!
echo.

echo [3/4] First-time OAuth Authorization
echo ======================================
echo The script will now open your browser to authorize the app with Gmail.
echo.
echo Steps:
echo 1. Select your Google account
echo 2. Click "Allow" to grant read-only access
echo 3. Copy the authorization code if prompted
echo.
pause

REM Run the Gmail watcher in auth-only mode
echo [4/4] Starting OAuth flow...
python "%~dp0gmail_watcher.py" --vault "%~dp0..\AI_Employee_Vault" --credentials "%~dp0..\credentials.json" --once

echo.
echo ======================================
echo Setup Complete!
echo ======================================
echo.
echo To run the Gmail Watcher:
echo   python gmail_watcher.py --vault "S:\Personal AI Employee\Autonomous FTEs\AI_Employee_Vault"
echo.
echo The watcher will:
echo   - Check for unread important emails every 2 minutes
echo   - Create action files in Needs_Action folder
echo   - Log all activity to Logs folder
echo.
pause
