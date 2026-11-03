@echo off
rem One command: build what changed, accept, sign and publish a release round.
rem The release tools live in .release\ (not in git; recreated here when missing or on another Python).
cd /d "%~dp0"
".release\tools\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info[:2] != (3, 14))" 2>nul || (
  echo Setting up the release tools in .release\tools on Python 3.14 ...
  if exist ".release\tools" rmdir /s /q ".release\tools"
  uv venv .release\tools --python 3.14 || exit /b 1
)
rem Quick when nothing changed; brings the tools to scripts\release\requirements.txt when it did.
uv pip install -q --python .release\tools\Scripts\python.exe --require-hashes -r scripts\release\requirements.txt || exit /b 1
".release\tools\Scripts\python.exe" scripts\release\release.py %*
