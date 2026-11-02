@echo off
title BorsaAI Launcher
cd /d "%~dp0"
echo ==========================================
echo BorsaAI - Otomatik Baslatma Yoneticisi
echo ==========================================
echo.
echo Python sunucu ve bot baslatiliyor...
python start.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [HATA] BorsaAI baslatilamadi! Lutfen bagimliliklari kontrol edin.
    pause
)
