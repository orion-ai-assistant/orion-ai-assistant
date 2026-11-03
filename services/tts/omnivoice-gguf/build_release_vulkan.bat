@echo off
echo ============================================================
echo  OmniVoice-GGUF RELEASE Build (Vulkan: AMD, Intel and NVIDIA cards)
echo  Source: services/tts/omnivoice-gguf (this folder)
echo  Needs the LunarG Vulkan SDK (VULKAN_SDK set by its installer).
echo ============================================================

if not defined VULKAN_SDK (
    echo [ERROR] VULKAN_SDK is not set. Install the LunarG Vulkan SDK first.
    pause
    exit /b 1
)

call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
    call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
rem vcvars64 may leave another folder current; the steps below work from this one.
cd /d "%~dp0"
set SRC=%CD%

rem A short build folder: ggml-vulkan's shader generator fails past Windows' 260-character paths, and this
rem folder inside the repository is already too deep. The results are copied to build\vulkan below.
set WORK=%TEMP%\ov-vk
if not exist "%WORK%" mkdir "%WORK%"
cd /d "%WORK%"

echo [1/4] Cleaning old CMake cache...
if exist CMakeCache.txt del /q CMakeCache.txt
if exist CMakeFiles rd /s /q CMakeFiles

rem GGML_NATIVE=OFF: the zip runs on other processors too (AVX2 baseline), not only on this one.
echo [2/4] Configuring (Release + Vulkan)...
cmake "%SRC%" -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_VULKAN=ON ^
  -DGGML_CUDA=OFF ^
  -DGGML_NATIVE=OFF ^
  -DCMAKE_CXX_FLAGS_RELEASE="/MD /O2 /Ob2 /DNDEBUG" ^
  -DCMAKE_C_FLAGS_RELEASE="/MD /O2 /Ob2 /DNDEBUG"

if errorlevel 1 (
    echo [ERROR] CMake configuration failed!
    cd /d "%SRC%"
    pause
    exit /b 1
)

echo [3/4] Building...
cmake --build . -j %NUMBER_OF_PROCESSORS%

if errorlevel 1 (
    echo [ERROR] Build failed!
    cd /d "%SRC%"
    pause
    exit /b 1
)

rem Its own folder: the bundled ggml.dll in bin\ is linked to ggml-cuda.dll, so the Vulkan zip carries a whole set.
echo [4/4] Copying the Vulkan set to build\vulkan\ ...
cd /d "%SRC%"
if not exist build\vulkan mkdir build\vulkan
for %%F in (tts-server.exe omnivoice-codec.exe ggml.dll ggml-base.dll ggml-cpu.dll ggml-vulkan.dll) do (
    copy /y "%WORK%\%%F" build\vulkan\%%F >nul || (
        echo [ERROR] Missing %%F
        pause
        exit /b 1
    )
)

echo.
echo Done! Vulkan binaries are in: build\vulkan\
echo.
set /p PACKAGE_CHOICE="Package them for GitHub Release (package_release.py vulkan)? (y/N): "
if /i "%PACKAGE_CHOICE%"=="y" (
    python package_release.py vulkan
)
pause
