param(
    [Parameter(Mandatory)]
    [string]$TelemacRoot
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $TelemacRoot).Path
$project = $PSScriptRoot
foreach ($location in $root, $project) {
    if ($location -notmatch '^[A-Za-z]:\\[A-Za-z0-9_.\\-]+$') {
        throw 'Use a local Windows path containing ASCII letters, digits, periods, underscores, or hyphens, without spaces.'
    }
}
foreach ($relative in 'systel.cfg', 'dictionaries\telemac2d.dico', 'scripts\python3\telemac2d.py') {
    if (!(Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) {
        throw "Missing TELEMAC component: $relative"
    }
}

$external = Join-Path $root 'external'
$bundled = @(Get-ChildItem -LiteralPath $external -Directory -Filter 'python-*' |
    ForEach-Object { Join-Path $_.FullName 'python.exe' } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
if ($bundled.Count -ne 1) {
    throw 'Expected exactly one bundled Python interpreter in the TELEMAC installation.'
}
$basePython = $bundled[0]
$pythonRoot = Split-Path -Parent $basePython
$venv = Join-Path $project '.venv'
$runtime = Join-Path $venv 'Scripts\python.exe'
$uv = (Get-Command uv.exe -ErrorAction Stop).Source
$env:UV_CACHE_DIR = Join-Path $project '.uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:TEMP = Join-Path $project '.tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP, (Join-Path $project 'cases') -Force | Out-Null

if (!(Test-Path -LiteralPath $runtime -PathType Leaf)) {
    if (Test-Path -LiteralPath $venv) { throw 'An incomplete .venv already exists. Use a clean checkout.' }
    & $uv venv --python $basePython --system-site-packages $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
} else {
    $venvConfig = Get-Content -LiteralPath (Join-Path $venv 'pyvenv.cfg')
    if ($venvConfig -notcontains "home = $pythonRoot") {
        throw 'Existing .venv uses a different Python installation. Use a clean checkout.'
    }
}
& $uv pip install --python $runtime -r (Join-Path $project 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the MCP dependencies.' }

$site = Join-Path $venv 'Lib\site-packages'
$bootstrap = @'
"""Register the native libraries bundled with TELEMAC on Windows."""

import os
from pathlib import Path

handles = []
if home := os.environ.get("HOMETEL"):
    root = Path(home)
    directories = [root / "bin"]
    directories.extend(package / "bin" for package in sorted((root / "external").iterdir()))
    handles = [os.add_dll_directory(str(path)) for path in directories if path.is_dir()]
'@
Set-Content -LiteralPath (Join-Path $site '_telemac_dll_paths.py') -Value $bootstrap -Encoding ascii
Set-Content -LiteralPath (Join-Path $site 'opentelemac-bundled.pth') -Value @(
    (Join-Path $pythonRoot 'Lib\site-packages'),
    (Join-Path $root 'bin'),
    'import _telemac_dll_paths'
) -Encoding ascii

$binPaths = @(Get-ChildItem -LiteralPath $external -Directory | ForEach-Object {
    Join-Path $_.FullName 'bin'
} | Where-Object { Test-Path -LiteralPath $_ -PathType Container })
$paths = (@((Join-Path $root 'scripts'), (Join-Path $root 'scripts\python3'),
    $pythonRoot, (Join-Path $root 'bin')) + $binPaths + '%PATH%') -join ';'
$runtimeLines = '@echo off', "set ""HOMETEL=$root""", "set ""PATH=$paths"""
$windowsNewline = [string][char]13 + [char]10
[IO.File]::WriteAllText((Join-Path $project '.runtime.cmd'),
    ($runtimeLines -join $windowsNewline) + $windowsNewline, [Text.Encoding]::ASCII)

& (Join-Path $project 'telemac-python.cmd') -B -c 'import _hermes; from mcp.server import MCPServer; from data_manip.extraction.telemac_file import TelemacFile; print(TelemacFile.__mro__[1].__name__)'
if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }
