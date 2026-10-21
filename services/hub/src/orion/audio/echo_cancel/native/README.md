# localvqe.dll

LocalVQE (https://github.com/localai-org/LocalVQE, Apache-2.0, see `LICENSE-LocalVQE.txt`; bundles
ggml, MIT) at commit `7f61cd05095d42e56a135b303f5bb4d761da22b9`, built by
`services/hub/vendor/localvqe/build_localvqe.cmd`:

- x86-64-v3 baseline (`/arch:AVX2`, FMA, F16C, BMI2), not `GGML_NATIVE`: runs on any AVX2 CPU
  (Intel Haswell 2013+, AMD Zen+), not only on the build machine.
- Single self-contained DLL: static CRT, no OpenMP; imports only KERNEL32 and ADVAPI32.

Upstream publishes no Windows binaries, hence the vendored build. The models are not here: they
are downloaded on first use with pinned hashes (`../assets.py`); DTLN-aec models are
https://github.com/breizhn/DTLN-aec (MIT).
