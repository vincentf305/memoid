# Memoid CLI Dispatcher

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"

$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) {
    throw "Unable to determine the Memoid script path."
}

$ScriptItem = Get-Item $ScriptPath
while ($ScriptItem.LinkType) {
    $TargetPath = $ScriptItem.Target
    if (-not [System.IO.Path]::IsPathRooted($TargetPath)) {
        $TargetPath = Join-Path $ScriptItem.DirectoryName $TargetPath
    }
    $ScriptItem = Get-Item $TargetPath
}

$ScriptDir = $ScriptItem.Directory.FullName
$RootDir = Split-Path $ScriptDir -Parent

function Show-Help {
    Write-Host "Usage: memoid <agent> [args...]" -ForegroundColor Cyan
    Write-Host "       memoid mcp" -ForegroundColor Cyan
    Write-Host "       memoid version" -ForegroundColor Cyan
    Write-Host "       memoid init" -ForegroundColor Cyan
    Write-Host "       memoid update" -ForegroundColor Cyan
    Write-Host "Example: memoid agy" -ForegroundColor Gray
    exit 1
}

if (-not $CliArgs -or $CliArgs.Count -lt 1) {
    Show-Help
}

$Command = $CliArgs[0]
$RemainingArgs = @()
if ($CliArgs.Count -gt 1) {
    $RemainingArgs = $CliArgs[1..($CliArgs.Count - 1)]
}

if ($Command -eq "version") {
    Push-Location $RootDir
    try {
        $Version = git describe --tags --always 2>$null
        if (-not $Version) {
            $Version = "untagged"
        }
        Write-Host "Memoid version: $Version"
    } finally {
        Pop-Location
    }
    exit 0
}

if ($Command -eq "init") {
    Push-Location $RootDir
    try {
        uv sync
        uv run python scripts/post_init_check.py
    } finally {
        Pop-Location
    }
    exit 0
}

if ($Command -eq "update") {
    Push-Location $RootDir
    try {
        Write-Host "Updating Memoid..."
        git fetch --tags --prune

        $LatestTag = git tag --sort=-v:refname | Select-Object -First 1
        if ($LatestTag) {
            Write-Host "Switching to latest tag: $LatestTag"
            git checkout $LatestTag
        } else {
            Write-Host "No tags found, pulling latest from main..."
            git pull --ff-only
        }

        uv sync
        uv run python scripts/post_init_check.py
        Write-Host "Memoid updated successfully."
    } finally {
        Pop-Location
    }
    exit 0
}

if ($Command -eq "mcp") {
    Push-Location $RootDir
    try {
        & uv run --quiet python scripts/mcp_server.py @RemainingArgs
        $ExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($null -ne $ExitCode) {
        exit $ExitCode
    }
    exit 0
}

if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
    Write-Error "Error: Agent command '$Command' not found in PATH"
    exit 1
}

Push-Location $RootDir
try {
    & $Command @RemainingArgs
    $ExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($null -ne $ExitCode) {
    exit $ExitCode
}
