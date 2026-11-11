@echo off
title Insteon Network Studio - Desktop Shortcut Installer
echo ==================================================================
echo  Insteon Network Studio - Creating Desktop Shortcut...
echo ==================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Create-DesktopShortcut.ps1"
echo.
pause
