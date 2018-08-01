# Battle.Net.Game.Launchers

I made this so that my <a href="https://forum.xda-developers.com/windows-10/development/win10tile-native-custom-windows-10-t3248677">Win10Tile</a> icons on my start menu wouldn't get confused about which Battle.Net shortcut it is. This can also be used just to launch the game, though if your goal is to have super light weight, you could create a shortcut with the information below.

**Your Battle.net Launcher Install path must be the default location**: <br />
C:\Program Files (x86)\Battle.net<br />

*Note: Your games can be elsewhere, they will be launched from the launcher.*

These executables were created using the generic IExpress Windows tool using Windows 10 x64 with hidden mode enabled; the executables emulate package installers with a dummy bat file:
```
echo why you do this windowz
```

Then runs the command to execute the game, thanks to <a href="https://github.com/dafzor/bnetlauncher/issues/22#issuecomment-399788430">Ethan-BB's post</a> , implementation like so:
```
// launch overwatch
cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch Pro""
```

The executable does not need to be run as administrator, however, windows may flag it as a potential threat due to unknown source. To prevent windows blocking it, simply right click properties, and there's an unblock button at the bottom right of that dialog prompt.
