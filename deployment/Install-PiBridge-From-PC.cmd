@echo off
title Insteon Bridge - Remote Raspberry Pi Installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-PiBridge-From-PC.ps1"
echo.
pause
