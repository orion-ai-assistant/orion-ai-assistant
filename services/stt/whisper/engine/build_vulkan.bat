@echo off
echo ============================================================
echo  whisper.cpp RELEASE Build (Vulkan: AMD, Intel and NVIDIA cards)
echo  Source: github.com/ggml-org/whisper.cpp, tag below. Needs the LunarG Vulkan SDK (VULKAN_SDK set by its
echo  installer), Visual Studio 2022 and git. CUDA and CPU come from whisper.cpp's own release zips.
echo ============================================================

set TAG=v1.9.5

if not defined VULKAN_SDK (
    echo [ERROR] VULKAN_SDK is not set. Install the LunarG Vulkan SDK first.
    exit /b 1
)

call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
    call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
cd /d "%~dp0"
set HERE=%CD%

rem Short folders: ggml-vulkan's shader generator fails past Windows' 260-character paths.
set SRC=%TEMP%\wcpp
set WORK=%TEMP%\wcpp-vk
if not exist "%SRC%" (
    echo [1/3] Fetching whisper.cpp %TAG%...
    git clone --depth 1 --branch %TAG% https://github.com/ggml-org/whisper.cpp "%SRC%"
    if errorlevel 1 exit /b 1
)

rem GGML_NATIVE=OFF: the zip runs on other processors too (AVX2 baseline), not only on this one.
echo [2/3] Configuring (Release + Vulkan)...
if exist "%WORK%\CMakeCache.txt" del /q "%WORK%\CMakeCache.txt"
cmake -S "%SRC%" -B "%WORK%" -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_VULKAN=ON ^
  -DGGML_CUDA=OFF ^
  -DGGML_NATIVE=OFF ^
  -DBUILD_SHARED_LIBS=ON ^
  -DWHISPER_BUILD_TESTS=OFF ^
  -DWHISPER_SDL2=OFF
if errorlevel 1 exit /b 1

echo [3/3] Building whisper-server...
cmake --build "%WORK%" -j %NUMBER_OF_PROCESSORS% --target whisper-server
if errorlevel 1 exit /b 1

echo Done: %WORK%\bin. Next: python "%HERE%\package.py"
