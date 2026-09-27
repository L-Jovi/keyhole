# Used only by the protected, explicitly approved workflow on a disposable hosted runner.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Use only in the protected Windows acceptance workflow.'
}
if (-not $env:KEYHOLE_ACCEPTANCE_KEY -or -not $env:KEYHOLE_ACCEPTANCE_TUNNEL) {
    throw 'The dedicated Windows acceptance credentials are missing.'
}
$repository = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$wheel = @(Get-ChildItem (Join-Path $repository 'dist/keyhole_mcp-*.whl'))
if ($wheel.Count -ne 1) { throw 'Build exactly one reviewed candidate wheel.' }
$work = Join-Path $env:RUNNER_TEMP 'keyhole-windows-acceptance'
New-Item -ItemType Directory -Path $work | Out-Null
$name = 'keyhole-acceptance'
$password = ConvertTo-SecureString ('Kh!' + [guid]::NewGuid().ToString('N')) -AsPlainText -Force
$user = New-LocalUser -Name $name -Password $password -AccountNeverExpires
$credential = [pscredential]::new("$env:COMPUTERNAME\$name", $password)
$process = $null
try {
    Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $user
    & icacls $work /inheritance:r /grant:r "${name}:(OI)(CI)F" '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect the disposable workspace.' }
    Copy-Item $wheel[0].FullName (Join-Path $work $wheel[0].Name)
    Copy-Item (Get-Command uv).Source (Join-Path $work 'uv.exe')
    Copy-Item (Join-Path $PSScriptRoot 'acceptance_bootstrap.py') (Join-Path $work 'bootstrap.py')
    Copy-Item (Join-Path $PSScriptRoot 'check_console.py') (Join-Path $work 'console-test.py')
    Copy-Item (Join-Path $repository 'contrib/ci/chatgpt_acceptance.py') (Join-Path $work 'session.py')
    $python = (Get-Command python).Source

    # Create the user's real Windows profile before constructing the restricted child environment.
    # No secret is passed to this profile-initialization process.
    $key = $env:KEYHOLE_ACCEPTANCE_KEY
    $tunnel = $env:KEYHOLE_ACCEPTANCE_TUNNEL
    Remove-Item Env:KEYHOLE_ACCEPTANCE_KEY, Env:KEYHOLE_ACCEPTANCE_TUNNEL
    $init = Start-Process -FilePath $python -ArgumentList '-c pass' -Credential $credential `
        -WorkingDirectory $work -LoadUserProfile -Wait -PassThru
    if ($init.ExitCode -ne 0) { throw 'Could not initialize the standard-user profile.' }
    $profilePath = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$($user.SID.Value)").ProfileImagePath
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.WorkingDirectory = $work
    $start.UseShellExecute = $false
    $start.UserName = $name
    $start.Domain = $env:COMPUTERNAME
    $start.Password = $password
    $start.LoadUserProfile = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($arg in @((Join-Path $work 'bootstrap.py'), '--wheel', (Join-Path $work $wheel[0].Name), '--session', (Join-Path $work 'session.py'), '--output', (Join-Path $work 'report.json'), '--uv', (Join-Path $work 'uv.exe'))) {
        $start.ArgumentList.Add($arg)
    }
    if ($env:KEYHOLE_ACCEPTANCE_INSTALL_ONLY -eq 'true') { $start.ArgumentList.Add('--install-only') }
    $start.Environment.Clear()
    $childEnv = @{
        SYSTEMROOT = $env:SYSTEMROOT; WINDIR = $env:SYSTEMROOT
        COMSPEC = "$env:SYSTEMROOT\System32\cmd.exe"
        PATH = "$work;$env:SYSTEMROOT\System32;$(Split-Path $python)"
        PATHEXT = '.COM;.EXE;.BAT;.CMD'; USERPROFILE = $profilePath
        APPDATA = (Join-Path $profilePath 'AppData/Roaming')
        LOCALAPPDATA = (Join-Path $profilePath 'AppData/Local')
        TEMP = $work; TMP = $work; PYTHONUTF8 = '1'
        UV_CACHE_DIR = (Join-Path $work 'uv-cache')
        GITHUB_ACTIONS = 'true'; RUNNER_ENVIRONMENT = 'github-hosted'; GITHUB_SHA = $env:GITHUB_SHA
        KEYHOLE_ACCEPTANCE_KEY = $key; KEYHOLE_ACCEPTANCE_TUNNEL = $tunnel
    }
    foreach ($entry in $childEnv.GetEnumerator()) { $start.Environment[$entry.Key] = $entry.Value }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    if (-not $process.Start()) { throw 'Could not start the standard-user acceptance session.' }
    $stderr = $process.StandardError.ReadToEndAsync()
    while ($null -ne ($line = $process.StandardOutput.ReadLine())) { Write-Output $line }
    $process.WaitForExit()
    Write-Output $stderr.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) { throw "Windows acceptance failed: $($process.ExitCode)" }
} finally {
    if ($null -ne $process) {
        if (-not $process.HasExited) { $process.Kill($true) }
        $process.Dispose()
    }
    $key = $null
    $tunnel = $null
    Remove-LocalUser -Name $name
}
