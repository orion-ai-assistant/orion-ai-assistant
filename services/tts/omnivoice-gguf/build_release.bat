@echo off
echo ============================================================
echo  OmniVoice-GGUF RELEASE Build (CPU set in bin\, CUDA set in build\cuda\)
echo  Source: services/tts/omnivoice-gguf (this folder)
echo  "build_release.bat cpu" or "build_release.bat cuda" builds one set only.
echo ============================================================

set WHAT=%~1
if "%WHAT%"=="" set WHAT=all
set PACKAGE_CHOICE=

call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
    call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
rem vcvars64 may leave another folder current; the steps below work from this one.
cd /d "%~dp0"
set SRC=%CD%

rem Short build folders: this folder inside the repository is too deep for Windows' 260-character paths.
rem GGML_NATIVE=OFF on both: the binaries run on other processors too (AVX2 baseline), not only on this one.
rem Each set is whole: ggml.dll is linked to the backends it was built with, so the CPU ggml.dll (bundled with
rem Orion, tracked in bin\) never needs ggml-cuda.dll, and the CUDA zip brings its own ggml.dll.

if /i not "%WHAT%"=="cuda" (
    set EXTRA=-DGGML_CUDA=OFF
    call :build cpu || exit /b 1
    echo Copying the CPU set to bin\ ...
    for %%F in (tts-server.exe omnivoice-codec.exe ggml.dll ggml-base.dll ggml-cpu.dll) do (
        copy /y "%TEMP%\ov-cpu\%%F" "%SRC%\bin\%%F" >nul || (
            echo [ERROR] Missing %%F
            pause
            exit /b 1
        )
    )
)

if /i not "%WHAT%"=="cpu" (
    set EXTRA=-DGGML_CUDA=ON -DCMAKE_CUDA_FLAGS_RELEASE=-O3 "-DCMAKE_CUDA_FLAGS_DEBUG=" "-DCMAKE_CUDA_FLAGS=-allow-unsupported-compiler -D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"
    call :build cuda || exit /b 1
    echo Copying the CUDA set to build\cuda\ ...
    if not exist "%SRC%\build\cuda" mkdir "%SRC%\build\cuda"
    for %%F in (tts-server.exe omnivoice-codec.exe ggml.dll ggml-base.dll ggml-cpu.dll ggml-cuda.dll) do (
        copy /y "%TEMP%\ov-cuda\%%F" "%SRC%\build\cuda\%%F" >nul || (
            echo [ERROR] Missing %%F
            pause
            exit /b 1
        )
    )
)

echo.
echo Done! CPU binaries are in bin\ (commit them), CUDA binaries in build\cuda\.
echo.
if /i not "%WHAT%"=="cpu" (
    set /p PACKAGE_CHOICE="Package the CUDA set for GitHub Release (package_release.py)? (y/N): "
)
if /i "%PACKAGE_CHOICE%"=="y" (
    python package_release.py
)
echo Restart the TTS service to use the new binaries.
pause
exit /b 0

rem :build <name> - a Release build in %TEMP%\ov-<name>, with the cmake options in EXTRA.
:build
set WORK=%TEMP%\ov-%~1
if not exist "%WORK%" mkdir "%WORK%"
cd /d "%WORK%"
echo [%~1] Cleaning old CMake cache...
if exist CMakeCache.txt del /q CMakeCache.txt
if exist CMakeFiles rd /s /q CMakeFiles
echo [%~1] Configuring (Release)...
cmake "%SRC%" -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_NATIVE=OFF ^
  %EXTRA% ^
  -DCMAKE_CXX_FLAGS_RELEASE="/MD /O2 /Ob2 /DNDEBUG" ^
  -DCMAKE_C_FLAGS_RELEASE="/MD /O2 /Ob2 /DNDEBUG" ^
  -DCMAKE_CXX_FLAGS="-D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH" ^
  -DCMAKE_C_FLAGS="-D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"
if errorlevel 1 (
    echo [ERROR] CMake configuration failed!
    cd /d "%SRC%"
    pause
    exit /b 1
)
echo [%~1] Building...
cmake --build . -j %NUMBER_OF_PROCESSORS%
if errorlevel 1 (
    echo [ERROR] Build failed!
    cd /d "%SRC%"
    pause
    exit /b 1
)
cd /d "%SRC%"
exit /b 0
