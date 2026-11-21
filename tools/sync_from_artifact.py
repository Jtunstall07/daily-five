"""Copy new stories and re-recorded voices from the Claude artifact into this installable app.

python3 tools/sync_from_artifact.py --books DIR --voices DIR --media DIR

--books   one JSON file per document of the artifact's "books" collection (the document's data object, or {"id":..,"data":{..}})
--voices  one JSON file per document of the artifact's "voices" collection (same shapes)
--media   the narration downloaded from the artifact, saved as <id>.mp4 (a book id or a voice id)

Writes data/books/<id>.json for each new story, audio/<id>.mp4 and voices/<id>.mp4, and data/index.json
(books newest first, so a new story heads "Recently added" and its shelf). Anything without its mp4 is
skipped and listed, so the app never points at audio it does not have. Run from anywhere; paths are
relative to the repository root.
"""
import argparse, glob, hashlib, json, os, shutil, sys, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
md5 = lambda p: hashlib.md5(open(p, "rb").read()).hexdigest()[:10]

def load(path):
    d = json.load(open(path))
    if isinstance(d, dict) and isinstance(d.get("data"), dict):
        x = dict(d["data"]); x.setdefault("id", d.get("id"))
        return x
    return d

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", default=""); ap.add_argument("--voices", default=""); ap.add_argument("--media", required=True)
    a = ap.parse_args()
    ix_path = os.path.join(ROOT, "data", "index.json")
    ix = json.load(open(ix_path)) if os.path.exists(ix_path) else {"books": [], "voices": {}}
    ix.setdefault("books", []); ix.setdefault("voices", {})
    builtin = {os.path.basename(p)[:-5] for p in glob.glob(os.path.join(ROOT, "data", "books", "w*d*.json"))}
    skipped, added, voiced = [], [], []

    books = {}
    for p in sorted(glob.glob(os.path.join(a.books, "*.json"))) if a.books else []:
        b = load(p); bid = b.get("id") or os.path.basename(p)[:-5]
        if not bid or bid in builtin or not isinstance(b.get("ch"), list): continue
        mp4 = os.path.join(a.media, bid + ".mp4")
        if not os.path.exists(mp4) and not os.path.exists(os.path.join(ROOT, "audio", bid + ".mp4")):
            skipped.append(bid + " (no audio)"); continue
        dst = os.path.join(ROOT, "audio", bid + ".mp4")
        if os.path.exists(mp4): shutil.copyfile(mp4, dst)
        b["id"] = bid
        if isinstance(b.get("audioUrl"), str) and b["audioUrl"].startswith("/_blob/"): b["srcAudio"] = b["audioUrl"]
        b["audioUrl"] = f"audio/{bid}.mp4?v={md5(dst)}"
        for k in ("recapUrl", "sceneFile"):
            if isinstance(b.get(k), str) and b[k].startswith("/_blob/"): b.pop(k)
        json.dump(b, open(os.path.join(ROOT, "data", "books", bid + ".json"), "w"), ensure_ascii=False)
        books[bid] = b.get("added", ""); added.append(bid)

    known = {i: "" for i in ix["books"]}
    for i in list(known):
        p = os.path.join(ROOT, "data", "books", i + ".json")
        if os.path.exists(p): known[i] = json.load(open(p)).get("added", "")
    known.update(books)
    ix["books"] = sorted(known, key=lambda i: (known[i], i), reverse=True)

    for p in sorted(glob.glob(os.path.join(a.voices, "*.json"))) if a.voices else []:
        v = load(p); vid = v.get("id") or os.path.basename(p)[:-5]
        if vid not in builtin or not isinstance(v.get("marks"), list): continue
        mp4 = os.path.join(a.media, vid + ".mp4")
        dst = os.path.join(ROOT, "voices", vid + ".mp4")
        if os.path.exists(mp4): os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(mp4, dst)
        if not os.path.exists(dst): skipped.append(vid + " (no voice audio)"); continue
        ix["voices"][vid] = {"audio": f"voices/{vid}.mp4?v={md5(dst)}", "marks": v["marks"], "dur": v.get("dur"),
                             "st": v.get("st"), "voice": v.get("voice"),
                             "src": v.get("audioUrl"), "rec": v.get("rec")}
        voiced.append(vid)

    ix["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump(ix, open(ix_path, "w"), indent=1)
    print("stories:", len(added), added)
    print("voices:", len(voiced), voiced)
    if skipped: print("SKIPPED:", skipped)

if __name__ == "__main__":
    main()
