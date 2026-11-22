@echo off
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
copy /y "D:\telegram-drive-bot\run_bot_silent.vbs" "%STARTUP_FOLDER%\TelegramDriveBot.vbs"
echo.
echo =======================================================
echo   SUCCESS! Telegram Drive Bot added to Windows Startup.
echo   It will now start automatically and silently on boot!
echo =======================================================
echo.
pause
