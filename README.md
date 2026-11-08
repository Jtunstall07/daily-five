# The Daily Five

A short narrated story every day: the life behind a great book, told in about fifteen minutes, with words to own, stories worth retelling, lessons for the businesses and a three-minute recap.

Live at **https://jtunstall07.github.io/daily-five/**. It is an installable web app: open it in Safari on iPhone, tap Share, then **Add to Home Screen**. It works offline once opened, and any book can be downloaded from its page to read and listen with no connection.

## What is in the repo

| Path | What it is |
| --- | --- |
| `index.html` | The whole app, with a light catalogue of every book inlined |
| `data/books/<id>.json` | One book's full text, chapter timings and sentence timings, loaded when the book is opened |
| `scenes/<id>.json` | The illustrated scenes for each chapter, loaded on demand |
| `audio/<id>.mp4` | Charon narration (AAC) |
| `recaps/<id>.mp4` | Three-minute recaps |
| `data/index.json` | The nightly feed: extra books and voice overrides (see below) |
| `sw.js`, `manifest.webmanifest`, `icons/` | Offline support and install details |
| `tools/` | The generator and its templates (`build_site.py`, `app_template.html`, `sw_template.js`, `icon.svg`) |

Progress, highlights, ratings, streak and Review cards are kept in the browser on each device (localStorage). Nothing is sent anywhere.

## Updating content

**New books without a rebuild.** Add the book as `data/books/<id>.json` (the same shape as the built-in book files, plus an `"art"` SVG string per chapter if it has illustrations), then list its id in `data/index.json`:

```json
{
  "books": ["w10d1"],
  "voices": {},
  "updated": "2026-11-09T05:00:00Z"
}
```

Change `updated` every time; it versions the extra book files so phones pick up edits.

**A new voice for a book.** Put the audio at `voices/<id>.mp4` and add an entry under `voices`:

```json
"voices": { "w1d1": { "audio": "voices/w1d1.mp4", "marks": [0, 120.0], "dur": 928.4, "st": [[7.28, 13.67]], "voice": "Charon" } }
```

`marks` are chapter start times in seconds, `st` the start time of every sentence per chapter, `dur` the length in seconds.

**Requests.** The "Tomorrow's story" box on Home opens a pre-filled GitHub issue labelled `request` in this repo.

**Rebuilding the built-in library.** Run `python3 tools/build_site.py --src <narration build folder>` (add `--icons` to re-render the icons; that needs Python Playwright and Chrome). It rewrites `index.html`, `sw.js`, the book and scene files and copies new audio, and never touches `data/index.json` once it exists. Every data, scene and audio URL carries a content hash, so a rebuild reaches installed copies without anyone clearing their cache.
