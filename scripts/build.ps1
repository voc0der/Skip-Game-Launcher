<#
.SYNOPSIS
    Builds Skip-Game-Launcher executables from games.json using IExpress.

.DESCRIPTION
    Each launcher is an IExpress self-extracting package that ships a dummy .bat
    (so IExpress has something to "install") and runs the real store launch
    command as its post-install step, with the progress window hidden.

    A game listed under several stores gets one executable per store, each in
    that store's folder - Steam/Portal2.exe, Epic/Portal2.exe and so on.

    By default only launchers missing from disk are built, which is what CI does
    on every push. The command formatting and .sed generation live in
    Launcher.psm1 so they can be tested without building anything.

.EXAMPLE
    ./scripts/build.ps1
    ./scripts/build.ps1 -Force
    ./scripts/build.ps1 -Only Portal2.exe
    ./scripts/build.ps1 -Only Portal2.exe -Store steam
    ./scripts/build.ps1 -DryRun -SedDir out      # write the .sed files, skip IExpress
#>
[CmdletBinding()]
param(
    [string]   $Manifest = 'games.json',
    [string]   $OutDir   = '.',
    [switch]   $Force,
    [string[]] $Only  = @(),
    [string[]] $Store = @(),

    # Generate everything but don't invoke IExpress. Lets the .sed generation be
    # exercised anywhere, including on a machine with no IExpress at all.
    [switch]   $DryRun,
    [string]   $SedDir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'Launcher.psm1') -Force

# System32 is the 64-bit IExpress; SysWOW64 would emit 32-bit stubs. Everything
# here is a shell-out to explorer, so bitness is cosmetic - we just stay consistent.
# $env:SystemRoot is only set on Windows; resolving it unconditionally would
# break -DryRun everywhere else, which is where the .sed generation gets tested.
$IExpress = if ($env:SystemRoot) { Join-Path $env:SystemRoot 'System32\iexpress.exe' } else { 'iexpress.exe' }
if (-not $DryRun -and -not (Test-Path $IExpress)) {
    throw "iexpress.exe not found at $IExpress - is this a Windows Desktop Experience image?"
}

$OutDir = (Resolve-Path $OutDir).Path

function Build-Launcher {
    param([Parameter(Mandatory)]$Target)

    $storeDir = Join-Path $OutDir (Get-StoreDirectory $Target.Store)
    if (-not (Test-Path $storeDir)) { New-Item -ItemType Directory -Path $storeDir | Out-Null }

    $targetPath  = Join-Path $storeDir $Target.Out
    $launch  = Get-LaunchCommand -Store $Target.Store -Id $Target.Id
    $workDir = Join-Path ([IO.Path]::GetTempPath()) ("sgl_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $workDir | Out-Null

    try {
        # The dummy payload exists only so IExpress has a file to package.
        [IO.File]::WriteAllText((Join-Path $workDir 'dummy.bat'), "echo 1`r`n", [Text.Encoding]::ASCII)

        $sed = New-SedContent -FriendlyName $Target.Name -Launch $launch `
                              -TargetPath $targetPath -WorkDir $workDir
        $sedPath = Join-Path $workDir 'package.sed'
        [IO.File]::WriteAllText($sedPath, $sed, [Text.Encoding]::ASCII)

        if ($SedDir) {
            if (-not (Test-Path $SedDir)) { New-Item -ItemType Directory -Path $SedDir | Out-Null }
            Copy-Item $sedPath (Join-Path $SedDir ("{0}-{1}.sed" -f $Target.Store, $Target.Out))
        }

        if ($DryRun) {
            Write-Host ("  [dry run] {0,-12} {1,-42} {2}" -f (Get-StoreDirectory $Target.Store), $Target.Out, $launch)
            return
        }

        # IExpress wants TargetName absolute, and silently keeps a stale file if
        # one is already sitting there. Do this only for a real build: -DryRun
        # must leave previously generated launchers untouched, even with -Force.
        if (Test-Path $targetPath) { Remove-Item $targetPath -Force }

        # iexpress is a GUI-subsystem binary, so it returns immediately unless waited on.
        $p = Start-Process -FilePath $IExpress -ArgumentList '/N', '/Q', '/M', "`"$sedPath`"" `
                           -Wait -PassThru -NoNewWindow
        if ($p.ExitCode -ne 0) { throw "iexpress exited $($p.ExitCode) building $($Target.Path)" }
        if (-not (Test-Path $targetPath)) { throw "iexpress reported success but produced no $targetPath" }

        $size = (Get-Item $targetPath).Length
        Write-Host ("  {0,-12} {1,-42} {2:N0} bytes  {3}" -f (Get-StoreDirectory $Target.Store), $Target.Out, $size, $launch)
    }
    finally {
        Remove-Item $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- main -------------------------------------------------------------------

$games   = Get-Content $Manifest -Raw | ConvertFrom-Json
$targets = Get-BuildTarget -Games $games

if ($Only.Count)  { $targets = @($targets | Where-Object { $Only  -contains $_.Out }) }
if ($Store.Count) { $targets = @($targets | Where-Object { $Store -contains $_.Store }) }

# A filter that matches nothing is a typo, not a no-op. Silently building
# nothing here would leave the request workflow committing a manifest entry
# with no executable beside it.
if (($Only.Count -or $Store.Count) -and -not $targets.Count) {
    throw ("no launcher in {0} matches -Only '{1}' -Store '{2}'" -f $Manifest, ($Only -join ','), ($Store -join ','))
}

$todo = if ($Force) { $targets } else {
    $targets | Where-Object { -not (Test-Path (Join-Path $OutDir $_.Path)) }
}
$todo = @($todo)

if (-not $todo.Count) {
    Write-Host "Nothing to build - every launcher in $Manifest already exists."
    if ($env:GITHUB_OUTPUT) { "built=0" | Out-File -Append -Encoding utf8 -FilePath $env:GITHUB_OUTPUT }
    return
}

Write-Host "Building $($todo.Count) launcher(s):"
foreach ($t in $todo) { Build-Launcher -Target $t }

if ($env:GITHUB_OUTPUT) {
    "built=$($todo.Count)"                                     | Out-File -Append -Encoding utf8 -FilePath $env:GITHUB_OUTPUT
    "names=$(($todo | ForEach-Object { $_.Path }) -join ', ')" | Out-File -Append -Encoding utf8 -FilePath $env:GITHUB_OUTPUT
}
