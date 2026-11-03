@echo off
setlocal enabledelayedexpansion
title Smart Factory Maintenance OS (Vercel Edition)
cd /d "%~dp0"

echo ======================================================================
echo    SMART FACTORY MAINTENANCE PLATFORM (Vercel Edition)
echo ======================================================================
echo.

:: Detect Python launcher or python executable
set "PY_CMD="
where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python 3.10 or newer was not found on your system.
    echo Please install Python from https://www.python.org/
    echo Ensure "Add Python to PATH" is checked during installation.
    echo.
    pause
    exit /b 1
)

echo [1/3] Checking Python runtime...
%PY_CMD% --version
if errorlevel 1 (
    echo [ERROR] Failed to run Python.
    pause
    exit /b 1
)

echo [2/3] Checking dependencies...
%PY_CMD% -c "import fastapi, uvicorn, pydantic" >nul 2>nul
if errorlevel 1 (
    echo Dependencies not found. Installing packages from requirements.txt...
    %PY_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Could not install required dependencies.
        pause
        exit /b 1
    )
) else (
    echo All dependencies are satisfied.
)

echo [3/3] Launching Factory Maintenance OS...
echo Opening local dashboard in your browser...
echo.
%PY_CMD% main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Application terminated unexpectedly.
    pause
)
