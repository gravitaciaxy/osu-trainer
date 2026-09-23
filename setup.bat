@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === osu!trainer: установка зависимостей ===
echo (проще всего поставить программу одной командой из README - она сама всё скачает)

set "NPM=npm"
if exist "%~dp0runtime\node\npm.cmd" (
  set "PATH=%~dp0runtime\node;%PATH%"
  set "NPM=%~dp0runtime\node\npm.cmd"
  goto node_ok
)
node --version >nul 2>nul || goto nonode
:node_ok

call "%NPM%" ci --no-audit --no-fund --loglevel=error || call "%NPM%" install --no-audit --no-fund --loglevel=error || goto fail

echo.
echo Готово! Запускай start.bat
pause
exit /b 0

:nonode
echo Не найден Node.js 18+. Установи LTS-версию: https://nodejs.org/ и запусти setup.bat снова.
pause
exit /b 1

:fail
echo Установка не удалась - смотри сообщения выше.
pause
exit /b 1
