<#
Pure logic behind build.ps1 - launch-command formatting, .sed generation and
manifest flattening. Kept free of filesystem and IExpress calls so it can be
tested without building anything.
#>

Set-StrictMode -Version Latest

$script:StoreDirs = [ordered]@{
    steam     = 'Steam'
    battlenet = 'BattleNet'
    epic      = 'Epic'
    ubisoft   = 'Ubisoft'
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
        Battle.net form in particular relies on cmd's "strip the outer pair"
        behaviour, so the doubled quotes are deliberate.
    #>
    param(
        [Parameter(Mandatory)][string]$Store,
        [Parameter(Mandatory)][string]$Id
    )

    switch ($Store) {
        'steam'     { "explorer steam://rungameid/$Id" }
        'ubisoft'   { "explorer uplay://launch/$Id/0" }
        'epic'      { 'explorer "com.epicgames.launcher://apps/' + $Id + '?action=launch&silent=true"' }
        'battlenet' { 'cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch ' + $Id + '""' }
        default     { throw "unknown store '$Store'" }
    }
}

function New-SedContent {
    <#
    .SYNOPSIS
        The IExpress directive file, as a CRLF string.
    .DESCRIPTION
        AppLaunched runs the dummy payload so IExpress believes it installed
        something; PostInstallCmd is what actually starts the game.
        ShowInstallProgramWindow=0 keeps the extraction window off screen.
    #>
    param(
        [Parameter(Mandatory)][string]$FriendlyName,
        [Parameter(Mandatory)][string]$Launch,
        [Parameter(Mandatory)][string]$TargetPath,
        [Parameter(Mandatory)][string]$WorkDir
    )

    $sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=0
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
AppLaunched=cmd /c dummy.bat
PostInstallCmd=$Launch
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="dummy.bat"
[SourceFiles]
SourceFiles0=$WorkDir
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

    $dupes = $targets | Group-Object Path | Where-Object Count -gt 1
    if ($dupes) { throw "two games want the same output path: $($dupes.Name -join ', ')" }

    return $targets
}

Export-ModuleMember -Function Get-StoreDirectory, Get-KnownStore, Get-LaunchCommand,
                              New-SedContent, Get-BuildTarget
