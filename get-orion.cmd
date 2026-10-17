@echo off
rem One-command Orion installer for cmd.exe (forwards arguments such as -StartWithWindows).
rem   curl -L -o get-orion.cmd https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.cmd && get-orion.cmd
if exist "%~dp0get-orion.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get-orion.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; & ([scriptblock]::Create((Invoke-RestMethod 'https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.ps1'))) %*"
)
exit /b %ERRORLEVEL%
