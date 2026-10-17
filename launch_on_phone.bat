@echo off
setlocal
set ADB=C:\Users\Shyam\AppData\Local\Android\Sdk\platform-tools\adb.exe

if not exist "%ADB%" (
    set ADB=s:\React\Automation\AgentTool\backend\.venv\Lib\site-packages\adbutils\binaries\adb.exe
)

echo ====================================================
echo   AgentTool - Install & Launch on Phone
echo ====================================================
echo.
echo Checking for connected Android device...
"%ADB%" devices

echo.
echo Waiting for device (Ensure USB Debugging is ON and Allowed on screen)...
"%ADB%" wait-for-device

echo.
echo [1/3] Installing AgentTool.apk...
"%ADB%" install -r "%~dp0AgentTool.apk"

echo.
echo [2/3] Pre-granting Call and Audio permissions...
"%ADB%" shell pm grant com.agenttool.assistant android.permission.CALL_PHONE
"%ADB%" shell pm grant com.agenttool.assistant android.permission.READ_CONTACTS
"%ADB%" shell pm grant com.agenttool.assistant android.permission.RECORD_AUDIO
"%ADB%" shell pm grant com.agenttool.assistant android.permission.READ_PHONE_STATE
"%ADB%" shell pm grant com.agenttool.assistant android.permission.POST_NOTIFICATIONS
"%ADB%" reverse tcp:8000 tcp:8000

echo.
echo [3/3] Launching AgentTool app on your mobile screen...
"%ADB%" shell am start -n com.agenttool.assistant/.MainActivity

echo.
echo ====================================================
echo   SUCCESS! App is now running on your phone!
echo ====================================================
endlocal
pause
