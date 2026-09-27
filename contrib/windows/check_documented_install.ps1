# Install a built wheel with the exact command from docs/windows.md, then run it unconfigured.
# Used by CI for every change and by the release workflow on the bytes about to be published.
param(
    [Parameter(Mandatory = $true)]
    [string]$Wheel
)

$ErrorActionPreference = "Stop"
# It installs a uv tool for the current user; run it only on a disposable hosted runner.
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or -not $env:RUNNER_TEMP) {
    throw "Run this check only on a GitHub-hosted Windows runner."
}

function Invoke-Checked {
    param([scriptblock]$Command)
    $output = & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
    return $output
}

$expected = [regex]::Match((Split-Path $Wheel -Leaf), '^keyhole_mcp-([^-]+)-').Groups[1].Value
if (-not $expected) {
    throw "Cannot read the package version from $Wheel."
}

# docs/windows.md installs `keyhole-mcp`; only the package source differs here.
Invoke-Checked { uv tool install --python cpython-3.12-windows-x86_64-none --no-build $Wheel }
$env:Path = "$(uv tool dir --bin);$env:Path"

$version = Invoke-Checked { keyhole --version }
if ($version -notmatch [regex]::Escape($expected)) {
    throw "keyhole --version printed '$version', expected $expected."
}

# The hosted runner's default temp path can contain an 8.3 short name, which Keyhole refuses.
$state = Join-Path $env:RUNNER_TEMP ("keyhole-unconfigured-" + [guid]::NewGuid())
$status = Invoke-Checked { keyhole --state-dir $state status --redact } | ConvertFrom-Json
if ($status.configured -or $status.open_workspace_count -ne 0) {
    throw "Unexpected status for an unconfigured state directory: $($status | ConvertTo-Json -Compress)"
}
if (Test-Path $state) {
    throw "status created the state directory."
}
Write-Output "Installed $version with the documented command; unconfigured status is clean."
