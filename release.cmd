@echo off
rem One command: build what changed, accept, sign and publish a release round.
cd /d "%~dp0"
".tmp\stage05-tools\Scripts\python.exe" scripts\release\release.py %*
