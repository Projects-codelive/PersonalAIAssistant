@echo off
setlocal
echo ====================================================
echo   Building AgentTool Android APK
echo ====================================================

cd /d "%~dp0android"

call gradlew.bat :app:assembleDebug

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Signing and verifying APK...
    set APKSIGNER=C:\Users\Shyam\AppData\Local\Android\Sdk\build-tools\36.0.0\apksigner.bat
    set KEYSTORE=C:\Users\Shyam\.android\debug.keystore

    if exist "%APKSIGNER%" (
        call "%APKSIGNER%" sign --ks "%KEYSTORE%" --ks-pass pass:android --key-pass pass:android --ks-key-alias androiddebugkey --v1-signing-enabled true --v2-signing-enabled true --v3-signing-enabled true --out "..\AgentTool.apk" "app\build\outputs\apk\debug\app-debug.apk"
        copy /y "..\AgentTool.apk" "..\client\AgentTool.apk"
    ) else (
        copy /y "app\build\outputs\apk\debug\app-debug.apk" "..\AgentTool.apk"
        copy /y "app\build\outputs\apk\debug\app-debug.apk" "..\client\AgentTool.apk"
    )

    echo.
    echo ====================================================
    echo   BUILD SUCCESSFUL!
    echo   APK created at: %~dp0AgentTool.apk
    echo   Downloadable at: http://^<YOUR_PC_IP^>:8000/AgentTool.apk
    echo ====================================================
) else (
    echo.
    echo [ERROR] Build failed. Please check the logs above.
)

endlocal
pause
