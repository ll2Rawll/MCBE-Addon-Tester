@echo off
setlocal
cd /d "%~dp0"
node -e "if(Number(process.versions.node.split('.')[0]) < 20) process.exit(1)"
if errorlevel 1 goto failed
call npm ci --prefix runtime
if errorlevel 1 goto failed
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
echo Installation failed. Install Python 3.11+ and Node.js 20+ with npm, then retry. See the error above.
pause
exit /b 1
