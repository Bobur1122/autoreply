param(
  [string]$Destination = "project.zip",
  [switch]$IncludeSecrets
)

$ErrorActionPreference = "Stop"

$required = @("main.py", "requirements.txt")
foreach ($f in $required) {
  if (-not (Test-Path -LiteralPath $f)) {
    throw "Missing required file: $f (it must be in the project root)."
  }
}

$include = @(
  "main.py",
  "requirements.txt",
  "README.md",
  ".env.example"
) | Where-Object { Test-Path -LiteralPath $_ }

if ($IncludeSecrets) {
  if (Test-Path -LiteralPath ".env") {
    $include += ".env"
    $envContent = Get-Content -Raw -LiteralPath ".env"
    $m = [regex]::Match($envContent, "(?m)^\s*TG_SESSION\s*=\s*(.+?)\s*$")
    if ($m.Success) {
      $sessionName = $m.Groups[1].Value.Trim()
      $sessionFiles = @("$sessionName.session", "$sessionName.session-journal") | Where-Object { Test-Path -LiteralPath $_ }
      $include += $sessionFiles
    }
  }
}

if (Test-Path -LiteralPath $Destination) {
  Remove-Item -LiteralPath $Destination -Force
}

Compress-Archive -Path $include -DestinationPath $Destination -Force
Write-Host "Created $Destination with: $($include -join ', ')"
