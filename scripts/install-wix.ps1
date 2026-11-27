#Requires -Version 7
# Installs WiX 7.0.1 from nuget.org, checked against the SHA-512 that nuget.org publishes for it (its registration
# catalog) BEFORE anything from it runs. `dotnet tool install` would fetch whatever the feed serves at that version,
# unchecked. installer\bigdownloader.wxs uses no extension, so the wix package alone is enough.
# Puts a `wix` command on PATH ($env:GITHUB_PATH in Actions).
param([string]$ToolDir = (Join-Path ([IO.Path]::GetTempPath()) 'wix-7.0.1'))
$ErrorActionPreference = 'Stop'

$version = '7.0.1'
$sha512 = 'nHKhfUNeDGQvK6bXhCSklSb810PMLQS9HgU9Hsri3RLWP6piAceVd0kON3Vmd/Wo199/VJjmS/mWAY6+F7N9cA=='

New-Item -ItemType Directory -Force $ToolDir | Out-Null
$nupkg = Join-Path $ToolDir "wix.$version.nupkg"
Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/wix/$version/wix.$version.nupkg" -OutFile $nupkg -UseBasicParsing
$actual = [Convert]::ToBase64String([Security.Cryptography.SHA512]::HashData([IO.File]::ReadAllBytes($nupkg)))
if ($actual -ne $sha512) {
    Remove-Item $nupkg -Force
    throw "wix $version does not match nuget.org's published SHA-512. Refusing to use it."
}
$target = Join-Path $ToolDir 'wix'
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Expand-Archive $nupkg -DestinationPath $target

# A `wix` command running the verified tool.
$dll = Join-Path $target 'tools\net8.0\any\wix.dll'
Set-Content (Join-Path $ToolDir 'wix.cmd') "@dotnet `"$dll`" %*" -Encoding ascii
if ($env:GITHUB_PATH) { $ToolDir >> $env:GITHUB_PATH }
$env:PATH = "$ToolDir;$env:PATH"
# WiX 7 needs its Open Source Maintenance Fee EULA accepted once per machine.
& (Join-Path $ToolDir 'wix.cmd') eula accept wix7
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& (Join-Path $ToolDir 'wix.cmd') --version
