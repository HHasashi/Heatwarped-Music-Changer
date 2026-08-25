@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo       HEATWARPED MUSIC PATCHER
echo ========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 heatwarped_patcher.py
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python heatwarped_patcher.py
    ) else (
        echo ERROR: Python 3 was not found.
        echo Install Python 3 from https://www.python.org/ and enable "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )
)

if not %errorlevel%==0 (
    echo.
    echo Patch failed. Read the ERROR above.
    pause
    exit /b 1
)

echo.
pause
