# This script is for a disposable GitHub-hosted Windows runner only.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Use only in a disposable GitHub-hosted Windows CI job.'
}
$work = Join-Path $env:RUNNER_TEMP 'keyhole-windows'
New-Item -ItemType Directory -Path $work | Out-Null
$name = 'keyhole-probe'
$password = ConvertTo-SecureString ('Kh!' + [guid]::NewGuid().ToString('N')) -AsPlainText -Force
$user = New-LocalUser -Name $name -Password $password -AccountNeverExpires
$credential = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$name", $password)
try {
    Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $user
    & icacls $work /grant "${name}:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not grant access to the disposable fixture.' }
    $denied = Join-Path $work 'owner-only.txt'
    Set-Content -Path $denied -Value 'Synthetic ACL fixture, not a credential'
    & icacls $denied /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the ACL fixture.' }
    $script = Join-Path $work 'probe.py'
    Copy-Item (Join-Path $PSScriptRoot 'probe.py') $script
    $python = (Get-Command python).Source
    $report = Join-Path $work 'report.json'
    $arguments = "`"$script`" --output `"$report`" --denied-file `"$denied`""
    $process = Start-Process -FilePath $python -ArgumentList $arguments -Credential $credential `
        -WorkingDirectory $work -LoadUserProfile -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'stdout.txt') -RedirectStandardError (Join-Path $work 'stderr.txt')
    Get-Content (Join-Path $work 'stdout.txt')
    Get-Content (Join-Path $work 'stderr.txt')
    if ($process.ExitCode -ne 0) { throw "Feasibility probe failed: $($process.ExitCode)" }
    if (-not (Test-Path $report)) { throw 'The probe did not create its evidence report.' }
} finally {
    Remove-LocalUser -Name $name
}
