# Ordered attachments

`input.attachments` is the ordered Hub attachment contract. Each item contains
`name`, `mime_type`, `data`, `isText`, and optional `id` and `size`. Media `data`
is a base64 data URL; text attachment `data` is the decoded text. The array order
is the selection order, including files still being read in the browser. Sending
is blocked until those files finish loading. Failed reads are visibly reported.

The worker emits one user message with one content part per attachment, in that
same order, followed by the user's prompt. Each text file is a separate `text`
part prefixed with its filename. There is no `chat` content type and no artificial
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
  The Router rejects raw video for this provider; automatic frame extraction or
  transcoding is not performed. Audio must not be rerouted to Responses.
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
