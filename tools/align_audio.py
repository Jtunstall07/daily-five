"""Word-level timing for The Daily Five narration, free and offline.

python3 align_audio.py <book.json> <audio.mp4> <out.json> [--clean <clean.mp4>]

Recognises the speech with PocketSphinx (pip install pocketsphinx), matches the
recognised words to the book text, and writes {marks, st, dur}:
  marks[ch] = start of the chapter heading, st[ch][i] = start of displayed sentence i.
With --clean, any stretch where the voice read out its own style instructions
("Read this aloud in a natural ... adding nothing") is cut out first, and the
timings refer to the cleaned file.
"""
import difflib, json, os, re, subprocess, sys, tempfile

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
def run(*a): subprocess.run(list(a), check=True)
def dur(p): return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())

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
              "Conversational, warm and human, never like a newsreader or a robot. Normal speaking pace. "
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

def gaps(path):
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

if __name__ == "__main__":
    bj, audio, outp = sys.argv[1], sys.argv[2], sys.argv[3]
    clean = sys.argv[sys.argv.index("--clean") + 1] if "--clean" in sys.argv else None
    book = json.load(open(bj))
    cache = outp + ".rec.json"
    if os.path.exists(cache) and json.load(open(cache)).get("src") == os.path.getsize(audio):
        rec = [tuple(x) for x in json.load(open(cache))["rec"]]
    else:
        rec = recognise(audio); json.dump({"src": os.path.getsize(audio), "rec": rec}, open(cache, "w"))
    spans = preamble_spans(rec)
    if spans and clean:
        cut(audio, spans, clean); audio = clean
        c2 = outp + ".clean.rec.json"
        if os.path.exists(c2) and json.load(open(c2)).get("src") == os.path.getsize(audio): rec = [tuple(x) for x in json.load(open(c2))["rec"]]
        else: rec = recognise(audio); json.dump({"src": os.path.getsize(audio), "rec": rec}, open(c2, "w"))
    elif clean and os.path.abspath(clean) != os.path.abspath(audio):
        run("cp", audio, clean); audio = clean
    total = dur(audio)
    marks, st, matched = timings(book, rec, total, gaps(audio))
    marks[0] = 0.0
    json.dump({"marks": marks, "st": st, "dur": round(total, 2), "cut": [[round(a, 2), round(b, 2)] for a, b in spans], "matched": round(matched, 3)}, open(outp, "w"))
    print(os.path.basename(bj), "cut", len(spans), "spans", round(sum(b - a for a, b in spans)), "s; matched", f"{matched:.0%}", flush=True)
