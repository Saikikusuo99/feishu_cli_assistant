@echo off
title AI Agent - Client

echo ================================================
echo     AI Agent Feishu Automation - Client
echo ================================================
echo.

python "%~dp0client\main.py"

if %errorlevel% neq 0 (
    echo.
    echo ================================================
    echo  Client exited with error code: %errorlevel%
    echo ================================================
)

echo.
echo ================================================
echo  Client exited. Press any key to close.
echo ================================================
pause >nul