@echo off
rem pub2idml: remove the "Send to" entry and the desktop shortcut.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "foreach ($d in @([Environment]::GetFolderPath('SendTo'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  $p = Join-Path $d 'pub2idml.lnk'; if (Test-Path $p) { Remove-Item $p } }"
echo pub2idml-Verknuepfungen entfernt / shortcuts removed.
if /i not "%~1"=="/quiet" pause
