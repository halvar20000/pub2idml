@echo off
rem pub2idml: add "Send to > pub2idml" and a desktop shortcut for the current user.
rem No administrator rights needed. Undo with Senden-an-entfernen.cmd.
setlocal
set "APP=%~dp0pub2idml-app.exe"
if not exist "%APP%" (
  echo pub2idml-app.exe nicht gefunden / not found: "%APP%"
  if /i not "%~1"=="/quiet" pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "foreach ($d in @([Environment]::GetFolderPath('SendTo'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  $l = $s.CreateShortcut((Join-Path $d 'pub2idml.lnk'));" ^
  "  $l.TargetPath = $env:APP; $l.WorkingDirectory = (Split-Path $env:APP);" ^
  "  $l.IconLocation = $env:APP + ',0'; $l.Description = 'Publisher (.pub) -> IDML'; $l.Save() }"
if errorlevel 1 (
  echo Einrichten fehlgeschlagen / setup failed.
  if /i not "%~1"=="/quiet" pause
  exit /b 1
)
echo.
echo Fertig. Rechtsklick auf .pub-Dateien ^> "Senden an" ^> pub2idml
echo         oder .pub-Dateien auf das pub2idml-Symbol auf dem Desktop ziehen.
echo Done.   Right-click .pub files ^> "Send to" ^> pub2idml,
echo         or drop .pub files on the pub2idml icon on your desktop.
echo.
echo Hinweis: diesen Ordner nicht verschieben - sonst erneut ausfuehren.
echo Note: do not move this folder - otherwise run this again.
if /i not "%~1"=="/quiet" pause
