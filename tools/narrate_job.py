"""Daily free-tier re-recorder for The Daily Five. Run: python3 narrate_job.py <books.json> <done_ids.txt> <outdir> <gemini_key>
books.json: list of book objects in library order (from the app page). done_ids.txt: one id per line already re-recorded.
Records as many whole books as fit in today's free quota (10 requests a day), alternating voices by library position
(even index Achernar, odd Aoede). For each finished book writes <outdir>/<id>.mp4 and <outdir>/<id>.json
{marks, dur, st, voice}. Prints DONE <id> lines and stops cleanly at the daily limit. Never touches billing."""
import base64, json, os, re, subprocess, sys, time, urllib.request, urllib.error

BOOKS, DONE, OUT, K = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
MODEL = os.environ.get("MODEL", "gemini-3.8-flash-tts")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
TMP = os.path.join(OUT, "tmp"); os.makedirs(TMP, exist_ok=True)
CHUNK = int(os.environ.get("CHUNK", "6500"))  # characters per request
STYLE = ("Read this aloud in a natural, relaxed British English accent, like a thoughtful friend telling a true story over a drink. "
         "Conversational, warm and human, never like a newsreader or a robot. Normal speaking pace. "
         "Let the voice rise and fall naturally, lean a little on the surprising details, pause briefly at the end of every sentence, "
         "a little longer between paragraphs, and about two seconds before each new chapter heading. Read every word exactly as written, adding nothing:\n\n")
ABBR = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Prof|Gen|Col|Capt|Lt|vs|etc|No|Jr|Sr|Ltd|Co|Inc|[A-Z])\.\s*$")
SENT = re.compile(r"[^.!?]+(?:[.!?]+[\"'”’)]*|$)\s*")
class DailyLimit(Exception): pass

def split_sentences(t):  # identical to splitSentences() in the app
    m = [x for x in SENT.findall(t) if x] or [t]
    out, buf = [], ""
    for s in m:
        buf += s
        if not ABBR.search(buf.strip()) or len(buf) > 400: out.append(buf); buf = ""
    if buf: out.append(buf)
    return out

def tts(text, voice, path):
    body = {"contents": [{"parts": [{"text": STYLE + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    wait = 20
    for attempt in range(8):
        try:
            r = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": K})
            d = json.load(urllib.request.urlopen(r, timeout=600))
            raw = base64.b64decode(d["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            open(path, "wb").write(raw); return
        except urllib.error.HTTPError as e:
            msg = e.read()[:600].decode("utf8", "replace")
            if e.code == 429 and ("PerDay" in msg or "per day" in msg.lower()): raise DailyLimit(msg[:200])
            if e.code in (429, 500, 503, 504): time.sleep(wait); wait = min(wait * 2, 240); continue
            raise RuntimeError(f"{e.code} {msg}")
        except (KeyError, IndexError):
            time.sleep(10)
        except urllib.error.URLError as e:
            time.sleep(wait); wait = min(wait * 2, 240)
    raise RuntimeError("gave up")

def run(*a): subprocess.run(list(a), check=True)
def dur(p): return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())
def norm(src, dst):
    run("ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "24000", "-af",
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse", dst)
def gaps(wav):
    r = subprocess.run(["ffmpeg", "-i", wav, "-af", "silencedetect=noise=-38dB:d=0.18", "-f", "null", "-"], capture_output=True, text=True)
    s = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    e = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(s, e))

def align(sents, total, G, hard):
    """Choose len(sents)-1 pauses in order as boundaries; boundaries before headings (hard) prefer long pauses."""
    n = len(sents)
    if n == 1: return [0.0]
    L = [len(s) for s in sents]; tot = sum(L); acc = 0; exp = []
    for k in range(n - 1): acc += L[k]; exp.append(total * acc / tot)
    m = len(G)
    if m < n - 1:
        return [0.0] + [min((g[1] for g in G if abs(g[1] - e) < 1.5), key=lambda x: abs(x - e), default=e) for e in exp]
    INF = 1e18
    def c(k, j):
        a, b = G[j]; w = 6.0 if hard[k + 1] else 2.5
        return abs((a + b) / 2 - exp[k]) - w * min(b - a, 2.0)
    dp = [[INF] * m for _ in range(n - 1)]; bk = [[-1] * m for _ in range(n - 1)]
    for j in range(m): dp[0][j] = c(0, j)
    for k in range(1, n - 1):
        best, bi = INF, -1
        for j in range(m):
            if j >= 1 and dp[k - 1][j - 1] < best: best, bi = dp[k - 1][j - 1], j - 1
            if best < INF: dp[k][j] = best + c(k, j); bk[k][j] = bi
    j = min(range(m), key=lambda x: dp[n - 2][x]); picks = [j]
    for k in range(n - 2, 0, -1): j = bk[k][j]; picks.append(j)
    picks.reverse()
    return [0.0] + [G[p][1] - 0.05 for p in picks]

def plan(b):
    """Group chapters into requests of up to CHUNK characters. Each item: list of chapter indices."""
    groups, cur, size = [], [], 0
    for ci, c in enumerate(b["ch"]):
        n = sum(len(p) for p in c["b"]) + len(c["h"]) + 40
        if cur and size + n > CHUNK: groups.append(cur); cur, size = [], 0
        cur.append(ci); size += n
    if cur: groups.append(cur)
    return groups


# ---- word-level alignment (PocketSphinx, free and offline) ----
import difflib
try:
    import pocketsphinx  # noqa
except ImportError:
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--break-system-packages', 'pocketsphinx'], check=False)

ABBR = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Prof|Gen|Col|Capt|Lt|vs|etc|No|Jr|Sr|Ltd|Co|Inc|[A-Z])\.\s*$")
SENT = re.compile(r"[^.!?]+(?:[.!?]+[\"'”’)]*|$)\s*")
def split_sentences(t):  # identical to splitSentences() in the app
    m = [x for x in SENT.findall(t) if x] or [t]
    out, buf = [], ""
    for s in m:
        buf += s
        if not ABBR.search(buf.strip()) or len(buf) > 400: out.append(buf); buf = ""
    if buf: out.append(buf)
    return out

def words(t): return re.findall(r"[a-z0-9]+", t.lower().replace("’", "'").replace("'", ""))

def recognise(path):
    from pocketsphinx import Decoder
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    d = Decoder(samprate=16000, loglevel="FATAL")
    out, step = [], 16000 * 2 * 30  # 30 s blocks, cut at the quietest point near each boundary
    pos = 0
    while pos < len(raw):
        end = min(len(raw), pos + step)
        if end < len(raw):  # move the cut to a quiet 20 ms frame within the last 3 s
            best, bi = None, end
            for f in range(end - 16000 * 2 * 3, end, 640):
                seg = raw[f:f + 640]
                e = sum(abs(int.from_bytes(seg[i:i + 2], "little", signed=True)) for i in range(0, len(seg), 8))
                if best is None or e < best: best, bi = e, f
            end = bi
        d.start_utt(); d.process_raw(raw[pos:end], full_utt=True); d.end_utt()
        off = pos / 32000.0
        for s in d.seg():
            w = re.sub(r"\(\d+\)$", "", s.word)
            if w.startswith("<") or w.startswith("[") or w in ("sil",): continue
            out.append((w.replace("'", ""), off + s.start_frame / 100.0, off + (s.end_frame + 1) / 100.0))
        pos = end
    return out

STYLE_START = {"read", "aloud", "natural", "relaxed", "british", "accent"}
def preamble_spans(rec):
    """Find the voice reading its own instructions: starts near 'read this aloud', ends at 'adding nothing'."""
    spans, i = [], 0
    ws = [w for w, a, b in rec]
    while i < len(rec):
        if ws[i] in ("adding", "nothing") and any(x in ("written", "exactly") for x in ws[max(0, i - 6):i]):
            j = i + 1 if ws[i] == "adding" and i + 1 < len(ws) and ws[i + 1] == "nothing" else i
            # walk back to the start of the instructions (up to 45 s)
            k = i
            while k > 0 and rec[i][1] - rec[k - 1][1] < 45: k -= 1
            starts = [m for m in range(k, i) if ws[m] in STYLE_START and m + 3 < len(ws) and len(STYLE_START & set(ws[m:m + 8])) >= 2]
            s = starts[0] if starts else k
            spans.append((max(0.0, rec[s][1] - 0.15), rec[j][2] + 0.25))
            i = j + 1
        else: i += 1
    return spans

def cut(src, spans, dst):
    total = dur(src); keep, t = [], 0.0
    for a, b in spans:
        if a > t: keep.append((t, a))
        t = b
    if t < total: keep.append((t, total))
    expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
    run("ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", f"aselect='{expr}',asetpts=N/SR/TB",
        "-c:a", "aac", "-b:a", "56k", "-ac", "1", "-movflags", "+faststart", dst)

def gap_ends(path):
    r = subprocess.run(["ffmpeg", "-i", path, "-af", "silencedetect=noise=-38dB:d=0.15", "-f", "null", "-"], capture_output=True, text=True)
    return [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]

def timings(book, rec, total, gap_ends):
    # book tokens with (chapter, sentence) owner; sentence -1 is the spoken heading
    toks, owner, units = [], [], []
    for ci, c in enumerate(book["ch"]):
        intro = (f"{book['title']}. {book['book']}, by {book['author']}.\n\n" if ci == 0 else "") + c["h"].rstrip(".") + "."
        units.append((ci, -1, intro)); j = 0
        for p in c["b"]:
            for s in split_sentences(p): units.append((ci, j, s)); j += 1
    first_tok = []
    for u, (ci, j, text) in enumerate(units):
        first_tok.append(len(toks))
        for w in words(text): toks.append(w); owner.append(u)
    rw = [w for w, a, b in rec]
    sm = difflib.SequenceMatcher(None, toks, rw, autojunk=False)
    tok_time = [None] * len(toks)
    for blk in sm.get_matching_blocks():
        if blk.size < 2 and not (len(toks[blk.a]) > 5 if blk.size else False): continue
        for k in range(blk.size): tok_time[blk.a + k] = rec[blk.b + k][1]
    # unit start = first matched token in the unit, minus time for the unmatched words before it
    start = [None] * len(units)
    for u in range(len(units)):
        a = first_tok[u]; b = first_tok[u + 1] if u + 1 < len(units) else len(toks)
        for k in range(a, b):
            if tok_time[k] is not None:
                start[u] = tok_time[k] - 0.3 * (k - a); break
    # keep order, interpolate gaps by word count
    last = 0.0
    for u in range(len(units)):
        if start[u] is not None and start[u] < last: start[u] = None
        if start[u] is not None: last = start[u]
    known = [u for u in range(len(units)) if start[u] is not None]
    for u in range(len(units)):
        if start[u] is None:
            prev = max([k for k in known if k < u], default=None); nxt = min([k for k in known if k > u], default=None)
            t0 = start[prev] if prev is not None else 0.0; t1 = start[nxt] if nxt is not None else total
            a0 = first_tok[prev] if prev is not None else 0; a1 = first_tok[nxt] if nxt is not None else len(toks)
            start[u] = t0 + (t1 - t0) * (first_tok[u] - a0) / max(1, a1 - a0)
    # snap to the end of a pause just before, if one is close
    for u in range(len(units)):
        near = [g for g in gap_ends if start[u] - 0.8 <= g <= start[u] + 0.25]
        if near: start[u] = max(near) - 0.05
        start[u] = max(0.0, round(start[u], 2))
    marks = [0.0] * len(book["ch"]); st = [[] for _ in book["ch"]]
    for (ci, j, _), t in zip(units, start):
        if j < 0: marks[ci] = t
        else: st[ci].append(t)
    matched = sum(1 for x in tok_time if x is not None) / max(1, len(toks))
    return marks, st, matched


def book(b, voice):
    bid = b["id"]; groups = plan(b)
    marks = [0.0] * len(b["ch"]); st = [None] * len(b["ch"]); parts = []; t = 0.0
    sil = os.path.join(TMP, "sil.wav")
    if not os.path.exists(sil): run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1.0", sil)
    for gi, g in enumerate(groups):
        text, sents, hard, owner = [], [], [], []
        for ci in g:
            c = b["ch"][ci]
            intro = (f"{b['title']}. {b['book']}, by {b['author']}.\n\n" if ci == 0 else "") + c["h"].rstrip(".") + "."
            text.append(intro + "\n\n" + "\n\n".join(c["b"]))
            for k, s in enumerate(split_sentences(intro)): sents.append(s); hard.append(k == 0); owner.append((ci, -1))
            j = 0
            for p in c["b"]:
                for s in split_sentences(p): sents.append(s); hard.append(False); owner.append((ci, j)); j += 1
        raw = os.path.join(TMP, f"{bid}_{gi}.wav"); tts("\n\n\n".join(text), voice, raw)
        wav = os.path.join(TMP, f"{bid}_{gi}_n.wav"); norm(raw, wav); d = dur(wav)
        expect = sum(len(s) for s in sents) / 16.0  # roughly 16 characters a second
        if d < expect * 0.6: raise RuntimeError(f"audio looks truncated ({d:.0f}s for about {expect:.0f}s of text); lower CHUNK")
        starts = align(sents, d, gaps(wav), hard)
        for (ci, j), s0, h in zip(owner, starts, hard):
            if h: marks[ci] = round(t + s0, 2)
            if j >= 0:
                if st[ci] is None: st[ci] = []
                st[ci].append(round(t + s0, 2))
        parts += [wav, sil]; t += d + 1.0
    lst = os.path.join(TMP, bid + ".txt"); open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    mp4 = os.path.join(OUT, bid + ".mp4")
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", "aac", "-b:a", "56k", "-ac", "1", "-movflags", "+faststart", mp4)
    # The voice sometimes reads its style instructions aloud. Cut any such stretch, then time every sentence from the words actually heard.
    rec = recognise(mp4); spans = preamble_spans(rec)
    if spans:
        fixed = os.path.join(TMP, bid + "_clean.mp4"); cut(mp4, spans, fixed); os.replace(fixed, mp4); rec = recognise(mp4)
    marks, st, matched = timings(b, rec, dur(mp4), gap_ends(mp4)); marks[0] = 0.0
    if matched < 0.5: raise RuntimeError(f"narration does not match the text ({matched:.0%} of words heard)")
    json.dump({"marks": marks, "dur": round(dur(mp4), 2), "st": st, "voice": voice, "cut": len(spans), "matched": round(matched, 3)}, open(os.path.join(OUT, bid + ".json"), "w"))
    return len(groups)

books = json.load(open(BOOKS)); done = set(x.strip() for x in open(DONE) if x.strip()) if os.path.exists(DONE) else set()
used = 0
for i, b in enumerate(books):
    if b["id"] in done: continue
    need = len(plan(b))
    if used + need > 10: break
    voice = os.environ.get("VOICE") or ("Achernar" if i % 2 == 0 else "Aoede")
    try:
        used += book(b, voice); print("DONE", b["id"], voice, flush=True)
    except DailyLimit as e:
        print("DAILY_LIMIT", e, flush=True); break
    except Exception as e:
        print("FAILED", b["id"], e, flush=True); break
print("USED", used)
