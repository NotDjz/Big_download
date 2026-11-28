#Requires -Version 7
# The installer's whole life on a disposable Windows machine (a CI runner): install, start, cut a video through the
# installed app's own routes (its bundled FFmpeg included), upgrade with the app running, relaunch, uninstall while
# running. Every check is recorded in <Out>\lifecycle.json; the exit code is the number of failed checks.
# It installs over the per-user Big Downloader and uninstalls it at the end: NEVER run it on a computer you use.
# -Phase runs one part (CI runs each as its own step, so a runner that dies mid-test still shows which part killed
# it); the checks carry over in <Out>\state.json, and only the "report" phase (or "all") exits with the count.
param(
    [Parameter(Mandatory)][string]$Old,
    [Parameter(Mandatory)][string]$New,
    [string]$Sample, # a short video, needed by the start phase only
    [string]$Out = (Join-Path ([IO.Path]::GetTempPath()) 'msi-lifecycle'),
    [ValidateSet('all', 'install', 'start', 'upgrade', 'relaunch', 'uninstall', 'report')][string]$Phase = 'all'
)
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force $Out | Out-Null
$state = Join-Path $Out 'state.json'
$checks = [ordered]@{}
$cut = $null
if ($Phase -notin 'all', 'install' -and (Test-Path $state)) {
    $saved = Get-Content $state -Raw | ConvertFrom-Json -AsHashtable
    $checks = $saved.checks
    $cut = $saved.cut
}
function Runs($part) { $Phase -in 'all', $part }
$dir = "$env:LOCALAPPDATA\Programs\BigDownloader"
$exe = "$dir\BigDownloader.exe"
$cookies = "$dir\cookies.txt"
$shortcut = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Big Downloader.lnk"
$appKey = 'HKCU:\Software\NotDjz\BigDownloader'
$base = 'http://127.0.0.1:5555'
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public static class Win {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public static bool Visible(string title) {
    bool found = false;
    EnumWindows((h, l) => { var s = new StringBuilder(128); GetWindowText(h, s, 128);
      if (s.ToString() == title && IsWindowVisible(h)) { found = true; return false; } return true; }, IntPtr.Zero);
    return found;
  }
}
"@

# Each check is printed as it happens: a step that hangs then shows where.
function Check($name, $ok, $detail = '') {
    $checks[$name] = [ordered]@{ ok = [bool]$ok; detail = "$detail" }
    '{0}  {1}  {2}' -f ($ok ? 'ok  ' : 'FAIL'), $name, $detail | Write-Host
}
# A check whose expression throws would be skipped and never counted: it fails instead.
trap { Check "threw: $($_.InvocationInfo.Line.Trim())" $false $_; continue }
function Step($what) { "[$(Get-Date -Format HH:mm:ss)] $what" | Write-Host }
function Msi($verb, $msi, $log) {
    # Wait on msiexec itself only: -Wait would also wait for an app Restart Manager relaunches.
    # /norestart: under /qn a file in use may reboot the machine. Without the reboot it exits 3010, which the exit-0
    # checks report.
    Step "msiexec $verb $(Split-Path $msi -Leaf)"
    $p = Start-Process msiexec -ArgumentList $verb, "`"$msi`"", '/qn', '/norestart', '/l*v', "`"$Out\$log`"" -PassThru
    if (-not $p.WaitForExit(300000)) {
        $p.Kill($true)
        return 'timed out after 5 min'
    }
    $p.ExitCode
}
# PyInstaller onefile: each running app is two BigDownloader processes, the bootloader and the app it unpacked.
function Processes { @(Get-Process BigDownloader -ErrorAction SilentlyContinue) }
function Listeners { @(Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue).Count }
function Products {
    @(Get-ChildItem HKCU:\Software\Microsoft\Installer\Products -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty $_.PSPath).ProductName -eq 'Big Downloader' }).Count
}
function Launch {
    [Diagnostics.Process]::Start([Diagnostics.ProcessStartInfo]@{ FileName = $exe; WorkingDirectory = $dir; UseShellExecute = $false })
}
# The page answers once Flask is up; the window comes right after.
function Serving {
    foreach ($i in 1..60) {
        try { if ((Invoke-WebRequest "$base/" -TimeoutSec 2).StatusCode -eq 200) { return $true } } catch {}
        Start-Sleep -Milliseconds 500
    }
    $false
}
function Kept { $cut -and (Test-Path $cut) -and (Test-Path $cookies) }
# GitHub Actions kills what a step started when the step ends: an app launched by the start phase is gone by the
# upgrade phase, which then tested an upgrade with nothing running (measured: 0 processes). Each phase that needs
# the app open starts it itself, and checks it really runs before msiexec.
function EnsureRunning($when) {
    if ((Listeners) -eq 0) { Step "launch the app ($when)"; $null = Launch; $null = Serving }
    Check "app running $when" ((Listeners) -eq 1) "$((Processes).Count) process(es)"
}

if (Runs install) {
$code = Msi '/i' $Old 'install.log'
Check 'install exits 0' ($code -eq 0) $code
Check 'exe installed per user' (Test-Path $exe)
Check 'Start Menu shortcut' (Test-Path $shortcut)
Check 'HKCU key path written' ((Get-ItemProperty $appKey -ErrorAction SilentlyContinue).Installed -eq 1)
Check 'one product registered' ((Products) -eq 1) (Products)
}

if (Runs start) {
Step 'launch the app'
$app = Launch
Check 'app serves its page' (Serving) ($app.HasExited ? ('exited with 0x{0:X8}' -f $app.ExitCode) : '')
$window = $false
foreach ($i in 1..20) { if ([Win]::Visible('Big Downloader')) { $window = $true; break }; Start-Sleep -Milliseconds 500 }
Check 'window visible' $window
Check 'library folders created beside the exe' (Test-Path "$dir\downloads\Videos")

# A real cut through the installed app: upload, cut the whole clip, follow the SSE stream to its end. The stream
# closes on the terminal event, so reading it whole returns once the cut is over.
Step 'cut a video through the installed app'
try {
    $up = Invoke-RestMethod "$base/upload-for-cut" -Method Post -Form @{ file = Get-Item $Sample } -TimeoutSec 60
    $body = @{ temp_name = $up.temp_name; original_name = $up.original_name; start = 0; end = $up.duration } | ConvertTo-Json
    $job = Invoke-RestMethod "$base/cut-uploaded" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 30
    $stream = (Invoke-WebRequest "$base/cut-progress/$($job.cut_id)" -TimeoutSec 180).Content
    $final = ($stream -split "`n" | Where-Object { $_ -like 'data: *' } | Select-Object -Last 1) -replace '^data: ', '' | ConvertFrom-Json
    Check 'cut completes with the bundled FFmpeg' ($final.status -eq 'complete') ($final | ConvertTo-Json -Compress)
    $cut = "$dir\downloads\Videos\$($final.filename)"
    Check 'cut output in the library' ((Test-Path $cut) -and (Get-Item $cut).Length -gt 0) $cut
} catch {
    Check 'cut completes with the bundled FFmpeg' $false $_
}

# The user's own data, which no upgrade or uninstall may touch.
Set-Content $cookies "# Netscape HTTP Cookie File`n" -Encoding ascii
}

if (Runs upgrade) {
EnsureRunning 'before the upgrade'
$code = Msi '/i' $New 'upgrade.log'
Check 'upgrade with the app running exits 0 (no reboot)' ($code -eq 0) $code
Start-Sleep -Seconds 10
Check 'never two servers after the upgrade' ((Listeners) -le 1) (Listeners)
Check 'one product registered after the upgrade' ((Products) -eq 1) (Products)
Check 'exe present after the upgrade' (Test-Path $exe)
Check 'downloads and cookies kept by the upgrade' (Kept)
}

if (Runs relaunch) {
if ((Listeners) -eq 0) { Step 'relaunch the upgraded app'; $null = Launch }
Check 'upgraded app serves its page' (Serving)
}

if (Runs uninstall) {
EnsureRunning 'before the uninstall'
$code = Msi '/x' $New 'uninstall.log'
Check 'uninstall while running exits 0' ($code -eq 0) $code
Start-Sleep -Seconds 3
Check 'nothing left running' ((Processes).Count -eq 0) "$((Processes).Count) process(es)"
Check 'exe removed' (-not (Test-Path $exe))
Check 'shortcut removed' (-not (Test-Path $shortcut))
Check 'HKCU key path value removed' ($null -eq (Get-ItemProperty $appKey -ErrorAction SilentlyContinue).Installed)
Check 'product unregistered' ((Products) -eq 0) (Products)
Check 'downloads and cookies kept by the uninstall' (Kept)
}

@{ checks = $checks; cut = $cut } | ConvertTo-Json -Depth 4 | Set-Content $state -Encoding utf8
if (-not (Runs report)) { exit 0 }
$failed = @($checks.Keys | Where-Object { -not $checks[$_].ok })
[ordered]@{ checks = $checks; failed = $failed } | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Out 'lifecycle.json') -Encoding utf8
"$($failed.Count) failed: $($failed -join '; ')" | Write-Host
exit $failed.Count
