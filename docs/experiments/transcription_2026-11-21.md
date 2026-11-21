# Speech to text: DeepInfra Whisper turbo against OpenAI, and the speech pre-filter, 2026-11-21

Run on real videos from the library (X bookmarks), with ffmpeg 7.1.1 and LiteLLM 1.110.2. No bookmark text is reproduced here.

## Recommendation

Use **DeepInfra `openai/whisper-large-v3-turbo`** as the default when a DeepInfra (transcription) key is set, and keep **OpenAI `gpt-transcribe`** as the fallback without one. This is what `kosha.llm.stt_model` now does. On clear English speech the two agree on about 95% of words and DeepInfra costs 22 times less. Two limits to know:

- On the Tamil clips Whisper turbo was poor (wrong language, or English filler). A library with much non-English speech should set the user setting `stt_model` to `openai/gpt-transcribe`.
- Whisper writes filler text ("Thank you.") over music and noise. The speech pre-filter below keeps most such clips away from it.

## Prices

| Model (LiteLLM name) | List price | Billed in this run | Source |
|---|---|---|---|
| `deepinfra/openai/whisper-large-v3-turbo` | $0.00020 a minute ($0.012 an hour) | $0.000283 for an 84.9 s clip, which is $0.0120 an hour (the `inference_status.cost` DeepInfra's own endpoint returns) | https://deepinfra.com/openai/whisper-large-v3-turbo, https://deepinfra.com/pricing (read 21 Nov 2026) |
| `openai/gpt-transcribe` | $0.0045 a minute ($0.27 an hour) | The response reports `usage: {type: duration, seconds}`; 27.7 minutes cost $0.125 at the list price | https://developers.openai.com/api/docs/pricing (read 21 Nov 2026) |

For the whole library (16,725 minutes, about 10% not sent): about $3 with DeepInfra, about $68 with OpenAI.

## The comparison: 20 clips, both models

`uv run python -m kosha.media_text_eval --n 20` picks 20 videos of 30 to 150 seconds that the pre-filter says have at least 10 seconds of speech (27.7 minutes together; the same clips every run), sends each as mono 16 kHz mp3 to both models with no context prompt, and compares the words (lower case, punctuation removed, Chinese by character).

| | DeepInfra Whisper turbo | OpenAI gpt-transcribe |
|---|---|---|
| Cost for the 20 clips | $0.0055 | $0.1249 |
| Cost per audio hour | $0.012 | $0.27 |
| Time per clip (mean) | 2.7 s, 31 times real time | 2.9 s, 29 times real time |
| A 20-minute piece (4.6 MB) | 6.2 s | not run |

Word-level agreement between the two transcripts of a clip:

| Clips | n | Agreement |
|---|---|---|
| Clear English speech | 11 | 91% to 99%, mean 95% |
| English, speech over music or with a long intro | 2 | 8% and 44%: Whisper returned far fewer words (one clip partly as Chinese characters) |
| Chinese | 1 | 96% |
| Tamil | 4 | 0%, 1%, 11%, 45%: Whisper wrote English filler or a much shorter text |
| No real speech (music) | 2 | one empty from both; one where only Whisper wrote text (repeated filler) |
| All 20 | 20 | mean 67%, median 92% |

- Where both understood the speech, the differences were product and company names, which Whisper spelled by sound. This run sent no context; the app sends the post's own text as `prompt`, which fixed the same problem for `gpt-transcribe` earlier (`media_text_2026-11-21.md`). Not measured for Whisper.
- Agreement is not accuracy: there is no reference transcript. On the Tamil and music clips OpenAI's text was the longer and more plausible one.
- Not tried: sending the post's language as `language` to Whisper, which may help the wrong-language cases.

## The LiteLLM call path for DeepInfra

- Model string: `deepinfra/openai/whisper-large-v3-turbo`; key `DEEPINFRA_API_KEY` (or the instance key `transcription`).
- **LiteLLM's own `deepinfra/` transcription route returns `text` (and an empty `usage`) only**, also with `response_format="verbose_json"`. DeepInfra itself returns `language`, `duration` and `segments` for the same request. So `kosha.llm.stt_call` sends DeepInfra models through LiteLLM's OpenAI-compatible route (`openai/openai/whisper-large-v3-turbo` with `api_base=https://api.deepinfra.com/v1/openai`), which keeps those fields. Transcripts from DeepInfra are therefore stored `timestamped`.
- Segments are about 30 seconds each (`start`, `end`, `text`), so a transcript line is a 30-second block, not a sentence.
- `language` is a two-letter code (`en`). `gpt-transcribe` returns `languages` and no segments.
- File size: a 20-minute, 4.6 MB mp3 piece (the largest the app sends) went through in 6.2 s. No limit was reached; DeepInfra's pages state none.
- Neither response carries the cost; the ledger uses minutes times the list price.

## The speech pre-filter

Before any paid call the app decodes the sound track with ffmpeg and runs Silero VAD on it (`kosha.media_text.speech_seconds`). A recording with under 3 seconds of speech in total (`MIN_SPEECH_S`) is not transcribed; its preview frame is described instead, as for silent videos. If ffmpeg or the model fails, the recording is sent as before.

**Choice: Silero VAD, run with `onnxruntime` directly.** The `silero-vad` package cannot be installed without torch (its resolution for Python 3.12 pulls torch 2.15 and, on Linux, the CUDA packages). The same model as an ONNX file (1.3 MB, MIT, `kosha/silero_vad_16k.onnx`) needs only `onnxruntime` and numpy, which have wheels for Python 3.12 on macOS arm64, Linux x86_64 and Linux aarch64. An energy check with ffmpeg `silencedetect` was not used: it cannot tell music from speech, and music is the common case here.

60 random library videos (225 minutes):

| Outcome | Videos | Minutes |
|---|---|---|
| No sound track (existing check) | 16 | |
| Silent track, peak under -50 dB (existing check) | 6 | |
| Under 3 s of speech (new: Silero VAD) | 15 | 10.2 |
| Sent for transcription | 23 | 202.7 |
| **Not sent, all three** | **37 of 60 (62%)** | **22.5 of 225 (10%)** |

- The 15 the filter skips had 0 to 2.5 seconds of detected speech; the 23 it keeps had 6 seconds or more, so the threshold sits in a clear gap.
- The filter ran at 100 to 370 times real time on one core (a 50-minute video in 8 seconds).
- **Check of the skipped ones.** All 15 were transcribed anyway (asked: 10). `gpt-transcribe` returned nothing for 13. One 29-second clip had a single spoken sentence of 4 words; one 16-second clip was a song with lyrics. So about 1 in 15 skipped clips lost real speech, and little of it. Whisper turbo returned text for all 15, filler for 13 of them: without the filter that text would have been paid for and indexed.
- The estimate uses these shares: `EXPECTED_SKIP_MINUTES = 0.10` and `EXPECTED_SKIP_FILES = 0.60`.

## Spent

OpenAI $0.18 (20 clips, the 15 skipped clips, one probe). DeepInfra $0.013 (the same, plus the 20-minute piece and three probes).
