@echo off
echo ============================================================
echo  OmniVoice-GGUF RELEASE Build
echo  Source: services/tts/omnivoice-gguf (this folder)
echo ============================================================

call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
    call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)

if not exist build mkdir build
cd build

echo [1/3] Cleaning old CMake cache...
if exist CMakeCache.txt del /q CMakeCache.txt
if exist CMakeFiles rd /s /q CMakeFiles

echo [2/3] Configuring (Release + CUDA)...
cmake .. -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_CUDA=ON ^
  -DCMAKE_CXX_FLAGS_RELEASE="/MD /O2 /Ob2 /DNDEBUG" ^
  -DCMAKE_C_FLAGS_RELEASE="/MD /O2 /Ob2 /DNDEBUG" ^
  -DCMAKE_CUDA_FLAGS_RELEASE="-O3" ^
  -DCMAKE_CUDA_FLAGS_DEBUG="" ^
  -DCMAKE_CXX_FLAGS="-D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH" ^
  -DCMAKE_C_FLAGS="-D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH" ^
  -DCMAKE_CUDA_FLAGS="-allow-unsupported-compiler -D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"

if errorlevel 1 (
    echo [ERROR] CMake configuration failed!
    cd ..
    pause
    exit /b 1
)

echo [3/3] Building...
cmake --build . -j %NUMBER_OF_PROCESSORS%

if errorlevel 1 (
    echo [ERROR] Build failed!
    cd ..
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Copying Release binaries to bin/ ...
echo ============================================================

cd ..

copy /y build\tts-server.exe bin\tts-server.exe
copy /y build\omnivoice-codec.exe bin\omnivoice-codec.exe
copy /y build\ggml.dll bin\ggml.dll
copy /y build\ggml-cpu.dll bin\ggml-cpu.dll
if exist build\ggml-cuda.dll copy /y build\ggml-cuda.dll bin\ggml-cuda.dll
if exist build\ggml-base.dll copy /y build\ggml-base.dll bin\ggml-base.dll
if exist build\omnivoice.dll copy /y build\omnivoice.dll bin\omnivoice.dll

echo.
echo Done! Release binaries are in: bin\
echo.
set /p PACKAGE_CHOICE="Package binaries for GitHub Release (package_release.py)? (y/N): "
if /i "%PACKAGE_CHOICE%"=="y" (
    python package_release.py
)
echo Restart the TTS service to use the new binaries.
pause

