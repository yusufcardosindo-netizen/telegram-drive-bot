Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\telegram-drive-bot"
WshShell.Run "python bot.py", 0, False
