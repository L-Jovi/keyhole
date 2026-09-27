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
    $repository = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
    $package = Join-Path $work 'keyhole'
    New-Item -ItemType Directory -Path $package | Out-Null
    Copy-Item (Join-Path $repository 'src/keyhole/*') $package -Recurse
    $nativeTests = Join-Path $work 'test_windows_files.py'
    Copy-Item (Join-Path $repository 'tests/test_windows_files.py') $nativeTests
    $native = Start-Process -FilePath $python -ArgumentList "`"$nativeTests`" -v" -Credential $credential `
        -WorkingDirectory $work -LoadUserProfile -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'native-stdout.txt') -RedirectStandardError (Join-Path $work 'native-stderr.txt')
    Get-Content (Join-Path $work 'native-stdout.txt')
    Get-Content (Join-Path $work 'native-stderr.txt')
    if ($native.ExitCode -ne 0) { throw "Native filesystem tests failed: $($native.ExitCode)" }
    $integrationTests = Join-Path $work 'test_windows_integration.py'
    Copy-Item (Join-Path $repository 'tests/test_windows_integration.py') $integrationTests
    $integration = Start-Process -FilePath $python -ArgumentList "`"$integrationTests`" -v" -Credential $credential `
        -WorkingDirectory $work -LoadUserProfile -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'integration-stdout.txt') -RedirectStandardError (Join-Path $work 'integration-stderr.txt')
    Get-Content (Join-Path $work 'integration-stdout.txt')
    Get-Content (Join-Path $work 'integration-stderr.txt')
    if ($integration.ExitCode -ne 0) { throw "Native integration tests failed: $($integration.ExitCode)" }
    $installerTest = Join-Path $work 'check_client_install.py'
    Copy-Item (Join-Path $repository 'contrib/ci/check_client_install.py') $installerTest
    $installer = Start-Process -FilePath $python -ArgumentList "`"$installerTest`"" -Credential $credential `
        -WorkingDirectory $work -LoadUserProfile -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'installer-stdout.txt') -RedirectStandardError (Join-Path $work 'installer-stderr.txt')
    Get-Content (Join-Path $work 'installer-stdout.txt')
    Get-Content (Join-Path $work 'installer-stderr.txt')
    if ($installer.ExitCode -ne 0) { throw "Official Windows client installation failed: $($installer.ExitCode)" }
} finally {
    Remove-LocalUser -Name $name
}
