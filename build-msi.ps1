#Requires -Version 7
# Builds dist\BigDownloader.exe with build.bat, then a per-user installer:
# dist\BigDownloader-<version>-x64.msi. Needs WiX 7 (pwsh ./scripts/install-wix.ps1, checked by hash). The version is the
# latest tag unless given.
param([string]$Version)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not $Version) {
    $Version = (git describe --tags --abbrev=0) -replace '^v', ''
    while ($Version.Split('.').Count -lt 3) { $Version += '.0' }
}

# build.bat ignores PyInstaller's exit code: a failed build would leave the previous exe to package.
# Only the exe is replaced; downloads and cookies kept in dist\ are not touched.
Remove-Item dist\BigDownloader.exe -ErrorAction SilentlyContinue
if (Test-Path dist\BigDownloader.exe) { throw 'dist\BigDownloader.exe is in use: close Big Downloader first' }
# .\ because cmd may be told not to search the current folder (NoDefaultCurrentDirectoryInExePath);
# nul answers the final pause.
cmd /c ".\build.bat < nul"
if (-not (Test-Path dist\BigDownloader.exe)) { throw 'build.bat did not produce dist\BigDownloader.exe' }

wix build installer\bigdownloader.wxs -arch x64 -ext WixToolset.Util.wixext -d Version=$Version -o "dist\BigDownloader-$Version-x64.msi"
if ($LASTEXITCODE) { exit $LASTEXITCODE }
"dist\BigDownloader-$Version-x64.msi"
