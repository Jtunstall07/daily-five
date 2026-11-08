#!/usr/bin/env python3
"""End-to-end test of the PWA against tools/serve.py on 127.0.0.1:8765/daily-five/.
Screenshots go to tools/shots/. Run: python3 tools/test_site.py
"""
import json, os, shutil, sys, time
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:8765/daily-five/"
SHOTS = os.path.join(ROOT, "tools", "shots")
CHROME = "/usr/bin/google-chrome"
os.makedirs(SHOTS, exist_ok=True)

RESULTS = []
def ok(name, cond, info=""):
    RESULTS.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(info)) if info else ""), flush=True)
    return cond

HOOK = """
(() => {
  const A = window.Audio;
  function W(...a){ const x = new A(...a); window.__aud = x; return x; }
  W.prototype = A.prototype; window.Audio = W;
})();
"""

def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")

def shot(page, name, full=False):
    page.wait_for_timeout(350)
    page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=full)

def tab(page, name):
    page.click(f'.tab[data-tab="{name}"]')
    page.wait_for_timeout(300)

def new_ctx(browser, w, h, dark=False):
    kw = dict(viewport={"width": w, "height": h}, device_scale_factor=2 if w < 600 else 1,
              color_scheme="dark" if dark else "light", ignore_https_errors=True,
              service_workers="allow", is_mobile=w < 600, has_touch=w < 600)
    ctx = browser.new_context(**kw)
    ctx.add_init_script(HOOK)
    # Google Fonts is unreachable from this sandbox (egress proxy); stub it so missing fonts are not counted as app errors
    ctx.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body="/* fonts stubbed in tests */",
              headers={"Access-Control-Allow-Origin": "*"}))
    return ctx

def watch(page, errs):
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))

def wait_sw(page):
    page.evaluate("navigator.serviceWorker.ready")
    for _ in range(40):
        if page.evaluate("!!navigator.serviceWorker.controller"):
            return True
        page.wait_for_timeout(250)
    return False

def aud(page, expr):
    return page.evaluate(f"(() => {{ const a = window.__aud; return a ? ({expr}) : null; }})()")

def wait_playing(page, minimum=0.6, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = aud(page, "{t: a.currentTime, p: !a.paused, src: a.src, rs: a.readyState}")
        if v and v["p"] and v["t"] > minimum:
            return v
        page.wait_for_timeout(250)
    return aud(page, "{t: a.currentTime, p: !a.paused, src: a.src, rs: a.readyState, err: a.error && a.error.code}")

def main():
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    with sync_playwright() as p:
        lk = dict(executable_path=CHROME, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        if proxy:
            lk["proxy"] = {"server": proxy, "bypass": "127.0.0.1,localhost"}
        browser = p.chromium.launch(**lk)

        # ---------------- phone, light ----------------
        ctx = new_ctx(browser, 390, 844)
        page = ctx.new_page(); errs = []; watch(page, errs)
        page.goto(BASE); page.wait_for_selector("#hero h2")
        ok("service worker registers and controls the page", wait_sw(page))
        sw_state = page.evaluate("navigator.serviceWorker.ready.then(r => r.active && r.active.state)")
        ok("service worker active", sw_state == "activated", sw_state)

        # manifest
        r = page.request.get(BASE + "manifest.webmanifest")
        man = r.json()
        need = {"name": "The Daily Five", "short_name": "Daily Five", "start_url": "./", "scope": "./",
                "display": "standalone", "orientation": "portrait"}
        ok("manifest fields", all(man.get(k) == v for k, v in need.items()) and man["background_color"] == man["theme_color"] == "#efe9df",
           {k: man.get(k) for k in list(need) + ["background_color", "theme_color"]})
        from PIL import Image
        import io
        icon_ok = []
        for ic in man["icons"]:
            rr = page.request.get(BASE + ic["src"])
            im = Image.open(io.BytesIO(rr.body()))
            icon_ok.append(rr.ok and f"{im.width}x{im.height}" == ic["sizes"])
        purposes = sorted(i.get("purpose", "any") for i in man["icons"])
        ok("manifest icons exist with right sizes", all(icon_ok) and purposes == ["any", "any", "maskable"], purposes)
        for extra in ["icons/apple-touch-icon.png", "icons/favicon.svg", "icons/favicon-32.png", ".nojekyll", "sw.js", "data/index.json"]:
            ok("file served: " + extra, page.request.get(BASE + extra).ok)
        head = page.evaluate("""({
          lang: document.documentElement.lang,
          vp: document.querySelector('meta[name=viewport]').content,
          tc: [...document.querySelectorAll('meta[name="theme-color"]')].map(m => m.media + ' ' + m.content),
          cap: document.querySelector('meta[name="apple-mobile-web-app-capable"]').content,
          sb: document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]').content,
          title: document.querySelector('meta[name="apple-mobile-web-app-title"]').content,
          ati: !!document.querySelector('link[rel="apple-touch-icon"]'),
          man: !!document.querySelector('link[rel="manifest"]')})""")
        ok("head metas", head["lang"] == "en-GB" and "viewport-fit=cover" in head["vp"] and len(head["tc"]) == 2 and head["sb"] == "black-translucent" and head["title"] == "Daily Five" and head["ati"] and head["man"], head)

        # Home
        ok("home: no horizontal overflow", overflow(page) <= 0, overflow(page))
        shot(page, "phone_home")
        shot(page, "phone_home_full", full=True)
        # Explore
        tab(page, "explore"); page.fill("#q", "Kahneman")
        page.wait_for_timeout(300)
        n = page.locator("#results .brow").count()
        ok("explore: search finds books", n > 0, n)
        ok("explore: no horizontal overflow", overflow(page) <= 0)
        shot(page, "phone_explore")
        page.fill("#q", "")
        page.click(".shelf >> nth=0"); page.wait_for_selector("#v-shelf.on .brow")
        shot(page, "phone_shelf")
        # Library and Review
        tab(page, "library"); ok("library renders", page.locator("#lpanel").inner_text() != "")
        shot(page, "phone_library")
        tab(page, "review"); ok("review renders", page.locator("#revBox").inner_text() != "")
        shot(page, "phone_review")

        # Book page
        page.goto(BASE + "#book-w1d1"); page.wait_for_selector("#v-detail.on #dPlay")
        sec = page.evaluate("[...document.querySelectorAll('#dBody .dsec h3')].map(h => h.textContent)")
        ok("book page: all sections", all(any(s.startswith(x) for s in sec) for x in ["Key ideas", "Words to own", "Stories worth retelling", "For your businesses", "Connects to"]), sec)
        ok("book page: rating, recap and download controls", page.locator("#dBody [data-rate]").count() == 2 and page.locator("#dRecap").count() == 1 and page.locator("#dDl").count() == 1,
           page.locator("#dDl").inner_text())
        ok("book page: no horizontal overflow", overflow(page) <= 0)
        shot(page, "phone_book")
        shot(page, "phone_book_full", full=True)
        # rating and saving a word
        page.click('#dBody [data-rate="w1d1|1"]')
        ok("rating toggles", page.get_attribute('#dBody [data-rate="w1d1|1"]', "aria-pressed") == "true")
        page.click("#dBody [data-wsave] >> nth=0")
        ok("word saved to review", page.get_attribute("#dBody [data-wsave] >> nth=0", "aria-pressed") == "true")

        # Reader
        page.click("#dRead"); page.wait_for_selector("#bBody .sent")
        ns = page.locator("#bBody .sent").count()
        ok("reader: chapter text loaded from data/books", ns > 10, ns)
        ok("reader: stage has scene art", page.locator("#stage .layer svg").count() >= 1)
        page.click("#bBody .sent >> nth=2")
        ok("reader: highlight saved", page.locator("#bBody .sent.saved").count() == 1)
        shot(page, "phone_reader")

        # Play from the book page, full player, seeking
        page.click("#bBack"); page.wait_for_selector("#v-detail.on #dPlay")
        page.click("#dPlay")
        v = wait_playing(page)
        ok("audio plays from audio/<id>.mp4", v and v["p"] and "audio/w1d1.mp4" in v["src"], v)
        page.click("#mOpen"); page.wait_for_selector("#psheet.open")
        page.wait_for_timeout(500)
        ms = page.evaluate("navigator.mediaSession && navigator.mediaSession.metadata && navigator.mediaSession.metadata.title")
        ok("media session metadata set", bool(ms), ms)
        t0 = aud(page, "a.currentTime")
        page.click("#fFwd"); page.wait_for_timeout(600)
        t1 = aud(page, "a.currentTime")
        ok("+15s skip", t1 - t0 > 13, (round(t0, 1), round(t1, 1)))
        page.click("#fBack"); page.wait_for_timeout(600)
        t2 = aud(page, "a.currentTime")
        ok("-15s skip", t1 - t2 > 13, round(t2, 1))
        page.evaluate("(() => { const r = document.getElementById('fRange'); r.value = 500; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change')); })()")
        page.wait_for_timeout(1500)
        v = aud(page, "{t: a.currentTime, d: a.duration, p: !a.paused}")
        ok("scrub to middle seeks and keeps playing", v and abs(v["t"] - v["d"] / 2) < 20 and v["p"], v)
        page.click("#fRate"); page.wait_for_timeout(200)
        ok("speed control", abs(aud(page, "a.playbackRate") - 1.1) < 0.01, page.inner_text("#fRateL"))
        page.click("#fRate"); page.click("#fRate"); page.click("#fRate"); page.click("#fRate"); page.click("#fRate"); page.click("#fRate")
        page.click("#fSleep"); ok("sleep timer", "min" in page.inner_text("#fSleepL"), page.inner_text("#fSleepL"))
        page.click("#fSleep"); page.click("#fSleep"); page.click("#fSleep"); page.click("#fSleep")
        page.click("#fChs"); page.wait_for_timeout(300)
        ok("chapter list", page.locator("#fList button").count() >= 5)
        shot(page, "phone_player")
        page.click("#fList button >> nth=2"); page.wait_for_timeout(1500)
        v = aud(page, "{t: a.currentTime, p: !a.paused}")
        ok("chapter jump", v and v["p"] and v["t"] > 200, v)
        # live sentence highlight in the reader
        page.click("#fText"); page.wait_for_selector("#v-read.on #bBody .sent")
        page.wait_for_timeout(2500)
        ok("reader: live highlight follows narration", page.locator("#bBody .sent.now").count() == 1)
        shot(page, "phone_reader_playing")
        page.click("#mPlay"); page.wait_for_timeout(300)
        ok("pause from mini player", aud(page, "a.paused"))
        # recap
        page.goto(BASE + "#book-w1d1"); page.wait_for_selector("#dRecap")
        page.click("#dRecap"); v = wait_playing(page, 0.3)
        ok("recap plays from recaps/<id>.mp4", v and "recaps/w1d1.mp4" in v["src"], v)
        page.click("#mPlay")
        # settings
        page.click('.tab[data-tab="home"]'); page.click("#openSet"); page.wait_for_selector("#ssheet.open")
        page.wait_for_timeout(600)
        ok("settings: offline section", "Storage used" in page.inner_text("#offTxt") or "downloaded" in page.inner_text("#offTxt"), page.inner_text("#offTxt"))
        page.click('#ssheet [data-k="theme"][data-v="dark"]'); page.wait_for_timeout(300)
        ok("theme switch to dark", page.evaluate("document.documentElement.dataset.theme") == "dark")
        page.evaluate("document.getElementById('ssheet').scrollTop = 9999")
        shot(page, "phone_settings_dark")
        page.click('#ssheet [data-k="theme"][data-v="system"]'); page.click("#sClose")
        # request box
        with ctx.expect_page() as newp:
            page.fill("#askIn", "The Medici bank")
            page.click("#askForm button")
        gh = newp.value
        gh_url = gh.url
        gh.close()
        from urllib.parse import unquote, urlparse, parse_qs
        want = "https://github.com/Jtunstall07/daily-five/issues/new?labels=request&title=The%20Medici%20bank&body=Requested%20from%20the%20app"
        got = parse_qs(urlparse(gh_url).query).get("return_to", [gh_url])[0]
        got = got if got.startswith("http") else "https://github.com" + got
        ok("request opens a pre-filled GitHub issue (GitHub asks to sign in first when logged out)", gh_url == want or got == want, got)
        ok("request kept in a local list", "The Medici bank" in page.inner_text("#askList"))
        ok("streak and goal card", page.locator("#gStreak").count() == 1 and page.inner_text("#gTxt") != "")
        ok("no console errors (online flows)", not errs, errs[:5])
        errs.clear()

        # ---------------- dynamic content: voice override and an extra book ----------------
        ix_path = os.path.join(ROOT, "data", "index.json")
        ix_orig = open(ix_path).read()
        vdir = os.path.join(ROOT, "voices"); os.makedirs(vdir, exist_ok=True)
        test_voice = os.path.join(vdir, "zz_test_voice.mp4")  # never touch the real voice files
        shutil.copy(os.path.join(ROOT, "audio", "w1d2.mp4"), test_voice)
        src = "/tmp/claude-0/-home-claude/df5e4708-fbf2-56b9-aad9-476dfc2b2f66/scratchpad/build"
        m = json.load(open(os.path.join(src, "audio", "w1d2.json")))
        st = json.load(open(os.path.join(src, "audio", "st_charon.json")))["w1d2"]
        ex = json.load(open(os.path.join(ROOT, "data", "books", "w1d5.json")))
        sc = json.load(open(os.path.join(ROOT, "scenes", "w1d5.json")))
        ex.update({"id": "zz_test", "title": "Test extra book", "added": "2026-11-08"})
        for c, s in zip(ex["ch"], sc["ch"]):
            if "art" in s: c["art"] = s["art"]
        open(os.path.join(ROOT, "data", "books", "zz_test.json"), "w").write(json.dumps(ex))
        open(ix_path, "w").write(json.dumps({"books": ["zz_test"], "voices": {"w1d2": {"audio": "voices/zz_test_voice.mp4", "marks": m["marks"], "dur": m["dur"], "st": st, "voice": "Test voice"}}, "updated": "2026-11-08T12:00:00Z"}))
        try:
            page.goto(BASE + "#home"); page.reload(); page.wait_for_selector("#hero h2"); page.wait_for_timeout(1500)
            ok("extra book from data/index.json appears on Home", "Test extra book" in page.inner_text("#homeRows"))
            page.goto(BASE + "#book-zz_test"); page.wait_for_selector("#v-detail.on .dart svg")
            ok("extra book page shows inline art", page.locator("#dBody .dart svg").count() == 1)
            page.click("#dRead"); page.wait_for_selector("#bBody .sent")
            ok("extra book opens in the reader", page.locator("#bBody .sent").count() > 10)
            page.goto(BASE + "#book-w1d2"); page.wait_for_selector("#dPlay"); page.wait_for_timeout(500)
            page.click("#dPlay"); v = wait_playing(page)
            ok("voice override plays voices/<id>.mp4", v and "voices/zz_test_voice.mp4" in v["src"] and v["p"], v)
            page.click("#mPlay")
            ok("no console errors (dynamic content)", not errs, errs[:5])
        finally:
            open(ix_path, "w").write(ix_orig)
            os.remove(os.path.join(ROOT, "data", "books", "zz_test.json"))
            os.remove(test_voice)
        errs.clear()

        # ---------------- offline: download a book, then go offline ----------------
        page.goto(BASE + "#book-w2d1"); page.wait_for_selector("#dDl")
        label0 = page.inner_text("#dDl")
        page.click("#dDl")
        page.wait_for_function("document.getElementById('dDl') && document.getElementById('dDl').getAttribute('aria-pressed') === 'true'", timeout=60000)
        label1 = page.inner_text("#dDl")
        ok("download for offline: tick and size", "downloaded" in label1.lower() and "MB" in label1 and page.locator("#dDl use[href='#i-check']").count() == 1, (label0, label1))
        keys = page.evaluate("caches.open('df-offline').then(c => c.keys()).then(k => k.map(r => r.url.split('/daily-five/')[1]))")
        ok("offline cache holds data, scenes, audio and recap", len(keys) == 4 and any(k.startswith("audio/w2d1.mp4") for k in keys) and any(k.startswith("recaps/w2d1.mp4") for k in keys), keys)
        shot(page, "phone_book_downloaded")
        # a second download, then remove it from the book page
        page.goto(BASE + "#book-w2d2"); page.wait_for_selector("#dDl"); page.click("#dDl")
        page.wait_for_function("document.getElementById('dDl').getAttribute('aria-pressed') === 'true'", timeout=60000)
        page.click("#dDl"); page.wait_for_timeout(500)
        ok("download toggle removes a book", page.get_attribute("#dDl", "aria-pressed") == "false")
        page.click('.tab[data-tab="home"]'); page.click("#openSet"); page.wait_for_timeout(800)
        off_txt = page.inner_text("#offTxt")
        ok("settings shows downloads and storage", "1 book downloaded" in off_txt and "Storage used" in off_txt, off_txt)
        page.wait_for_timeout(2500)
        page.evaluate("document.getElementById('ssheet').scrollTop = 9999")
        shot(page, "phone_settings_offline")
        page.click("#sClose")

        ctx.set_offline(True)
        page.goto(BASE + "#book-w2d1"); page.reload(); page.wait_for_selector("#v-detail.on #dPlay", timeout=15000)
        ok("offline: app shell loads from the service worker", True)
        page.click("#dRead"); page.wait_for_selector("#bBody .sent", timeout=10000)
        ok("offline: downloaded book opens in the reader", page.locator("#bBody .sent").count() > 10)
        ok("offline: scene art loads", page.locator("#stage .layer svg").count() >= 1)
        shot(page, "phone_offline_reader")
        page.click("#bBack"); page.wait_for_selector("#dPlay"); page.click("#dPlay")
        v = wait_playing(page, 1.0, 20)
        ok("offline: downloaded audio plays", v and v["p"] and v["t"] > 1.0, v)
        page.click("#mOpen"); page.wait_for_selector("#psheet.open")
        page.evaluate("(() => { const r = document.getElementById('fRange'); r.value = 700; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change')); })()")
        page.wait_for_timeout(2000)
        v = aud(page, "{t: a.currentTime, d: a.duration, p: !a.paused}")
        ok("offline: seeking within downloaded audio", v and abs(v["t"] - 0.7 * v["d"]) < 20 and v["p"], v)
        page.click("#fBack"); page.wait_for_timeout(800)
        v2 = aud(page, "a.currentTime")
        ok("offline: -15s works", v["t"] - v2 > 10, round(v2, 1))
        shot(page, "phone_offline_player")
        rng = page.evaluate("""(async () => { const u = window.__aud.src; const r = await fetch(u, {headers: {Range: 'bytes=100-199'}});
           const b = await r.arrayBuffer(); return {s: r.status, n: b.byteLength, cr: r.headers.get('content-range')}; })()""")
        ok("offline: service worker answers Range with 206", rng["s"] == 206 and rng["n"] == 100, rng)
        page.click("#fClose"); page.click("#mPlay")
        page.goto(BASE + "#read-w3d1"); page.wait_for_selector("#bBody .empty", timeout=10000)
        ok("offline: a book not downloaded says so gracefully", "offline" in page.inner_text("#bBody").lower())
        shot(page, "phone_offline_not_downloaded")
        ctx.set_offline(False)
        unexpected = [e for e in errs if "ERR_INTERNET_DISCONNECTED" not in e and "Failed to fetch" not in e and "net::" not in e]
        ok("offline: no unexpected console errors", not unexpected, unexpected[:5])
        ctx.close()

        # ---------------- phone, dark ----------------
        ctx = new_ctx(browser, 390, 844, dark=True)
        page = ctx.new_page(); errs = []; watch(page, errs)
        page.goto(BASE); page.wait_for_selector("#hero h2"); wait_sw(page)
        shot(page, "phone_home_dark")
        page.goto(BASE + "#book-w4d1"); page.wait_for_selector("#dPlay"); shot(page, "phone_book_dark")
        page.click("#dRead"); page.wait_for_selector("#bBody .sent"); shot(page, "phone_reader_dark")
        ok("dark: no horizontal overflow", overflow(page) <= 0)
        ok("dark: no console errors", not errs, errs[:5])
        ctx.close()

        # ---------------- desktop ----------------
        ctx = new_ctx(browser, 1200, 900)
        page = ctx.new_page(); errs = []; watch(page, errs)
        page.goto(BASE); page.wait_for_selector("#hero h2"); wait_sw(page)
        ok("desktop: home no overflow", overflow(page) <= 0); shot(page, "desktop_home")
        tab(page, "explore"); ok("desktop: explore no overflow", overflow(page) <= 0); shot(page, "desktop_explore")
        tab(page, "library"); shot(page, "desktop_library")
        tab(page, "review"); shot(page, "desktop_review")
        page.goto(BASE + "#book-w6d1"); page.wait_for_selector("#dPlay")
        ok("desktop: book no overflow", overflow(page) <= 0); shot(page, "desktop_book")
        page.click("#dRead"); page.wait_for_selector("#bBody .sent")
        ok("desktop: storyboard beats in the reader", page.locator("#stage .layer svg").count() >= 1); shot(page, "desktop_reader")
        page.click("#bBack"); page.wait_for_selector("#dPlay"); page.click("#dPlay")
        v = wait_playing(page); ok("desktop: audio plays", v and v["p"], v)
        page.click("#mOpen"); page.wait_for_selector("#psheet.open"); page.wait_for_timeout(500)
        ok("desktop: player no overflow", overflow(page) <= 0); shot(page, "desktop_player")
        page.click("#fClose"); page.click("#mPlay")
        ok("desktop: no console errors", not errs, errs[:5])
        ctx.close()
        browser.close()

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    for f in fails:
        print("FAILED:", f[0], f[2])
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
