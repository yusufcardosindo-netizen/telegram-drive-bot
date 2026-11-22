@echo off
echo Stopping Telegram Drive Bot...
taskkill /F /FI "WINDOWTITLE eq *bot.py*" /T 2>nul
wmic process where "commandline like '%%python bot.py%%'" call terminate 2>nul
echo Done!
pause
