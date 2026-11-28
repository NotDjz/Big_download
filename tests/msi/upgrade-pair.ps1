#Requires -Version 7
# From the MSI build-msi.ps1 just made, an upgrade pair: old.msi is that MSI, new.msi a second WiX build of the same
# version (a new package code, so installing it over old.msi is a major upgrade, as a release replacing the last one).
param([Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent | Split-Path -Parent)
$msi = Get-ChildItem dist -Filter 'BigDownloader-*-x64.msi' | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $msi) { throw 'No dist\BigDownloader-*-x64.msi: run build-msi.ps1 first' }
$version = $msi.Name -replace '^BigDownloader-(.+)-x64\.msi$', '$1'
New-Item -ItemType Directory -Force $Out | Out-Null
Copy-Item $msi.FullName (Join-Path $Out 'old.msi')
wix build installer\bigdownloader.wxs -arch x64 -ext WixToolset.Util.wixext -d Version=$version -o (Join-Path $Out 'new.msi')
exit $LASTEXITCODE
