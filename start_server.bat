@echo off
title AI Agent - Server

echo ================================================
echo     AI Agent Feishu Automation - Server
echo ================================================
echo.

python "%~dp0server\main.py"

echo.
echo ================================================
echo  Server stopped. Press any key to close.
echo ================================================
pause >nul