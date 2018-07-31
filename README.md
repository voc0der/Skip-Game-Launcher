# bnetgamelaunchers
Literally zero fluff, makes use of the new battle.net game launcher commands to launch games. Default install path only.

for compatibility path must be: C:\Program Files (x86)\Battle.net

what it does: it just extracts a 0kb file that has 1 line:
```
echo why you do this windowz
```

then runs the command to execute the game, referenced in https://github.com/dafzor/bnetlauncher/issues/22#issuecomment-399788430 

The executable does not need to be run as administrator, however, windows will flag it as a 'potential threat' which is funny because I used a windows tool, called iexpress to have the command prompt launch the game. To prevent windows blocking it, simply right click properties, and there's an unblock button at the bottom right of that dialog prompt.
