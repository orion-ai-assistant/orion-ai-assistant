# Whisper CT2 model releases

The installer downloads versioned CT2 packages from this repository's GitHub
Releases. Model assets are separate from application releases and do not replace
the application's latest release.

| Installer model | Model release | Source |
| --- | --- | --- |
| `whisper-small-official` | `stt-whisper-ct2-v1` | `Systran/faster-whisper-small`, revision `536b0662742c02347bc0e980a01041f333bce120` |
| `whisper-large-v3-turbo-ct2` | `stt-whisper-ct2-v1` | Existing local CT2 conversion, with download/conversion scripts targeting `openai/whisper-large-v3-turbo` |

The combined release is titled **Whisper CT2 — STT Models** and contains only
`whisper-small-ct2.zip` and `whisper-large-v3-turbo-ct2.zip`. Every ZIP contains
the required model files, license, and provenance at the archive root. Conversion
scripts, standalone license assets, and standalone checksum assets are omitted.

The existing `tts-v1.0.0` release is titled **TTS — Ses Sentezi Paketleri**.
Its OmniVoice Windows/CUDA runtime asset and download URL are preserved.
That asset contains the runtime, while TTS model weights are downloaded separately.

Both packages contain FP16 weights. NVIDIA compute choices are `int8_float16`,
`float16`, and `int8`; CPU uses `int8`. Legacy `float32` settings are normalized
to the supported device default. Audio buffers still use NumPy float32.

The manifest pins each ZIP URL, its own `archive_sha256`, and SHA256 values for
every installed file. The downloader verifies the archive, checks the exact file
list, extracts into a temporary directory, and verifies all files before replacing
installed files. Cancelled or rejected archives are cleaned up. Existing verified
packages do not need downloading again. File-by-file downloads remain supported
for other services.

Only the standard Small and Turbo packages appear in the ready-model catalog.
An installed `whisper-small-finetuned-tr` folder is discovered as a local model
and appears under the user's own models instead.

Both complete local packages were loaded with Faster Whisper on CPU/int8 with
`local_files_only=True`. This validates package loading, not transcription quality.
The Small weights match the SHA256 in the local upstream cache. Turbo's original
source revision and conversion library version were not recorded; its provenance
file explicitly records that limitation and hashes the local HF source weights.

To prepare future model releases, package resolved model files rather than cache
symlinks. Exclude `.locks`, `blobs`, and incomplete downloads. Preserve upstream
attribution and licenses inside the ZIP. Update both archive and file hashes, and
verify anonymous asset access before distribution. Use a new version when replacing
model contents; adding another named model package can share the release.

## Language settings and prompts

The single language catalog lives at
`services/stt/whisper/stt_languages.json`. It contains all 100
Whisper language codes, display names, localized example prompts, and browser
language aliases. Only STT reads this file and selects transcription prompts. Its public
`/v1/audio/language-catalog` endpoint exposes names and aliases without prompts.
Router forwards that metadata at `/v1/audio/languages`; Hub exposes it to web and
Flutter at `/api/v1/audio/languages` over its authenticated, pinned Router connection.
The existing STT `/v1/audio/languages` dashboard list remains compatible.
Hub settings validate code syntax without importing an engine catalog. STT handles
its supported codes and aliases. STT can run independently of Hub/Flutter.

`stt_language` is a shared user setting. Web and Flutter send their device locale
in `X-Orion-STT-Language` when loading settings. If the preference is unset, Hub
uses STT metadata to select a supported language (or `auto`) and atomically saves
it to the user's durable settings. The first successful initialization wins;
other devices use that choice. Initialization never replaces a manual preference,
including `auto`, and unavailable STT/database leaves it unset for a later retry.
A saved language overrides each device's locale. Choosing a language selects its
prompt; automatic detection selects the
prompt after detecting the audio language. Custom request prompts still take priority.

Restart STT after updating the catalog and reload the browser to pick up the
new choices. Prompt translations are editable examples, not fine-tuned weights.
