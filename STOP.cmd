@echo off
cd /d "%~dp0"
if not exist .mcbe-lab mkdir .mcbe-lab
type nul > .mcbe-lab\STOP
echo Legacy desktop input stop requested. The simulator never controls your computer.
