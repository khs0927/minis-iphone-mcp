# setup-windows.ps1 — install and start the Minis PC agent on Windows.
#
#   powershell -ExecutionPolicy Bypass -File setup-windows.ps1 -Url http://192.168.1.10:8900 -Token mk-xxxx
#
# What it does:
#   1. downloads pc-agent.py from the Minis bridge
#   2. writes %USERPROFILE%\.minis-pc-agent.json (bridge url + token)
#   3. starts the agent in the background (hidden)
#   4. with -Install, registers a Scheduled Task so it starts at every logon
#
# The agent lets the Minis app on the iPhone run commands on this PC
# (list/kill processes, start apps, read files, ...) and read the output back.
param(
  [string]$Url,
  [string]$Token,
  [switch]$Install,
  [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$Home_ = $env:USERPROFILE
$AgentDir = Join-Path $Home_ ".minis-pc-agent"
$AgentPy  = Join-Path $AgentDir "pc-agent.py"
$Config   = Join-Path $Home_ ".minis-pc-agent.json"

function Say($m) { Write-Host $m -ForegroundColor Cyan }

# --- config -----------------------------------------------------------------
if (-not (Test-Path $Config)) { $Config = $null }
$cfg = $null
if (Test-Path $Config) {
  try { $cfg = Get-Content $Config -Raw | ConvertFrom-Json } catch { $cfg = $null }
}
if (-not $Url -and $cfg) { $Url = $cfg.urls[0] }
if (-not $Token -and $cfg) { $Token = $cfg.token }

if (-not $Url) { throw "Missing -Url (bridge base url, e.g. http://192.168.1.10:8900)" }
if (-not $Token) { throw "Missing -Token (bridge bearer token)" }
$Url = $Url.TrimEnd("/")

# --- locate python ----------------------------------------------------------
$py = $null
foreach ($cand in @("python", "python3", "py")) {
  $p = Get-Command $cand -ErrorAction SilentlyContinue
  if ($p) { $py = $p.Source; break }
}
if (-not $py) { throw "Python 3 not found in PATH. Install it from python.org (tick 'Add to PATH')." }
Say "python: $py"

# --- download the agent -----------------------------------------------------
New-Item -ItemType Directory -Force -Path $AgentDir | Out-Null
$src = "$Url/download/pc-agent.py"
Say "downloading $src"
$headers = @{ Authorization = "Bearer $Token" }
try {
  Invoke-WebRequest -Uri $src -Headers $headers -OutFile $AgentPy -UseBasicParsing
} catch {
  throw "download failed: $($_.Exception.Message)`nCheck that the phone bridge is up and the token is correct."
}
Say "agent saved: $AgentPy"

# --- config -----------------------------------------------------------------
$urls = @($Url)
if ($cfg -and $cfg.urls) { $urls = @($cfg.urls) }
$json = @{ urls = $urls; token = $Token } | ConvertTo-Json -Compress
Set-Content -Path $Config -Value $json -Encoding UTF8
Say "config saved: $Config"

# --- start ------------------------------------------------------------------
if (-not $NoStart) {
  Get-Process -Name python, python3, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.CommandLine -like "*pc-agent.py*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
  $log = Join-Path $AgentDir "agent.log"
  Start-Process -FilePath $py -ArgumentList @($AgentPy, "--url", ($urls -join ","), "--token", $Token) `
      -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError (Join-Path $AgentDir "agent.err")
  Start-Sleep -Seconds 2
  Say "agent started (log: $log)"
}

# --- optional: start at every logon ----------------------------------------
if ($Install) {
  $task = "MinisPcAgent"
  Say "registering scheduled task '$task'"
  try {
    if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
      Unregister-ScheduledTask -TaskName $task -Confirm:$false
    }
    $action  = New-ScheduledTaskAction -Execute $py -Argument "$AgentPy --url $($urls -join ',') --token $Token"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
        -Description "Minis iPhone bridge PC agent" -RunLevel Limited -Force | Out-Null
    Say "scheduled task registered"
  } catch {
    Say "could not register the scheduled task: $($_.Exception.Message) (the agent still runs now)"
  }
}

Write-Host ""
Say "DONE. The Minis app can now run commands on this PC."
Say "  list processes :  Get-Process | Select-Object -First 20 Name,Id"
Say "  system info    :  systeminfo | more"
Write-Host ""
