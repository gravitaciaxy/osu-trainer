@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Python: портативный из папки программы (ставит install.ps1) или системный
set "PYEXE="
set "PYARGS="
if exist "%~dp0runtime\python\python.exe" set "PYEXE=%~dp0runtime\python\python.exe"
if not defined PYEXE python --version >nul 2>nul && set "PYEXE=python"
if not defined PYEXE py -3 --version >nul 2>nul && set "PYEXE=py" && set "PYARGS=-3"
if not defined PYEXE goto nopython

rem Node.js: портативный из папки программы, если есть
if exist "%~dp0runtime\node\node.exe" set "PATH=%~dp0runtime\node;%PATH%"
if not exist "%~dp0node_modules\realm" call "%~dp0setup.bat"

"%PYEXE%" %PYARGS% "%~dp0ui.py" %*
if errorlevel 1 pause
exit /b 0

:nopython
echo Не найден Python. Установи osu!trainer одной командой (см. README) или поставь Python 3.9+.
pause
exit /b 1
