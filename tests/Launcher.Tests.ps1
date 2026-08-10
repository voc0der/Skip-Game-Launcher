<#
Pester tests for scripts/Launcher.psm1 and the build.ps1 orchestration.

These cover everything except the IExpress call itself; that is exercised
end-to-end by .github/workflows/verify.yml, which rebuilds the whole set and
reads the launch commands back out of the resulting executables.

    Invoke-Pester tests/Launcher.Tests.ps1
#>

BeforeAll {
    $script:Root = Split-Path $PSScriptRoot -Parent
    Import-Module (Join-Path $script:Root 'scripts/Launcher.psm1') -Force
}

Describe 'Get-StoreDirectory' {
    It 'maps <Store> to <Expected>' -ForEach @(
        @{ Store = 'steam';     Expected = 'Steam' }
        @{ Store = 'battlenet'; Expected = 'BattleNet' }
        @{ Store = 'epic';      Expected = 'Epic' }
        @{ Store = 'ubisoft';   Expected = 'Ubisoft' }
    ) {
        Get-StoreDirectory -Store $Store | Should -Be $Expected
    }

    It 'throws on an unknown store rather than inventing a folder' {
        { Get-StoreDirectory -Store 'gog' } | Should -Throw '*unknown store*'
    }
}

Describe 'Get-LaunchCommand' {
    # These strings were read out of the original hand-built executables. The
    # quoting is load-bearing, so they are asserted literally.
    It 'builds the Steam form' {
        Get-LaunchCommand -Store steam -Id '620' |
            Should -BeExactly 'explorer steam://rungameid/620'
    }

    It 'builds the Ubisoft form' {
        Get-LaunchCommand -Store ubisoft -Id '3539' |
            Should -BeExactly 'explorer uplay://launch/3539/0'
    }

    It 'builds the Epic form' {
        Get-LaunchCommand -Store epic -Id 'Petunia' |
            Should -BeExactly 'explorer "com.epicgames.launcher://apps/Petunia?action=launch&silent=true"'
    }

    It 'builds the Battle.net form with its doubled quotes intact' {
        Get-LaunchCommand -Store battlenet -Id 'Pro' |
            Should -BeExactly 'cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch Pro""'
    }

    It 'throws on an unknown store' {
        { Get-LaunchCommand -Store 'gog' -Id '1' } | Should -Throw '*unknown store*'
    }
}

Describe 'New-SedContent' {
    BeforeAll {
        $script:Sed = New-SedContent -FriendlyName 'Portal 2' `
                                     -Launch 'explorer steam://rungameid/620' `
                                     -TargetPath 'C:\out\Steam\Portal2.exe' `
                                     -WorkDir 'C:\work'
    }

    It 'runs the real launch command as the installed app' {
        $script:Sed | Should -Match ([regex]::Escape('AppLaunched=explorer steam://rungameid/620'))
    }

    It 'does not run a second post-install command' {
        $script:Sed | Should -Match 'PostInstallCmd=<None>'
    }

    It 'starts the launch command in a hidden window' {
        $script:Sed | Should -Match 'ShowInstallProgramWindow=0'
    }

    It 'hides the extraction animation' {
        $script:Sed | Should -Match 'HideExtractAnimation=1'
    }

    It 'does not request a reboot' {
        $script:Sed | Should -Match 'RebootMode=N'
    }

    It 'uses the absolute target path' {
        $script:Sed | Should -Match ([regex]::Escape('TargetName=C:\out\Steam\Portal2.exe'))
    }

    It 'carries the friendly name' {
        $script:Sed | Should -Match 'FriendlyName=Portal 2'
    }

    It 'points SourceFiles at the work directory' {
        $script:Sed | Should -Match ([regex]::Escape('SourceFiles0=C:\work\'))
    }

    It 'enables long filenames for generated launcher names' {
        $script:Sed | Should -Match 'UseLongFileName=1'
    }

    It 'uses CRLF throughout - IExpress is an ANSI-era tool' {
        $script:Sed | Should -Match "`r`n"
        # No bare LF that isn't part of a CRLF pair.
        [regex]::Matches($script:Sed, "(?<!`r)`n").Count | Should -Be 0
    }

    It 'leaves the quiet-install commands empty' {
        $script:Sed | Should -Match 'AdminQuietInstCmd=\r?\n'
        $script:Sed | Should -Match 'UserQuietInstCmd=\r?\n'
    }

    It 'does not mangle a Battle.net command' {
        $bnet = 'cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch Pro""'
        $sed = New-SedContent -FriendlyName 'Overwatch' -Launch $bnet `
                              -TargetPath 'C:\out\BattleNet\Overwatch.exe' -WorkDir 'C:\work'
        $sed | Should -Match ([regex]::Escape("AppLaunched=$bnet"))
        $sed | Should -Match 'PostInstallCmd=<None>'
    }
}

Describe 'Get-BuildTarget' {
    BeforeAll {
        function New-Manifest([string]$Json) { $Json | ConvertFrom-Json }
    }

    It 'produces one target per store' {
        $games = New-Manifest '[{"name":"Metro","out":"Metro.exe","stores":{"epic":"Petunia","steam":"286690"}}]'
        $targets = Get-BuildTarget -Games $games
        $targets.Count | Should -Be 2
        ($targets | ForEach-Object Store | Sort-Object) | Should -Be @('epic', 'steam')
    }

    It 'routes each target into its store folder' {
        $games = New-Manifest '[{"name":"Metro","out":"Metro.exe","stores":{"epic":"Petunia","steam":"286690"}}]'
        $paths = Get-BuildTarget -Games $games | ForEach-Object Path | Sort-Object
        $paths[0] | Should -Be (Join-Path 'Epic' 'Metro.exe')
        $paths[1] | Should -Be (Join-Path 'Steam' 'Metro.exe')
    }

    It 'keeps the id as a string so numeric ids do not become integers' {
        $games = New-Manifest '[{"name":"P","out":"P.exe","stores":{"steam":"620"}}]'
        (Get-BuildTarget -Games $games)[0].Id | Should -BeOfType [string]
    }

    It 'rejects a manifest entry missing <Field>' -ForEach @(
        @{ Field = 'name';   Json = '[{"out":"P.exe","stores":{"steam":"1"}}]' }
        @{ Field = 'out';    Json = '[{"name":"P","stores":{"steam":"1"}}]' }
        @{ Field = 'stores'; Json = '[{"name":"P","out":"P.exe"}]' }
    ) {
        { Get-BuildTarget -Games (New-Manifest $Json) } | Should -Throw "*missing '$Field'*"
    }

    It 'rejects an empty id' {
        { Get-BuildTarget -Games (New-Manifest '[{"name":"P","out":"P.exe","stores":{"steam":""}}]') } |
            Should -Throw '*empty id*'
    }

    It 'rejects a game with no stores at all' {
        { Get-BuildTarget -Games (New-Manifest '[{"name":"P","out":"P.exe","stores":{}}]') } |
            Should -Throw '*no stores listed*'
    }

    It 'rejects an unknown store' {
        { Get-BuildTarget -Games (New-Manifest '[{"name":"P","out":"P.exe","stores":{"gog":"1"}}]') } |
            Should -Throw '*unknown store*'
    }

    It 'rejects two games competing for one output path' {
        $json = '[{"name":"A","out":"Same.exe","stores":{"steam":"1"}},
                  {"name":"B","out":"Same.exe","stores":{"steam":"2"}}]'
        { Get-BuildTarget -Games (New-Manifest $json) } | Should -Throw '*same output path*'
    }

    It 'allows one filename across two different stores' {
        $json = '[{"name":"A","out":"Same.exe","stores":{"steam":"1","epic":"Zed"}}]'
        (Get-BuildTarget -Games (New-Manifest $json)).Count | Should -Be 2
    }
}

Describe 'build.ps1 (dry run)' {
    BeforeAll {
        $script:Work = Join-Path ([IO.Path]::GetTempPath()) ("sgltest_" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $script:Work | Out-Null
        $script:SedOut = Join-Path $script:Work 'sed'

        @'
[
  {"name":"Portal 2","out":"Portal2.exe","stores":{"steam":"620"}},
  {"name":"Metro","out":"Metro.exe","stores":{"epic":"Petunia","steam":"286690"}}
]
'@ | Set-Content -Path (Join-Path $script:Work 'games.json') -Encoding utf8

        & (Join-Path $script:Root 'scripts/build.ps1') `
            -Manifest (Join-Path $script:Work 'games.json') `
            -OutDir $script:Work -SedDir $script:SedOut -DryRun -Force | Out-Null
    }

    AfterAll {
        Remove-Item $script:Work -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'writes one .sed per launcher' {
        (Get-ChildItem $script:SedOut -Filter *.sed).Count | Should -Be 3
    }

    It 'gives each store its own directive file' {
        Test-Path (Join-Path $script:SedOut 'steam-Portal2.exe.sed') | Should -BeTrue
        Test-Path (Join-Path $script:SedOut 'epic-Metro.exe.sed')    | Should -BeTrue
        Test-Path (Join-Path $script:SedOut 'steam-Metro.exe.sed')   | Should -BeTrue
    }

    It 'targets the right store folder for <File>' -ForEach @(
        @{ File = 'steam-Portal2.exe.sed'; Folder = 'Steam'; Exe = 'Portal2.exe' }
        @{ File = 'epic-Metro.exe.sed';    Folder = 'Epic';  Exe = 'Metro.exe' }
        @{ File = 'steam-Metro.exe.sed';   Folder = 'Steam'; Exe = 'Metro.exe' }
    ) {
        $content = Get-Content (Join-Path $script:SedOut $File) -Raw
        $content | Should -Match ([regex]::Escape((Join-Path $Folder $Exe)))
    }

    It 'writes the .sed as ASCII with no BOM' {
        $path = Join-Path $script:SedOut 'steam-Portal2.exe.sed'
        $bytes = [IO.File]::ReadAllBytes($path)
        # A UTF-8 BOM (EF BB BF) would land in the [Version] header and IExpress
        # would refuse the file.
        $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
        $hasBom | Should -BeFalse
        ($bytes | Where-Object { $_ -gt 127 }).Count | Should -Be 0
    }

    It 'does not produce executables in a dry run' {
        (Get-ChildItem $script:Work -Recurse -Filter *.exe).Count | Should -Be 0
    }

    It 'does not delete an existing executable in a forced dry run' {
        $full = Join-Path $script:Work 'Steam/Portal2.exe'
        New-Item -ItemType Directory -Path (Split-Path $full) -Force | Out-Null
        Set-Content -Path $full -Value 'existing launcher'

        & (Join-Path $script:Root 'scripts/build.ps1') `
            -Manifest (Join-Path $script:Work 'games.json') `
            -OutDir $script:Work -DryRun -Force | Out-Null

        (Get-Content $full -Raw).Trim() | Should -BeExactly 'existing launcher'
    }

    It 'throws when -Only matches nothing rather than silently doing nothing' {
        {
            & (Join-Path $script:Root 'scripts/build.ps1') `
                -Manifest (Join-Path $script:Work 'games.json') `
                -OutDir $script:Work -DryRun -Force -Only 'NoSuchGame.exe'
        } | Should -Throw '*matches -Only*'
    }

    It 'builds nothing when the launchers already exist' {
        # Stand in for previously built output.
        foreach ($rel in 'Steam/Portal2.exe', 'Epic/Metro.exe', 'Steam/Metro.exe') {
            $full = Join-Path $script:Work $rel
            New-Item -ItemType Directory -Path (Split-Path $full) -Force | Out-Null
            Set-Content -Path $full -Value 'stub'
        }
        # Write-Host goes to the information stream (6), not stdout.
        $out = & (Join-Path $script:Root 'scripts/build.ps1') `
                    -Manifest (Join-Path $script:Work 'games.json') `
                    -OutDir $script:Work -DryRun 6>&1 | Out-String
        $out | Should -Match 'Nothing to build'
    }
}
