# Media text on real media, 2026-11-21

`backend/kosha/media_text.py` run on 9 real videos and 5 real photos from the library (X bookmarks), in process, with ffmpeg 7.1.1 at `/opt/homebrew/bin/ffmpeg`. No bookmark text is reproduced here.

- Speech to text: `gpt-transcribe`, $0.0045 a minute (https://developers.openai.com/api/docs/pricing, read 21 Nov 2026). The older `whisper-1` and `gpt-4o-transcribe` models shut down on 26 Feb 2027.
- Images: `gpt-6-luna` with the image as input, answering `{description, text}`.
- Video route: smallest mp4 variant → ffmpeg to mono 16 kHz mp3 in 20-minute pieces → one transcription call a piece, with the post's own text as context.

## Result

| | n | Outcome | Cost | Time per file |
|---|---|---|---|---|
| Videos with speech (107 s, 99 s, 198 s, 190 s) | 4 | 4 transcripts, 1,457 to 3,852 characters, language detected | $0.0446 for 9.9 minutes | 3.5 to 11 s |
| Videos with no speech (48 to 70 s) | 5 | Not sent for transcription; the preview frame is described instead | $0.0011 | 2 to 5 s |
| Photos | 5 | 5 descriptions, 349 to 1,416 characters with the readable text | $0.0017 ($0.00014 to $0.00056 each) | 2 to 7 s |

After each file the item's content hash changed, its summary gained a `Transcript: …` or `Image: …` line where its own text is short, and the text-search column was rewritten, so keyword search finds it.

## Quality

- **Transcripts** read cleanly, with sentences and punctuation, and follow the speaker closely. In the first run a product name was written as a common word that sounds the same; sending the post's text as the `prompt` fixed it in the second run. The same context goes with every call now.
- **No timestamps**: `gpt-transcribe` returns text and languages only, so `extract_format` is `plain`. The code writes `timestamped` lines (`[mm:ss] text`) if a model returns segments.
- **Image descriptions** name the kind of picture and its subject (chart of what, screenshot of which tool, architecture diagram of which system) and copy the visible text, including code and axis labels. One description of a chat screenshot wrote "[obscured]" for a contact name that was blurred in the picture.
- **Silent videos are common**: 5 of 9. Two had no audio track; the other three had a track with no sound in it (peak under -50 dB). The first run sent two of those and paid for an empty transcript, so there is a level check before sending now. Their preview frame is described, which is useful for screen recordings and weak when the frame is unrelated to the post.

## Found while testing

- The image call returned nothing with a 400-token output limit: the limit includes the model's reasoning tokens. It is 1,500 now, with reasoning effort `low`.
- The estimate for these 14 files was $0.0695 (15.1 minutes and 5 images); the bill was $0.047, because the silent videos were not transcribed. The estimate cannot know a video is silent before downloading it, so it stays an upper bound.

## Size of "all"

The library has 2,735 X videos with a downloadable file, 16,725 minutes together, and 3,599 photos. `POST /api/media-text/estimate {"all": true}` prices that at about $77. LinkedIn videos have only a cover picture in the capture, so they are not counted and cannot be transcribed.

## Not done

- Semantic search does not see extracts: the embedded text (`kosha/search/embeddings.py`) has no media text in it. Keyword search and Jev do.
- Documents (LinkedIn PDF carousels) and audio longer than the download cap (200 MB for the smallest mp4) are not handled.
- One worker thread in the server process. Files queued when the server stops stay queued and continue at the next start request.
