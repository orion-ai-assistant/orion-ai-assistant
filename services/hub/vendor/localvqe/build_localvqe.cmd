@echo off
rem Reproducible build of the shipped localvqe.dll (Orion Hub echo cancellation).
rem Needs: git, Visual Studio 2022 with "Desktop development with C++" (MSVC, CMake, Ninja).
rem Usage: build_localvqe.cmd [short work dir, default C:\lvqe]
rem The work dir must be short: ggml's nested object paths exceed the 260 character MAX_PATH limit.
rem x86-64-v3 (AVX2) baseline instead of -march=native, static CRT and no OpenMP: the DLL runs on any
rem AVX2 CPU (Intel Haswell / AMD Zen and later) with nothing beyond what Windows 10/11 ships.
setlocal
set COMMIT=7f61cd05095d42e56a135b303f5bb4d761da22b9
set WORK=%~1
if "%WORK%"=="" set WORK=C:\lvqe
set HERE=%~dp0
set OUT=%HERE%..\..\src\orion\audio\echo_cancel\native
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
if not exist "%WORK%\src\.git" git clone https://github.com/localai-org/LocalVQE.git "%WORK%\src" || exit /b 1
git -C "%WORK%\src" checkout --force %COMMIT% || exit /b 1
git -C "%WORK%\src" submodule update --init --recursive --force || exit /b 1
git -C "%WORK%\src" apply "%HERE%portable-x86-64-v3.patch" || exit /b 1
if exist "%WORK%\b" rmdir /s /q "%WORK%\b"
cmake -S "%WORK%\src\ggml" -B "%WORK%\b" -G Ninja -DCMAKE_BUILD_TYPE=Release -DLOCALVQE_BUILD_SHARED=ON ^
  -DGGML_OPENMP=OFF -DCMAKE_POLICY_DEFAULT_CMP0091=NEW -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded || exit /b 1
cmake --build "%WORK%\b" --target localvqe_shared || exit /b 1
copy /y "%WORK%\b\bin\localvqe.dll" "%OUT%\localvqe.dll" >nul || exit /b 1
copy /y "%WORK%\src\LICENSE" "%OUT%\LICENSE-LocalVQE.txt" >nul || exit /b 1
echo Built %OUT%\localvqe.dll
