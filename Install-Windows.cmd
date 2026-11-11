@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -e ".[ai]"
if errorlevel 1 goto failed
.venv\Scripts\mcbe-test.exe capabilities
echo.
echo Simulator installed. No Minecraft installation or AI API key is needed.
echo Read docs\AI-WORKFLOW.md to connect your assistant to the MCP tools.
pause
exit /b 0
:failed
echo Installation failed. Install Python 3.11 or newer, then retry. See the error above.
pause
exit /b 1
