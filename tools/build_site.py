#!/usr/bin/env python3
"""Build The Daily Five as a static PWA for GitHub Pages.

Reads the narration build folder (the same inputs as build.py there) and writes the site into the repo root:
  index.html, manifest.webmanifest, sw.js, icons/, data/books/<id>.json, scenes/<id>.json,
  audio/<id>.mp4, recaps/<id>.mp4, .nojekyll and (only if missing) data/index.json.

Usage:  python3 tools/build_site.py [--src /path/to/build] [--icons]
  --icons  re-render the PNG icons from tools/icon.svg (needs Python Playwright and Chrome)

The source folder is only read, never written.
"""
import argparse, glob, hashlib, json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
DEFAULT_SRC = "/tmp/claude-0/-home-claude/df5e4708-fbf2-56b9-aad9-476dfc2b2f66/scratchpad/build"
CHROME = "/usr/bin/google-chrome"

LIGHT_BG = "#efe9df"

SHELVES = [
 {"id":"psychology","name":"Psychology and Human Nature","hue":215,"motif":"circle",
  "blurb":"How the mind actually works: the shortcuts it takes, the animal underneath, and what people do to survive the worst.",
  "books":["w1d1","w1d5","w5d2","w5d3","w5d4","w5d5"]},
 {"id":"philosophy","name":"Philosophy","hue":265,"motif":"star",
  "blurb":"Emperors, ministers, a freed slave, an essayist and a misread genius, all writing about how to live.",
  "books":["w8d1","w8d2","w8d3","w8d4","w8d5"]},
 {"id":"business","name":"Business and Money","hue":150,"motif":"rise",
  "blurb":"How wealth is really built and kept, told through the lives of the people who built it.",
  "books":["w3d1","w3d2","w3d3","w3d4","w3d5","w1d3"]},
 {"id":"sales","name":"Sales and Marketing","hue":345,"motif":"wave",
  "blurb":"Persuasion, positioning and story: why people say yes, and how the best sellers got them there.",
  "books":["w4d1","w4d2","w4d3","w4d4","w4d5","w1d2"]},
 {"id":"empires","name":"Empires and History","hue":30,"motif":"arch",
  "blurb":"Why great powers rise and fall, from Rome and the Silk Roads to 1914 and the long story of everybody.",
  "books":["w6d1","w6d2","w6d3","w6d4","w6d5","w2d3","w5d1"]},
 {"id":"strategy","name":"Strategy and Power","hue":0,"motif":"chev",
  "blurb":"Generals, princes and a fighter pilot on how to win when someone is trying to stop you.",
  "books":["w2d1","w2d2","w2d4","w2d5","w1d4"]},
 {"id":"mastery","name":"Mastery and Craft","hue":45,"motif":"grid",
  "blurb":"How real skill is built, and the thing that stops most people starting.",
  "books":["w7d1","w7d2","w7d3","w7d4","w7d5"]},
 {"id":"risk","name":"Risk and Decisions","hue":190,"motif":"tri",
  "blurb":"What to do when you cannot know: wild events, fragile systems, honest forecasting and how much to bet.",
  "books":["w9d1","w9d2","w9d3","w9d4","w9d5"]},
]
SCENES = set("childhood study city sea battle court market road warroom mind crowd ruin prison workshop stars money letter fire storm dawn".split())

# fields kept in the light catalogue inlined in index.html (chapter text and sentence timing load per book)
CAT_FIELDS = ["id", "shelf", "title", "book", "author", "year", "field", "mins", "added", "wiifm", "take", "recall",
              "words", "stories", "biz", "connects", "recap", "recapUrl", "note",
              "audioUrl", "marks", "adur", "dataFile", "sceneFile", "dlSize"]
CH_FIELDS = ["h", "scene", "cap", "when"]


def md5_file(p, n=10):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def md5_bytes(b, n=10):
    return hashlib.md5(b).hexdigest()[:n]


def no_dashes(obj):
    """Same em dash rule as build.py: ' — ' becomes ', ' everywhere in the book data."""
    txt = json.dumps(obj, ensure_ascii=False)
    txt = re.sub(r"\s*—\s*", ", ", txt)
    return json.loads(txt)


def copy_if_changed(src, dst):
    if os.path.exists(dst):
        a, b = os.stat(src), os.stat(dst)
        if a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime):
            return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def write_if_changed(path, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    if os.path.exists(path) and open(path, "rb").read() == data:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return True


def shelf_of(bid):
    for s in SHELVES:
        if bid in s["books"]:
            return s["id"]
    return SHELVES[0]["id"]


def load_books(S):
    books = []
    for w in range(1, 10):
        p = os.path.join(S, f"w{w}.json")
        if not os.path.exists(p):
            print("missing", p)
            continue
        for b in json.load(open(p)):
            b["added"] = "2026-11-03"
            b["mins"] = round(sum(len(" ".join(c["b"]).split()) for c in b["ch"]) / 145)
            for c in b["ch"]:
                if c.get("scene") not in SCENES:
                    c["scene"] = "dawn"
            books.append(b)
    extras = {}
    for f in glob.glob(os.path.join(S, "extras", "w*.json")):
        try:
            extras.update(json.load(open(f)))
        except Exception as e:
            print("bad extras", f, e)
    for b in books:
        x = extras.get(b["id"])
        if x:
            for k in ("words", "stories", "biz", "connects", "recap"):
                if k in x:
                    b[k] = x[k]
    # chapter captions can be overridden by the art files (the art itself already lives in scenes/<id>.json)
    art = {}
    for f in sorted(p for p in glob.glob(os.path.join(S, "art", "*.json")) if re.search(r"/(pilot_w6d1|w\d)\.json$", p)):
        art.update(json.load(open(f)))
    for b in books:
        a = art.get(b["id"])
        if a and len(a) == len(b["ch"]):
            for c, x in zip(b["ch"], a):
                if "<script" not in x["svg"].lower():
                    c["cap"] = x.get("cap") or c.get("cap")
    return books


def build(S, render_icons):
    stc = json.load(open(os.path.join(S, "audio", "st_charon.json")))
    books = load_books(S)
    print(len(books), "books")
    sizes = {}
    n_audio = n_recap = n_copied = 0
    cat = []
    for b in books:
        bid = b["id"]
        b["shelf"] = shelf_of(bid)
        # media: narration (Charon, AAC in MP4), 3-minute recap, scene art
        m4 = os.path.join(S, "m4", bid + ".mp4")
        mj = os.path.join(S, "audio", bid + ".json")
        dl = 0
        if os.path.exists(m4) and os.path.exists(mj):
            dst = os.path.join(SITE, "audio", bid + ".mp4")
            n_copied += copy_if_changed(m4, dst)
            m = json.load(open(mj))
            b["audioUrl"] = f"audio/{bid}.mp4?v={md5_file(dst)}"
            b["marks"] = m["marks"]
            b["adur"] = m["dur"]
            if bid in stc:
                b["st"] = stc[bid]
            b["mins"] = round(m["dur"] / 60)
            dl += os.path.getsize(dst)
            n_audio += 1
        rc = os.path.join(S, "recaps", bid + ".mp4")
        if os.path.exists(rc):
            dst = os.path.join(SITE, "recaps", bid + ".mp4")
            n_copied += copy_if_changed(rc, dst)
            b["recapUrl"] = f"recaps/{bid}.mp4?v={md5_file(dst)}"
            dl += os.path.getsize(dst)
            n_recap += 1
        sc = os.path.join(S, "scenes", bid + ".json")
        if os.path.exists(sc):
            sj = json.load(open(sc))
            if len(sj.get("ch", [])) != len(b["ch"]):
                print("scene chapter count mismatch", bid)
            dst = os.path.join(SITE, "scenes", bid + ".json")
            n_copied += copy_if_changed(sc, dst)
            b["sceneFile"] = f"scenes/{bid}.json?v={md5_file(dst)}"
            dl += os.path.getsize(dst)
        b = no_dashes(b)
        # full book file: everything except the art (which stays in scenes/), same shape as a nightly extra book
        full = {k: v for k, v in b.items() if k not in ("sceneFile",)}
        full["ch"] = [{k: c[k] for k in ("h", "b", "scene", "cap", "when") if k in c} for c in b["ch"]]
        raw = json.dumps(full, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        write_if_changed(os.path.join(SITE, "data", "books", bid + ".json"), raw)
        b["dataFile"] = f"data/books/{bid}.json?v={md5_bytes(raw)}"
        dl += len(raw)
        b["dlSize"] = dl
        entry = {k: b[k] for k in CAT_FIELDS if k in b}
        entry["ch"] = [{k: c[k] for k in CH_FIELDS if k in c} for c in b["ch"]]
        cat.append(entry)
    print(n_audio, "narrated;", n_recap, "recaps;", n_copied, "media/scene files copied")

    # index.html
    tpl = open(os.path.join(HERE, "app_template.html"), encoding="utf-8").read()
    txt = json.dumps(cat, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/", txt).replace("/*__SHELVES__*/", json.dumps(SHELVES, ensure_ascii=False))
    build_id = md5_bytes(html.encode("utf-8"), 12)
    html = html.replace("/*__BUILD__*/", build_id)
    write_if_changed(os.path.join(SITE, "index.html"), html)
    print("index.html", len(html.encode("utf-8")) // 1024, "KB; catalogue", len(txt.encode("utf-8")) // 1024, "KB; build", build_id)

    # icons
    icons_dir = os.path.join(SITE, "icons")
    need = ["icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "favicon-32.png", "favicon.svg"]
    if render_icons or not all(os.path.exists(os.path.join(icons_dir, n)) for n in need):
        make_icons(icons_dir)

    # manifest
    manifest = {
        "name": "The Daily Five",
        "short_name": "Daily Five",
        "description": "A short narrated story every day: the lives behind great books, with words, stories and lessons to keep.",
        "id": "./",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": LIGHT_BG,
        "theme_color": LIGHT_BG,
        "lang": "en-GB",
        "dir": "ltr",
        "categories": ["books", "education"],
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    write_if_changed(os.path.join(SITE, "manifest.webmanifest"), json.dumps(manifest, indent=2) + "\n")

    # data/index.json is the nightly feed: only create it when missing, never overwrite it
    ix = os.path.join(SITE, "data", "index.json")
    if not os.path.exists(ix):
        write_if_changed(ix, json.dumps({"books": [], "voices": {}, "updated": "2026-11-08T00:00:00Z"}, indent=2) + "\n")

    # service worker: version follows the shell files
    precache = ["index.html", "manifest.webmanifest", "data/index.json"] + ["icons/" + n for n in need]
    shell_hash = hashlib.md5()
    for p in ["index.html", "manifest.webmanifest"] + ["icons/" + n for n in need]:
        shell_hash.update(open(os.path.join(SITE, p), "rb").read())
    sw = open(os.path.join(HERE, "sw_template.js"), encoding="utf-8").read()
    sw = sw.replace("__BUILD__", shell_hash.hexdigest()[:12]).replace("__PRECACHE__", json.dumps(precache))
    write_if_changed(os.path.join(SITE, "sw.js"), sw)

    write_if_changed(os.path.join(SITE, ".nojekyll"), "")
    print("done")


def make_icons(out):
    os.makedirs(out, exist_ok=True)
    src = open(os.path.join(HERE, "icon.svg"), encoding="utf-8").read()
    variants = {
        "rounded": src.replace("__RX__", "112").replace("__SCALE__", "0.94"),
        "full": src.replace("__RX__", "0").replace("__SCALE__", "0.94"),
        "mask": src.replace("__RX__", "0").replace("__SCALE__", "0.74"),
        "fav": src.replace("__RX__", "96").replace("__SCALE__", "1.08"),
    }
    write_if_changed(os.path.join(out, "favicon.svg"), variants["fav"])
    jobs = [("icon-192.png", "rounded", 192), ("icon-512.png", "rounded", 512), ("icon-maskable-512.png", "mask", 512),
            ("apple-touch-icon.png", "full", 180), ("favicon-32.png", "fav", 32)]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = br.new_page(device_scale_factor=1)
        for name, var, size in jobs:
            svg = variants[var].replace('width="512" height="512"', f'width="{size}" height="{size}"', 1)
            pg.set_viewport_size({"width": size, "height": size})
            pg.set_content(f'<html><body style="margin:0;background:transparent">{svg}</body></html>')
            pg.locator("svg").screenshot(path=os.path.join(out, name), omit_background=True)
        br.close()
    print("icons rendered")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--icons", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(a.src):
        sys.exit("source folder not found: " + a.src)
    build(a.src, a.icons)
