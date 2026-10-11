# Voice

One sentence: **voice is an input and output mode for the same tutor, not a separate product**,
so a mis-heard word is treated as a speech problem and never as evidence about the learner.

## Transport

`GET /api/v1/voice/capabilities` returns the WebSocket path (currently `/ws/voice`), the sample
rates, the encoding and whether each engine is installed. The client dials what the server says
instead of hard-coding it.

| frame | direction | shape |
|---|---|---|
| `hello` | server | `{sample_rate_in: 16000, sample_rate_out: 24000, encoding: "s16le", session_id, status}` |
| binary | both | mono PCM s16le at the rate above |
| `text` | client | `{t: "text", text}` — the keyboard path, identical tutoring |
| `partial` | server | `{t: "partial", text}` — transcript while you speak, always editable |
| `state` | server | `{t: "state", state}` — `listening · thinking · speaking · idle` |
| `reply` | server | `{t: "reply", text, ms, meta}` |
| `speak` | server | audio in binary frames, or `speak_unavailable` with the reason |
| `config` | client | `{t: "config", voice_on, gender, barge_in}` → `config_ack` |

An unauthenticated socket is closed with **4401**: a voice stream is your session, and an
anonymous microphone has nowhere legitimate to be recorded to.

## Engines and fallbacks

| need | first choice | if unavailable |
|---|---|---|
| hearing you | local `faster-whisper` (in-process, decoded in memory and discarded) | browser dictation (`SpeechRecognition`), which is on-device in Chrome/Edge/Safari |
| speaking | local Kokoro (`af_heart` / `am_fenrir`, 24 kHz) | browser `speechSynthesis` with a male/female pick from the installed voices |
| turn-taking | energy VAD with a 600 ms hangover and 240 ms minimum speech (`vad_backend="webrtc"` when installed) | push-to-talk; the button on the voice screen |

`POST /api/v1/voice/transcribe` accepts an uploaded clip for non-realtime use; in the browser
path it deliberately returns `note: "browser STT supplies text, not audio"` because nothing was
uploaded to it.

Install the local engines with:

```bash
pip install faster-whisper kokoro && apt-get install -y espeak-ng
# weights are fetched from Hugging Face once, then cached in ~/.cache
```

`kokoro` requires Python < 3.13 and is published as `kokoro` (not `pykokoro`). Both engines are
optional: the tests that cover turn-taking, barge-in flags, latency accounting and the
fallbacks run without them.

## What is stored

Turn **transcripts** (only for turns the learner chose to record), plus latency and an
`interrupted` flag. No audio is retained: input is decoded in memory and output is streamed.
`GET /api/v1/privacy` states the same from the server side, and `voice_enabled=false` means the
microphone is never touched — the keyboard path teaches exactly the same thing, so voice is a
preference and never a requirement.

`voice_gender` is stored on the account and can be overridden per session on the voice screen,
so a borrowed laptop does not leave your preference switched off for the next person.

## Measurement

Two separate quantities, in that order:

1. **recognition** — word error rate against the transcript the learner confirmed. A corrected
   transcript is the ground truth, which is why the box is editable.
2. **teaching** — whether the answer was useful (the same rubric the text tutor is judged on).

Reporting them together would let a bad microphone flatter or damn the tutor. `reply.ms` covers
the tutor only; capture-to-first-audio is logged separately so a slow device shows up as what it
is.
