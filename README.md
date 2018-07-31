# Battle.Net.Game.Launchers

For compatibility your Battle.net install base path must be: C:\Program Files (x86)\Battle.net<br />
The games can be elsewhere.

What it does: extracts a 0kb file that has 1 line for fluff so it can run:
```
echo why you do this windowz
```

Then runs the command to execute the game, referenced in https://github.com/dafzor/bnetlauncher/issues/22#issuecomment-399788430 , implementation like so:
```
// launch overwatch
cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch Pro""
```

The executable does not need to be run as administrator, however, windows may flag it as a potential threat due to unknown source. To prevent windows blocking it, simply right click properties, and there's an unblock button at the bottom right of that dialog prompt.
