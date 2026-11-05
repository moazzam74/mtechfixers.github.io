"""M Tech Fixers design studio.

Reads reel-requests/*.json files that have "kind": "studio" and renders
branded images / videos with a real browser (Playwright + Chromium).
Everything it writes goes under the request's out_dir (inside reels/),
and a done.json marker is written last so n8n knows the job finished.
"""
import os, sys, json, glob, subprocess, shutil, urllib.request, time, traceback

ROOT = os.path.abspath(os.getcwd())
TPL = os.path.join(ROOT, "tools", "studio")


def sh(cmd):
    subprocess.run(cmd, shell=True, check=True)


def ensure_playwright():
    try:
        import playwright  # noqa
    except ImportError:
        try:
            sh(f"{sys.executable} -m pip install -q playwright")
        except subprocess.CalledProcessError:
            sh(f"{sys.executable} -m pip install -q --break-system-packages playwright")
    sh(f"{sys.executable} -m playwright install --with-deps chromium > /dev/null")


def ensure_ffmpeg():
    if not shutil.which("ffmpeg"):
        sh("sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg")


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")

HIDE_JS = r"""
() => {
  const words = /(cookie|consent|gdpr|accept all|we use cookies|subscribe|newsletter|chat with us)/i;
  document.querySelectorAll('body *').forEach(el => {
    const cs = getComputedStyle(el);
    if ((cs.position === 'fixed' || cs.position === 'sticky') && el.offsetHeight > 0) {
      const r = el.getBoundingClientRect();
      const big = r.height > innerHeight * 0.25 || r.width > innerWidth * 0.6;
      if (words.test(el.innerText || '') || (big && r.top > innerHeight * 0.4)) el.style.display = 'none';
      if (/(chat|widget|tawk|intercom|crisp|messenger)/i.test(el.id + ' ' + el.className)) el.style.display = 'none';
    }
  });
  document.querySelectorAll('iframe').forEach(f => {
    if (/(chat|tawk|intercom|crisp|messenger|recaptcha)/i.test(f.src || '')) f.style.display = 'none';
  });
}
"""


def shoot(browser, job, out_dir):
    """Screenshot a live website on desktop and mobile."""
    name, url = job["name"], job["url"]
    results = {}
    for mode, vp, dpr, ua, full_h in (("desktop", (1440, 900), 1.5, UA, 5200),
                                      ("mobile", (390, 844), 3, UA_M, 4200)):
        ctx = browser.new_context(viewport={"width": vp[0], "height": vp[1]},
                                  device_scale_factor=dpr, user_agent=ua,
                                  is_mobile=(mode == "mobile"), has_touch=(mode == "mobile"))
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            # scroll through the page so lazy images load, then go back up
            h = page.evaluate("document.documentElement.scrollHeight")
            y = 0
            while y < min(h, full_h):
                page.evaluate(f"window.scrollTo(0,{y})")
                page.wait_for_timeout(250)
                y += vp[1] // 2
            page.evaluate("window.scrollTo(0,0)")
            page.wait_for_timeout(1200)
            page.evaluate(HIDE_JS)
            page.wait_for_timeout(300)
            top = os.path.join(out_dir, f"{name}-{mode}.jpg")
            page.screenshot(path=top, type="jpeg", quality=90)
            # fixed headers would repeat in a full-page shot, so make them static
            page.add_style_tag(content="*{scroll-behavior:auto!important}")
            page.evaluate("""() => document.querySelectorAll('body *').forEach(el => {
                const p = getComputedStyle(el).position;
                if (p === 'fixed' || p === 'sticky') el.style.position = 'absolute'; })""")
            h = min(page.evaluate("document.documentElement.scrollHeight"), full_h)
            full = os.path.join(out_dir, f"{name}-{mode}-full.jpg")
            page.screenshot(path=full, type="jpeg", quality=88, full_page=True,
                            clip={"x": 0, "y": 0, "width": vp[0], "height": max(vp[1], h)})
            results[mode] = True
        except Exception as e:
            print("shoot failed", url, mode, e)
            results[mode] = False
        finally:
            ctx.close()
    # fallback: use the portfolio screenshot if the live site could not be opened
    if not results.get("desktop") and job.get("fallback"):
        try:
            dst = os.path.join(out_dir, f"{name}-desktop.jpg")
            urllib.request.urlretrieve(job["fallback"], dst)
            shutil.copy(dst, os.path.join(out_dir, f"{name}-desktop-full.jpg"))
            results["desktop"] = "fallback"
        except Exception as e:
            print("fallback failed", e)
    return results


def page_for(browser, job, out_dir):
    w, h = job.get("w", 1080), job.get("h", 1350)
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    page = ctx.new_page()
    tpl = job.get("template", "carousel")
    data = dict(job.get("data", {}))
    data["_root"] = "file://" + ROOT + "/"
    data["_out"] = "file://" + os.path.join(ROOT, out_dir) + "/"
    html = open(os.path.join(TPL, tpl + ".html"), encoding="utf-8").read()
    html = html.replace("/*__DATA__*/", "window.DATA = " + json.dumps(data) + ";")
    tmp = os.path.join(TPL, f"_tmp_{os.getpid()}_{int(time.time()*1000)}.html")
    open(tmp, "w", encoding="utf-8").write(html)
    page.goto("file://" + tmp)
    page.wait_for_function("window.READY === true", timeout=30000)
    os.remove(tmp)
    return ctx, page


def render(browser, job, out_dir):
    ctx, page = page_for(browser, job, out_dir)
    try:
        page.screenshot(path=os.path.join(out_dir, job["out"]), type="jpeg", quality=94)
    finally:
        ctx.close()


def video(browser, job, out_dir):
    """Capture an animated HTML template frame by frame and encode an MP4 with music."""
    ensure_ffmpeg()
    fps = job.get("fps", 30)
    ctx, page = page_for(browser, job, out_dir)
    dur = page.evaluate("window.DURATION || 15")
    frames = os.path.join(out_dir, "_frames")
    os.makedirs(frames, exist_ok=True)
    try:
        page.evaluate("document.getAnimations().forEach(a => a.pause())")
        n = int(dur * fps)
        for i in range(n):
            t = i * 1000.0 / fps
            page.evaluate(f"(() => {{ const t={t}; document.getAnimations().forEach(a => a.currentTime = t); if (window.TICK) window.TICK(t); }})()")
            page.screenshot(path=os.path.join(frames, f"{i:05d}.jpg"), type="jpeg", quality=92)
    finally:
        ctx.close()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from make_reel import music
    wav = os.path.join(out_dir, "_music.wav")
    music(dur, wav)
    out = os.path.join(out_dir, job["out"])
    sh(f'ffmpeg -y -loglevel error -framerate {fps} -i "{frames}/%05d.jpg" -i "{wav}" '
       f'-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r {fps} '
       f'-c:a aac -b:a 160k -ar 44100 -shortest -movflags +faststart "{out}"')
    shutil.rmtree(frames, ignore_errors=True)
    os.remove(wav)


def main():
    todo = []
    for req in sorted(glob.glob("reel-requests/*.json")):
        try:
            d = json.load(open(req))
        except Exception:
            continue
        if d.get("kind") != "studio":
            continue
        if not os.path.exists(os.path.join(d["out_dir"], "done.json")):
            todo.append(d)
    if not todo:
        print("studio: nothing to do")
        return
    ensure_playwright()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--allow-file-access-from-files", "--disable-web-security"])
        for d in todo:
            out_dir = d["out_dir"]
            os.makedirs(out_dir, exist_ok=True)
            report = {"id": d.get("id"), "ok": True, "jobs": []}
            for job in d.get("jobs", []):
                t0 = time.time()
                try:
                    if job["type"] == "shoot":
                        r = shoot(browser, job, out_dir)
                    elif job["type"] == "render":
                        r = render(browser, job, out_dir)
                    elif job["type"] == "video":
                        r = video(browser, job, out_dir)
                    else:
                        r = "unknown job"
                    report["jobs"].append({"type": job["type"], "out": job.get("out", job.get("name")), "result": r, "sec": round(time.time() - t0, 1)})
                except Exception as e:
                    traceback.print_exc()
                    report["ok"] = False
                    report["jobs"].append({"type": job["type"], "out": job.get("out", job.get("name")), "error": str(e)[:300]})
            json.dump(report, open(os.path.join(out_dir, "done.json"), "w"), indent=1)
            print("studio built", out_dir, report["ok"])
        browser.close()


if __name__ == "__main__":
    main()
