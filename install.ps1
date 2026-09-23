# osu!trainer - установка одной командой (Windows, PowerShell):
#
#   irm https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.ps1 | iex
#
# Ставит программу в %LOCALAPPDATA%\osu-trainer. Если в системе нет Python 3.9+ или Node.js 18+,
# скачивает их портативные версии в папку программы (в систему ничего не устанавливается).
# Повторный запуск обновляет программу, настройки и кэш сохраняются.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'gravitaciaxy/osu-trainer'
$Dir = Join-Path $env:LOCALAPPDATA 'osu-trainer'
# для проверки установщика: своя папка, свой адрес архива, принудительно портативные Python/Node
if ($env:OSU_TRAINER_DIR) { $Dir = $env:OSU_TRAINER_DIR }
$Force = ($env:OSU_TRAINER_FORCE_PORTABLE -eq '1')
$PyVersion = '3.12.10'
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ('osu-trainer-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

function Say($m) { Write-Host "[osu!trainer] $m" -ForegroundColor Magenta }

function Get-File($urls, $out) {
    foreach ($u in $urls) {
        try { Invoke-WebRequest -Uri $u -OutFile $out -UseBasicParsing; return $u } catch { }
    }
    throw "Не удалось скачать: $($urls -join ', ')"
}

function Test-Python($exe, $extra) {
    try {
        $v = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]'3.9')) { return $true }
    } catch { }
    return $false
}

function Test-Node {
    try {
        $v = & node --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match '^v(\d+)') { return ([int]$Matches[1] -ge 18) }
    } catch { }
    return $false
}

try {
    Say 'Скачиваю программу...'
    $zip = Join-Path $Tmp 'app.zip'
    $sources = @("https://github.com/$Repo/releases/latest/download/osu-trainer.zip",
                 "https://github.com/$Repo/archive/refs/heads/main.zip")
    if ($env:OSU_TRAINER_ZIP) { $sources = @($env:OSU_TRAINER_ZIP) }
    Get-File $sources $zip | Out-Null
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Tmp 'app') -Force
    $root = Get-ChildItem (Join-Path $Tmp 'app') -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    # файлы программы заменяются, а cache, backups, downloads, runtime и config.json остаются
    Get-ChildItem $root.FullName -Force | ForEach-Object {
        Copy-Item $_.FullName -Destination $Dir -Recurse -Force
    }

    $pyDir = Join-Path $Dir 'runtime\python'
    if (-not (Test-Path (Join-Path $pyDir 'python.exe'))) {
        if ($Force -or -not ((Test-Python 'python' @()) -or (Test-Python 'py' @('-3')))) {
            Say "Python не найден - скачиваю портативный Python $PyVersion (только для osu!trainer)..."
            $pz = Join-Path $Tmp 'python.zip'
            Get-File @("https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip") $pz | Out-Null
            Expand-Archive $pz -DestinationPath $pyDir -Force
        }
    }

    $nodeDir = Join-Path $Dir 'runtime\node'
    if (-not (Test-Path (Join-Path $nodeDir 'node.exe')) -and ($Force -or -not (Test-Node))) {
        Say 'Node.js не найден - скачиваю портативный Node.js LTS (только для osu!trainer)...'
        $index = Invoke-RestMethod 'https://nodejs.org/dist/index.json'
        $lts = $index | Where-Object { $_.lts -and ($_.files -contains 'win-x64-zip') } | Select-Object -First 1
        $name = "node-$($lts.version)-win-x64"
        $nz = Join-Path $Tmp 'node.zip'
        Get-File @("https://nodejs.org/dist/$($lts.version)/$name.zip") $nz | Out-Null
        Expand-Archive $nz -DestinationPath $Tmp -Force
        if (Test-Path $nodeDir) { Remove-Item $nodeDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path (Split-Path $nodeDir) | Out-Null
        Move-Item (Join-Path $Tmp $name) $nodeDir
    }

    $npm = 'npm.cmd'
    if (Test-Path (Join-Path $nodeDir 'node.exe')) {
        $env:Path = "$nodeDir;$env:Path"
        $npm = Join-Path $nodeDir 'npm.cmd'
    }
    Say 'Ставлю модуль для работы с базой osu! (realm)...'
    Push-Location $Dir
    try {
        & $npm ci --no-audit --no-fund --loglevel=error
        if ($LASTEXITCODE -ne 0) { & $npm install --no-audit --no-fund --loglevel=error }
        if ($LASTEXITCODE -ne 0) { throw 'npm не смог установить зависимости' }
    } finally { Pop-Location }

    if ($env:OSU_TRAINER_NO_SHORTCUTS -ne '1') {
        Say 'Создаю ярлыки в меню «Пуск» и на рабочем столе...'
        $shell = New-Object -ComObject WScript.Shell
        $icon = Join-Path $env:LOCALAPPDATA 'osulazer\current\osu!.exe'
        foreach ($folder in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
            $lnk = $shell.CreateShortcut((Join-Path $folder 'osu!trainer.lnk'))
            $lnk.TargetPath = Join-Path $Dir 'start.bat'
            $lnk.WorkingDirectory = $Dir
            if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
            $lnk.Save()
        }
    }

    Say "Готово! Программа в $Dir"
    if ($env:OSU_TRAINER_NO_START -ne '1') {
        Start-Process -FilePath (Join-Path $Dir 'start.bat') -WorkingDirectory $Dir
    }
}
catch {
    Write-Host "[osu!trainer] Ошибка установки: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}
