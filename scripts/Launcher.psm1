<#
Pure logic behind build.ps1 - launch-command formatting, .sed generation and
manifest flattening. Kept free of filesystem and IExpress calls so it can be
tested without building anything.
#>

Set-StrictMode -Version Latest

$script:StoreDirs = [ordered]@{
    steam        = 'Steam'
    battlenet    = 'BattleNet'
    battlenetuid = 'BattleNet'
    epic         = 'Epic'
    ubisoft      = 'Ubisoft'
}

function Get-StoreDirectory {
    <#
    .SYNOPSIS
        Folder a store's launchers live in. Throws on an unknown store.
    #>
    param([Parameter(Mandatory)][string]$Store)

    if (-not $script:StoreDirs.Contains($Store)) {
        throw "unknown store '$Store' (known: $($script:StoreDirs.Keys -join ', '))"
    }
    return $script:StoreDirs[$Store]
}

function Get-KnownStore {
    return @($script:StoreDirs.Keys)
}

function Get-LaunchCommand {
    <#
    .SYNOPSIS
        The command IExpress runs to hand off to the store's launcher.
    .DESCRIPTION
        These strings are load-bearing and the quoting is fiddly - the
        `battlenetuid` form relies on cmd's "strip the outer pair" behaviour,
        so its doubled quotes are deliberate.

        Nothing here may shell through cmd unless it has to. IExpress hides the
        window of the process it starts, and explorer.exe and Battle.net.exe are
        both GUI-subsystem binaries with no console to begin with - which is why
        ShowInstallProgramWindow=0 appears to work for them. cmd.exe is console
        subsystem and allocates one anyway, so a cmd hop is a visible window on
        every launch.
    #>
    param(
        [Parameter(Mandatory)][string]$Store,
        [Parameter(Mandatory)][string]$Id
    )

    switch ($Store) {
        'steam'     { "explorer steam://rungameid/$Id" }
        'ubisoft'   { "explorer uplay://launch/$Id/0" }
        'epic'      { 'explorer "com.epicgames.launcher://apps/' + $Id + '?action=launch&silent=true"' }
        # Invoked directly rather than through cmd: the argv Battle.net receives
        # is identical either way, so the cmd hop only ever bought a console
        # window. Verified against Diablo II: Resurrected (`OSI`).
        'battlenet' { '"C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch ' + $Id + '"' }
        # `launch` takes a product code and only addresses top-level products.
        # `launch_uid` takes a uid and is the only form that reaches a specific
        # game version. It selects the version rather than starting it - see
        # CONTRIBUTING.md - so these land on the client with Play ready.
        # Left on the cmd form: the direct invocation above has not been tried
        # against `launch_uid`, and these five are the only entries using it.
        'battlenetuid' { 'cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch_uid ' + $Id + '""' }
        default     { throw "unknown store '$Store'" }
    }
}

function New-SedContent {
    <#
    .SYNOPSIS
        The IExpress directive file, as a CRLF string.
    .DESCRIPTION
        IExpress requires a packaged file, so the dummy payload is included but
        never run. AppLaunched starts the game and PostInstallCmd remains empty,
        matching the original launchers. ShowInstallProgramWindow=0 therefore
        applies hidden-window handling to the actual launch command.
    #>
    param(
        [Parameter(Mandatory)][string]$FriendlyName,
        [Parameter(Mandatory)][string]$Launch,
        [Parameter(Mandatory)][string]$TargetPath,
        [Parameter(Mandatory)][string]$WorkDir
    )

    # IExpress treats SourceFiles entries as directory prefixes rather than
    # paths to resolve. Its generated .sed files always include the trailing
    # separator; without it, the filename is appended directly to the directory
    # name and package creation exits with code 1.
    $sourceDir = $WorkDir.TrimEnd([IO.Path]::DirectorySeparatorChar,
                                  [IO.Path]::AltDirectorySeparatorChar) +
                 [IO.Path]::DirectorySeparatorChar

    $sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$TargetPath
FriendlyName=$FriendlyName
AppLaunched=$Launch
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="dummy.bat"
[SourceFiles]
SourceFiles0=$sourceDir
[SourceFiles0]
%FILE0%=
"@

    # IExpress is an ANSI-era tool and wants CRLF throughout.
    return ($sed -replace "`r?`n", "`r`n")
}

function Get-BuildTarget {
    <#
    .SYNOPSIS
        Flattens games x stores into one record per executable, validating as it goes.
    #>
    param([Parameter(Mandatory)][AllowEmptyCollection()]$Games)

    $targets = foreach ($g in $Games) {
        foreach ($field in 'name', 'out', 'stores') {
            if (-not $g.PSObject.Properties[$field]) {
                throw "games.json entry missing '$field': $($g | ConvertTo-Json -Compress)"
            }
        }
        if (-not $g.out) { throw "games.json entry has an empty 'out'" }

        $stores = @($g.stores.PSObject.Properties)
        if (-not $stores.Count) { throw "no stores listed for $($g.out)" }

        foreach ($s in $stores) {
            if (-not $s.Value) { throw "empty id for $($g.out) on $($s.Name)" }
            [pscustomobject]@{
                Name  = $g.name
                Out   = $g.out
                Store = $s.Name
                Id    = [string]$s.Value
                Path  = (Join-Path (Get-StoreDirectory $s.Name) $g.out)
            }
        }
    }

    $targets = @($targets)

    # Two entries on one filename, or one entry naming two stores that share a
    # folder ('battlenet' and 'battlenetuid' both build into BattleNet/): either
    # way the second package would overwrite the first.
    $dupes = $targets | Group-Object Path | Where-Object Count -gt 1
    if ($dupes) { throw "two launchers want the same output path: $($dupes.Name -join ', ')" }

    return $targets
}

Export-ModuleMember -Function Get-StoreDirectory, Get-KnownStore, Get-LaunchCommand,
                              New-SedContent, Get-BuildTarget
