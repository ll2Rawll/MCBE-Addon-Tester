@echo off
cd /d "%~dp0"
if not exist .mcbe-lab mkdir .mcbe-lab
type nul > .mcbe-lab\STOP
echo Input stop requested. Remove .mcbe-lab\STOP manually to resume.
