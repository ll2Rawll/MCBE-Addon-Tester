@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -e ".[ai]"
if errorlevel 1 goto failed
.venv\Scripts\mcbe-test.exe doctor
echo.
echo Installed. Read README.md for the one-time Minecraft setup and AI connection.
pause
exit /b 0
:failed
echo Installation failed. Install Python 3.11 or newer, then retry. See the error above.
pause
exit /b 1
