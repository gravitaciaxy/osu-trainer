@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === osu!trainer: installing dependencies ===
echo (easiest: the one-line install command from the README downloads everything itself)

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
echo Done! Now run start.bat
pause
exit /b 0

:nonode
echo Node.js 18+ not found. Install the LTS version from https://nodejs.org/ and run setup.bat again.
pause
exit /b 1

:fail
echo Installation failed - see the messages above.
pause
exit /b 1
