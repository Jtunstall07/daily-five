"""Free-tier narrator for The Daily Five, recorded in SHORT TAKES so the voice never drifts.
Run: python3 narrate_job.py <books.json> <done_ids.txt> <outdir> <gemini_key>

The Gemini voices start to rush and slur after a minute or two of continuous speech, so each request is one
short take of about 1,400 characters (roughly 80 to 90 seconds), split on paragraph boundaries. Each take is
checked: its speaking speed must be normal and any spoken style instructions are cut out; a bad take is
recorded once more. Takes are cached in $CACHE (default <outdir>/cache) under names that include the voice and
model, so a book can be finished over several days with the same model: keep the cache folder between runs.
One model per book: a book already started with another model is skipped (SKIP_MODEL) so its sound stays even.

Env: MODEL (default gemini-3.8-flash-tts), VOICE (fixed voice; default alternates Achernar/Aoede by position),
CHUNK (characters per take, default 1400; "auto:N" picks the smallest size that fits the book in N takes),
MAXREQ (requests allowed this run, default 10), CACHE.
Prints DONE <id> <voice>, PARTIAL <id> <takes done>/<takes>, SKIP_MODEL <id>, DAILY_LIMIT, FAILED <id> <reason>,
then USED <n>. For each finished book writes <outdir>/<id>.mp4 and <outdir>/<id>.json
{marks, dur, st, voice, rec: "short", model}. Never touches billing."""
import base64, json, os, re, subprocess, sys, time, urllib.request, urllib.error

BOOKS, DONE, OUT, K = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
MODEL = os.environ.get("MODEL", "gemini-3.8-flash-tts")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
TMP = os.path.join(OUT, "tmp"); os.makedirs(TMP, exist_ok=True)
CHUNK = os.environ.get("CHUNK", "1400")  # characters per take, or "auto:N"
STYLE = ("Read this aloud in a natural, relaxed British English accent, like a thoughtful friend telling a true story over a drink. "
         "Conversational, warm and human, never like a newsreader or a robot. Calm, unhurried, steady speaking pace, exactly the same speed at the end as at the start. "
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
    af = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse"
    probe = subprocess.run(["ffprobe", "-v", "error", src], capture_output=True)
    if probe.returncode == 0:
        run("ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "24000", "-af", af, dst)
    else:  # some models return bare 24 kHz 16-bit PCM with no header
        run("ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", src, "-ac", "1", "-ar", "24000", "-af", af, dst)
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

STYLE_TEXT = ("Read this aloud in a natural, relaxed British English accent, like a thoughtful friend telling a true story over a drink. "
              "Conversational, warm and human, never like a newsreader or a robot. Calm, unhurried, steady speaking pace, exactly the same speed at the end as at the start. "
              "Let the voice rise and fall naturally, lean a little on the surprising details, pause briefly at the end of every sentence, "
              "a little longer between paragraphs, and about two seconds before each new chapter heading. Read every word exactly as written, adding nothing.")
STOP = set("a an the and or of on in at to as like over every each before between about never this little".split())
def preamble_spans(rec, style=STYLE_TEXT):
    """Find every stretch where the voice read its own style instructions aloud, however much of them it read."""
    st = words(style); key = set(st) - STOP
    ws = [w for w, a, b in rec]; spans, i = [], 0
    while i < len(ws):
        win = ws[i:i + 25]
        if ws[i] in key and sum(1 for w in win if w in key) >= 8:
            sub = ws[i:i + 140]
            sm = difflib.SequenceMatcher(None, st, sub, autojunk=False)
            hits = [blk.b + k for blk in sm.get_matching_blocks() for k in range(blk.size)]
            if len(hits) >= 15:
                a, b = i + hits[0], i + hits[-1]
                # the first words are often misheard: walk back to the pause that opens this section (at most 8 s)
                while a > 0 and rec[a][1] - rec[a - 1][2] < 0.6 and rec[i + hits[0]][1] - rec[a - 1][1] < 8: a -= 1
                # include the instruction's own last words if the voice got that far
                while b + 1 < len(ws) and ws[b + 1] in ("adding", "nothing", "written", "exactly", "heading", "headings"): b += 1
                spans.append((max(0.0, rec[a][1] - 0.15), rec[b][2] + 0.25)); i = b + 1; continue
        i += 1
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
    a = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    b = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(a, b))  # (start, end) of each pause

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
    # a new sentence nearly always follows a pause: snap to the strongest pause just before the estimate
    prev = -1.0
    for u in range(len(units)):
        est = start[u]
        near = [(g1 - g0) - 0.35 * abs(g1 - est) for g0, g1 in gap_ends if est - 2.0 <= g1 <= est + 0.3 and g1 - g0 >= 0.2 and g1 > prev + 0.5]
        cand = [g1 for g0, g1 in gap_ends if est - 2.0 <= g1 <= est + 0.3 and g1 - g0 >= 0.2 and g1 > prev + 0.5]
        if cand: start[u] = cand[max(range(len(near)), key=lambda k: near[k])] - 0.05
        start[u] = max(0.0, round(start[u], 2)); prev = start[u]
    marks = [0.0] * len(book["ch"]); st = [[] for _ in book["ch"]]
    for (ci, j, _), t in zip(units, start):
        if j < 0: marks[ci] = t
        else: st[ci].append(t)
    matched = sum(1 for x in tok_time if x is not None) / max(1, len(toks))
    return marks, st, matched


import hashlib, math
CACHE = os.environ.get("CACHE", os.path.join(OUT, "cache")); os.makedirs(CACHE, exist_ok=True)
MAXREQ = int(os.environ.get("MAXREQ", "10"))
GAP_TAKE, GAP_CHAPTER = 0.6, 1.4
used = 0

def units(b):
    """Paragraph units in reading order: (chapter, text, starts_chapter). A chapter's heading travels with its first paragraph."""
    out = []
    for ci, c in enumerate(b["ch"]):
        intro = (f"{b['title']}. {b['book']}, by {b['author']}.\n\n" if ci == 0 else "") + c["h"].rstrip(".") + "."
        for j, p in enumerate(c["b"]):
            out.append((ci, (intro + "\n\n" + p) if j == 0 else p, j == 0))
    return out

def takes(b, size):
    """Pack paragraph units into takes of up to `size` characters (a longer single paragraph is its own take)."""
    out, cur, n = [], [], 0
    for u in units(b):
        if cur and n + len(u[1]) > size: out.append(cur); cur, n = [], 0
        cur.append(u); n += len(u[1])
    if cur: out.append(cur)
    return out

def plan(b):
    if str(CHUNK).startswith("auto:"):
        limit = int(CHUNK.split(":")[1]); size = 1400
        while len(takes(b, size)) > limit and size < 4000: size += 100
        return takes(b, size)
    return takes(b, int(CHUNK))

def take_text(t):
    s = ""
    for k, (ci, txt, first) in enumerate(t):
        s += ("\n\n\n" if first and k else ("\n\n" if k else "")) + txt
    return s

def cut_wav(src, spans, dst):
    total = dur(src); keep, t0 = [], 0.0
    for a, b2 in spans:
        if a > t0: keep.append((t0, a))
        t0 = b2
    if t0 < total: keep.append((t0, total))
    expr = "+".join(f"between(t,{a:.3f},{b2:.3f})" for a, b2 in keep)
    run("ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", f"aselect='{expr}',asetpts=N/SR/TB", "-ac", "1", "-ar", "24000", dst)

def record_take(bid, i, text, voice, path):
    """One request (two if the first take is bad). Returns once a checked take is saved at path."""
    global used
    for attempt in range(2):
        if used >= MAXREQ: return False
        raw = os.path.join(TMP, f"{bid}_{i}_raw.wav"); tts(text, voice, raw); used += 1
        wav = os.path.join(TMP, f"{bid}_{i}_n.wav"); norm(raw, wav)
        spans = preamble_spans(recognise(wav))
        if spans:
            fixed = os.path.join(TMP, f"{bid}_{i}_c.wav"); cut_wav(wav, spans, fixed); wav = fixed
        d = dur(wav); cps = len(text) / max(d, 0.1)
        ok = 11.0 <= cps <= 19.5
        print(f"  take {i}: {d:.0f}s, {cps:.1f} chars/s{'' if ok else ' (rejected, retrying)' if attempt == 0 else ' (kept after retry)'}", flush=True)
        if ok or attempt == 1:
            run("ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "flac", path); return True
    return False

def book(b, voice):
    bid = b["id"]; ts = plan(b)
    man_p = os.path.join(CACHE, "models.json")
    man = json.load(open(man_p)) if os.path.exists(man_p) else {}
    if man.get(bid, {}).get("model", MODEL) != MODEL or man.get(bid, {}).get("voice", voice) != voice:
        return "SKIP_MODEL"
    man[bid] = {"model": MODEL, "voice": voice}; json.dump(man, open(man_p, "w"))
    paths = []
    for i, t in enumerate(ts):
        text = take_text(t)
        key = hashlib.md5((MODEL + voice + text).encode()).hexdigest()[:10]
        p = os.path.join(CACHE, f"{bid}_{i:03d}_{key}.flac"); paths.append(p)
        if os.path.exists(p): continue
        if not record_take(bid, i, text, voice, p):
            return f"PARTIAL {sum(os.path.exists(x) for x in paths)}/{len(ts)}"
    # assemble: short pause between takes, longer before a new chapter
    lst, sils = [], {}
    for i, (t, p) in enumerate(zip(ts, paths)):
        if i:
            g = GAP_CHAPTER if t[0][2] else GAP_TAKE
            sp = sils.get(g) or os.path.join(TMP, f"sil{g}.flac")
            if g not in sils:
                run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(g), "-c:a", "flac", sp); sils[g] = sp
            lst.append(sp)
        lst.append(p)
    lf = os.path.join(TMP, bid + ".txt"); open(lf, "w").write("".join(f"file '{os.path.abspath(x)}'\n" for x in lst))
    mp4 = os.path.join(OUT, bid + ".mp4")
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lf, "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-movflags", "+faststart", mp4)
    rec = recognise(mp4)
    marks, st, matched = timings(b, rec, dur(mp4), gap_ends(mp4)); marks[0] = 0.0
    if matched < 0.5: raise RuntimeError(f"narration does not match the text ({matched:.0%} of words heard)")
    json.dump({"marks": marks, "dur": round(dur(mp4), 2), "st": st, "voice": voice, "rec": "short", "model": MODEL,
               "takes": len(ts), "matched": round(matched, 3)}, open(os.path.join(OUT, bid + ".json"), "w"))
    for x in paths:
        try: os.remove(x)
        except OSError: pass
    man.pop(bid, None); json.dump(man, open(man_p, "w"))
    return "DONE"

if __name__ == "__main__":
    books = json.load(open(BOOKS)); done = set(x.strip() for x in open(DONE) if x.strip()) if os.path.exists(DONE) else set()
    for i, b in enumerate(books):
        if b["id"] in done: continue
        if used >= MAXREQ: break
        voice = os.environ.get("VOICE") or ("Achernar" if i % 2 == 0 else "Aoede")
        try:
            r = book(b, voice)
        except DailyLimit as e:
            print("DAILY_LIMIT", e, flush=True); break
        except Exception as e:
            print("FAILED", b["id"], e, flush=True); continue
        print(("DONE " + b["id"] + " " + voice) if r == "DONE" else (r.split()[0] + " " + b["id"] + " " + " ".join(r.split()[1:])).strip(), flush=True)
    print("USED", used)
