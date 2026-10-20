# -*- coding: utf-8 -*-
# Build dell'eseguibile Windows con Nuitka.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Produce dist\MagnetScout.exe: compilato in C (niente bytecode impacchettato),
# nessuna installazione richiesta all'utente finale.

param(
    [string]$Python = "",
    [switch]$Standalone
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not $Python) {
    $venv = Join-Path $root ".venv-build\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}

$version = (& $Python -c "import version; print(version.__version__)").Trim()
if (-not $version) { throw "Versione non trovata in version.py" }
Write-Host "MagnetScout $version - build Nuitka" -ForegroundColor Cyan

$mode = if ($Standalone) { "--standalone" } else { "--onefile" }

$nuitkaArgs = @(
    "-m", "nuitka",
    $mode,
    "--assume-yes-for-downloads",
    "--windows-console-mode=force",
    "--include-data-dir=static=static",
    "--company-name=NoShellButCloud",
    "--product-name=MagnetScout",
    "--file-version=$version",
    "--product-version=$version",
    "--file-description=MagnetScout - portale locale per torrent e XDCC",
    "--copyright=Progetto didattico, nessuna garanzia",
    "--output-dir=dist",
    "--output-filename=MagnetScout.exe",
    "--nofollow-import-to=tkinter,unittest,doctest,pydoc,test,idlelib,lib2to3,ensurepip,venv,setuptools,pip",
    "--remove-output",
    "server.py"
)

& $Python @nuitkaArgs
# Nuitka scrive l'avanzamento su stderr: con ErrorActionPreference=Stop
# PowerShell lo tratterebbe come errore terminante. Conta l'exit code.
$code = $LASTEXITCODE
if ($code -ne 0) { throw "Build Nuitka fallita (codice $code)" }

$exe = Join-Path $root "dist\MagnetScout.exe"
if (-not (Test-Path $exe)) { throw "Eseguibile non prodotto: $exe" }
$size = [math]::Round((Get-Item $exe).Length / 1MB, 2)
Write-Host "Pronto: dist\MagnetScout.exe ($size MB)" -ForegroundColor Green
