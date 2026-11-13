@echo off
echo ============================================================
echo  VoxCPM2 (GGUF) engine RELEASE Build: llama.cpp-omni's llama-tts-server
echo  "build_release.bat cuda", "vulkan" or "cpu" builds one set; no argument builds all three.
echo  Needs Visual Studio 2022, git, CUDA Toolkit 12.x (cuda) and the LunarG Vulkan SDK (vulkan).
echo ============================================================

rem The source: our fork of tc-mb/llama.cpp-omni (OpenBMB's own on-device path for VoxCPM2), branch orion:
rem upstream d30f00f plus our commits: it builds with MSVC (ssize_t, struct iovec), and the TTS server runs one
rem generation at a time and stops a whole-waveform request whose client hung up; it keeps the encoded reference audio
rem (the same voice with every request is encoded once).
set REPO=https://github.com/krstalacam/llama.cpp-omni
set COMMIT=43cf1f968dd851398caf5e5c51403f927bd0cdac

set WHAT=%~1
if "%WHAT%"=="" set WHAT=all

call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
    call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
rem vcvars64 may leave another folder current; the steps below work from this one.
cd /d "%~dp0"
set HERE=%CD%

rem Short folders: llama.cpp's tree and ggml-vulkan's shader generator fail past Windows' 260-character paths.
set SRC=%TEMP%\ovx\src
if not exist "%SRC%\.git" (
    git -c core.longpaths=true clone %REPO% "%SRC%" || exit /b 1
)
cd /d "%SRC%"
git -c core.longpaths=true fetch -q origin %COMMIT% 2>nul
git remote set-url origin %REPO%
git -c core.longpaths=true fetch -q origin %COMMIT% 2>nul
git checkout -q -f %COMMIT% || exit /b 1
cd /d "%HERE%"

rem GGML_NATIVE=OFF: the sets run on other processors too (AVX2 baseline). Only llama-tts-server is built.
set COMMON=-DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
rem CUDA 12.1 refuses this Visual Studio unless told; CMake reads CUDAFLAGS while it checks the compiler.
set CUDAFLAGS=-allow-unsupported-compiler -D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH

if /i "%WHAT%"=="all" (
    call :build cpu "" || exit /b 1
    call :build vulkan "-DGGML_VULKAN=ON" || exit /b 1
    call :build cuda "-DGGML_CUDA=ON -DCMAKE_CXX_FLAGS=-D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH" || exit /b 1
) else if /i "%WHAT%"=="cuda" (
    call :build cuda "-DGGML_CUDA=ON -DCMAKE_CXX_FLAGS=-D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH" || exit /b 1
) else if /i "%WHAT%"=="vulkan" (
    call :build vulkan "-DGGML_VULKAN=ON" || exit /b 1
) else (
    call :build cpu "" || exit /b 1
)
echo.
echo Done! The sets are in build\cpu, build\vulkan and build\cuda. Package them with: python package_release.py
exit /b 0

rem :build <name> <cmake options> - in %TEMP%\ovx\b-<name>, then the set is copied to build\<name>.
rem CUDA: compute capability 7.5 and up (the same as OmniVoice's, local_parts.json "min_compute").
:build
set WORK=%TEMP%\ovx\b-%~1
if not exist "%WORK%" mkdir "%WORK%"
cd /d "%WORK%"
if exist CMakeCache.txt del /q CMakeCache.txt
if exist CMakeFiles rd /s /q CMakeFiles
echo [%~1] Configuring...
cmake "%SRC%" -G Ninja %COMMON% %~2 "-DCMAKE_CUDA_ARCHITECTURES=75-virtual;80-virtual;86-real;89-real" || goto :failed
echo [%~1] Building...
cmake --build . -j %NUMBER_OF_PROCESSORS% --target llama-tts-server || goto :failed
cd /d "%HERE%"
if exist "build\%~1" rd /s /q "build\%~1"
mkdir "build\%~1"
copy /y "%WORK%\bin\llama-tts-server.exe" "build\%~1\" >nul || goto :failed
copy /y "%WORK%\bin\*.dll" "build\%~1\" >nul || goto :failed
exit /b 0

:failed
echo [ERROR] %~1 build failed!
cd /d "%HERE%"
exit /b 1
