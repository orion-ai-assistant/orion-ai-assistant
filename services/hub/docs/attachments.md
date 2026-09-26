# Ordered attachments

`input.attachments` is the ordered Hub attachment contract. Each item contains
`name`, `mime_type`, `data`, `isText`, and optional `id` and `size`. Media `data`
is a base64 data URL; text attachment `data` is the decoded text. The array order
is the selection order, including files still being read in the browser. Sending
is blocked until those files finish loading. Failed reads are visibly reported.

The worker emits one user message with attachments in that same order, followed
by the user's prompt. Each media attachment has a short `text` label immediately
before its native media part: `Ek 1 — Görsel: photo.png` (or `Ses` / `Video`).
Each text/code file stays in one `text` part: `Ek 2 — Metin dosyası: test.txt`,
then its unmodified contents between `<file_content>` and `</file_content>` lines.
Numbering follows the full attachment list and restarts in each user turn.
These labels apply to all providers, including remote providers, and history
rebuilt from attachment metadata uses the same format. The delimiters are reading
aids, not a security boundary or a guarantee of model comprehension.
There is no `chat` content type and no artificial
conversation turn per file. Display metadata preserves filenames and previews
through SSE, Redis and persisted history.

Hub-to-Router content contract:

| Attachment | Content part |
| --- | --- |
| Image | `image_url: {url: data URL}` |
| Audio | `input_audio: {data: raw base64, format: wav/mp3/...}` |
| Video | `input_video: {data: raw base64, format: mp4/...}` |
| Text/code | `text: filename header and file contents` |

Each part also has `type` equal to the key shown above. `input_video` is a Router
extension compatible with current llama.cpp, not a universal OpenAI content type.
Provider adapters must preserve order, translate all supported parts and explicitly
reject unsupported parts instead of dropping them or treating them as images.

- Gemini converts typed audio/video to native byte parts with their MIME types.
- Local llama.cpp receives typed parts directly. Audio/video require a compatible
  server build, model/projector and, for video, the server's ffmpeg support.
- OpenAI audio uses Chat Completions with WAV/MP3 and an audio-capable model.
  Video is decoded locally by the Router using FFmpeg/FFprobe: 1 FPS, at most
  32 frames spread across the whole clip, and at most 768 pixels per edge.
  Frames and approximate timestamps replace the video at its original position.
  Video audio is not processed. A vision-capable model is required; frames go to
  the OpenAI API, so only preparation is local. Audio must not be rerouted to Responses.
- Future providers implement this contract in their Router adapter; no provider
  branching belongs in the browser.

Deploy the Router adapter update together with Hub. Older Gemini adapters only
recognize `image_url`, so they would otherwise omit the new audio/video parts.
Legacy `input.images` remains accepted. Legacy UI requests containing
`metadata.attachments` are upgraded using their stored order and display text.

References:
- https://developers.openai.com/api/docs/guides/audio-chat-completions
- https://ai.google.dev/gemini-api/docs/video-understanding
- https://github.com/ggml-org/llama.cpp/tree/master/tools/server#post-v1chatcompletions-openai-compatible-chat-completions-api

Native llama.cpp uses 1 FPS by default in Orion (`VIDEO_FPS` in its service `.env`;
upstream defaults to 4 FPS). Install both `ffmpeg` and `ffprobe` on PATH, or set
`FFMPEG_DIR` in the llama.cpp service `.env` and Router `.env` to their directory.
Use a current video-enabled llama.cpp build and a vision model/projector.
No separate transcription or audio-analysis model is called. Native video decoding
and inference stay local. Unlike the OpenAI frame path, native llama.cpp samples
at the configured FPS without Orion's 32-frame cap; long clips need more context.

For a fresh native Windows checkout, obtain a Windows build linked from
https://ffmpeg.org/download.html and put both `ffmpeg.exe` and `ffprobe.exe` in
`services/llm/llama-cpp/bin/ffmpeg/`. The native launcher discovers that directory
automatically; restart the llama.cpp service after installation. Its own web UI
then uses the same server-side video decoder as API requests.
Native Windows x64 installation of either Hub or llama.cpp calls the shared
`scripts/install_ffmpeg.py` installer. It downloads the version-pinned `9.0.2` essentials ZIP directly from
`GyanD/codexffmpeg` on GitHub and verifies the SHA256 pinned in that script;
no Orion release or checksum asset is needed. The upstream archive hash differs
from the old Orion repack, while both executable hashes are unchanged. Only
FFmpeg, FFprobe, the license and upstream README are extracted from its nested
directory into the shared folder.
Both services share the directory above, and verified installations are reused.
Concurrent installs are serialized. A failed checksum stops installation rather
than changing the trusted hash. Existing llama.cpp installs also run this check.
The installer and manager pass this directory to the native Orion Router when
starting it (an explicit FFMPEG_DIR environment value takes precedence).
Restart an already-running Router to pick up the tools. This does not modify the
separate Router repository. On native Linux/macOS install ffmpeg and ffprobe
through your package manager. The Windows archive cannot be used in containers.
The executables are ignored by Git. Cloning alone does not install them.
For Docker, the tools must be available inside the llama.cpp container.

Release migration: publish the updated installer before removing the old Orion
`ffmpeg-v9.0.2` binary asset. Older checkouts must update before reinstalling.
Existing verified executables continue to work without downloading again.
