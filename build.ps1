$ErrorActionPreference = "Stop"

$python = ".\.venv\Scripts\python.exe"
$appBaseName = "DataloggerVGC_realtime"
$distDir = Join-Path $PSScriptRoot "dist"
$runId = Get-Date -Format "yyyyMMdd_HHmmss"
$appName = "${appBaseName}_${runId}"
$distExe = Join-Path $distDir "$appName.exe"
$workRoot = Join-Path $PSScriptRoot "build\pyinstaller_$runId"
$specRoot = Join-Path $PSScriptRoot "build\spec_$runId"
$condaRoot = "C:\ProgramData\miniconda3"
$condaBin = Join-Path $condaRoot "Library\bin"
$tclLib = Join-Path $condaRoot "Library\lib\tcl8.6"
$tkLib = Join-Path $condaRoot "Library\lib\tk8.6"

New-Item -ItemType Directory -Path $distDir -Force | Out-Null
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
New-Item -ItemType Directory -Path $specRoot -Force | Out-Null

Get-ChildItem -LiteralPath $distDir -Filter "RCX*.tmp" -ErrorAction SilentlyContinue |
  ForEach-Object {
    try {
      Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
    }
    catch {
      Write-Warning "Skipping locked temp file: $($_.Name)"
    }
  }

$env:TCL_LIBRARY = $tclLib
$env:TK_LIBRARY = $tkLib

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --distpath $distDir `
  --workpath $workRoot `
  --specpath $specRoot `
  --collect-submodules tkinter `
  --add-binary "$condaBin\tcl86t.dll;." `
  --add-binary "$condaBin\tk86t.dll;." `
  --add-binary "$condaBin\libmpdec-4.dll;." `
  --add-binary "$condaBin\libcrypto-3-x64.dll;." `
  --add-binary "$condaBin\libssl-3-x64.dll;." `
  --add-binary "$condaBin\liblzma.dll;." `
  --add-binary "$condaBin\libbz2.dll;." `
  --add-binary "$condaBin\ffi.dll;." `
  --add-data "$tclLib;tcl8.6" `
  --add-data "$tkLib;tk8.6" `
  --name $appName `
  vgc50x_logger.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path $distExe)) {
  throw "Build finished without producing $distExe"
}

Write-Host ""
Write-Host "Build complete: $distExe"
