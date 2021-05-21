<b>What this does?</b> Launches the respective Battle.net or Steam game its named after directly, without needing to click "Play".



*Note: Your games can be elsewhere, they will be launched from the launcher.*

<b>Side note:</b> If you use <a href="https://forum.xda-developers.com/windows-10/development/win10tile-native-custom-windows-10-t3248677">Win10Tile</a>, you can make game tile shortcuts with these files easily.

**If using a steam game, Steam can be installed anywhere.

## Source Information

These executables were created using the generic IExpress Windows tool using Windows 10 x64 with hidden mode enabled; the executables emulate package installers with a dummy bat file:
```
echo 1
```

Then runs the command to execute the game, thanks to <a href="https://github.com/dafzor/bnetlauncher/issues/22#issuecomment-399788430">Ethan-BB's post</a> , implementation like so:

For Battle.Net: 
**Requirement: Battle.net Launcher Install path must be the default location (below):**<br />
```C:\Program Files (x86)\Battle.net```
```
// launch overwatch
cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch Pro""
```

For Steam, it's suffice to do something like
```launch x
explorer steam://rungameid/2760
```

For EpicGames, here's how you can launch a desktop shortcut from cmd (quotes are neccessary)
```
explorer "com.epicgames.launcher://apps/Albacore?action=launch&silent=true"
```

The executable does not need to be run as administrator, however, windows may flag it as a potential threat due to unknown source. To prevent windows blocking it, simply right click properties, and there's an unblock button at the bottom right of that dialog prompt.
