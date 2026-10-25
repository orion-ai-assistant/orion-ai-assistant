@echo off
rem One command: build what changed, accept, sign and publish a release round.
rem The release tools live in .release\ (not in git; recreated here when missing).
cd /d "%~dp0"
if not exist ".release\tools\Scripts\python.exe" (
  echo Setting up the release tools in .release\tools ...
  uv venv .release\tools --python 3.11 || exit /b 1
  uv pip install --python .release\tools\Scripts\python.exe --require-hashes -r scripts\release\requirements.txt || exit /b 1
)
".release\tools\Scripts\python.exe" scripts\release\release.py %*
