!include "x64.nsh"

Name "WhisperService"
OutFile "..\build\WhisperService-win64.exe"
InstallDir "$PROGRAMFILES\WhisperService"
RequestExecutionLevel admin

Section "Install"
    SetOutPath "$INSTDIR"
    File /r ".\dist\python\*.*"
    File /r ".\dist\ffmpeg\*.*"
    File /r ".\scripts\*.*"

    CreateDirectory "$INSTDIR\app"
    CreateDirectory "$INSTDIR\app\models"

    SetOutPath "$INSTDIR\app"
    File "..\app\main.py"

    SetOutPath "$INSTDIR\app\static"
    File /r "..\app\static\*.*"

    SetOutPath "$INSTDIR\app\templates"
    File /r "..\app\templates\*.*"

SectionEnd
