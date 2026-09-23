# osu!trainer - one-line installer for Windows (PowerShell):
#
#   irm https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.ps1 | iex
#
# Installs the app to %LOCALAPPDATA%\osu-trainer. If Python 3.9+ or Node.js 18+ is missing, portable
# copies are downloaded into the app folder (nothing is installed system-wide).
# Running it again updates the app; settings and cache are kept.
#
# Keep this file pure ASCII: Windows PowerShell 5.1 reads BOM-less script files in the ANSI code
# page, and a UTF-8 BOM would break "irm | iex" (irm keeps U+FEFF at the start of the string).

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'gravitaciaxy/osu-trainer'
$Dir = Join-Path $env:LOCALAPPDATA 'osu-trainer'
# for testing the installer: custom folder, custom archive URL, forced portable Python/Node
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
    throw "Download failed: $($urls -join ', ')"
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
    Say 'Downloading the app...'
    $zip = Join-Path $Tmp 'app.zip'
    $sources = @("https://github.com/$Repo/releases/latest/download/osu-trainer.zip",
                 "https://github.com/$Repo/archive/refs/heads/main.zip")
    if ($env:OSU_TRAINER_ZIP) { $sources = @($env:OSU_TRAINER_ZIP) }
    Get-File $sources $zip | Out-Null
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Tmp 'app') -Force
    $root = Get-ChildItem (Join-Path $Tmp 'app') -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    # app files are replaced; cache, backups, downloads, runtime and config.json are kept
    Get-ChildItem $root.FullName -Force | ForEach-Object {
        Copy-Item $_.FullName -Destination $Dir -Recurse -Force
    }

    $pyDir = Join-Path $Dir 'runtime\python'
    if (-not (Test-Path (Join-Path $pyDir 'python.exe'))) {
        if ($Force -or -not ((Test-Python 'python' @()) -or (Test-Python 'py' @('-3')))) {
            Say "Python not found - downloading portable Python $PyVersion (for osu!trainer only)..."
            $pz = Join-Path $Tmp 'python.zip'
            Get-File @("https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip") $pz | Out-Null
            Expand-Archive $pz -DestinationPath $pyDir -Force
        }
    }

    $nodeDir = Join-Path $Dir 'runtime\node'
    if (-not (Test-Path (Join-Path $nodeDir 'node.exe')) -and ($Force -or -not (Test-Node))) {
        Say 'Node.js not found - downloading portable Node.js LTS (for osu!trainer only)...'
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
    Say 'Installing the osu! database module (realm)...'
    Push-Location $Dir
    try {
        & $npm ci --no-audit --no-fund --loglevel=error
        if ($LASTEXITCODE -ne 0) { & $npm install --no-audit --no-fund --loglevel=error }
        if ($LASTEXITCODE -ne 0) { throw 'npm could not install the dependencies' }
    } finally { Pop-Location }

    if ($env:OSU_TRAINER_NO_SHORTCUTS -ne '1') {
        Say 'Creating Start menu and desktop shortcuts...'
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

    Say "Done! Installed to $Dir"
    if ($env:OSU_TRAINER_NO_START -ne '1') {
        Start-Process -FilePath (Join-Path $Dir 'start.bat') -WorkingDirectory $Dir
    }
}
catch {
    Write-Host "[osu!trainer] Installation failed: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}
