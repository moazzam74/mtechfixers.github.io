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
    data["_files"] = sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []
    html = open(os.path.join(TPL, tpl + ".html"), encoding="utf-8").read()
    html = html.replace("/*__DATA__*/", "window.DATA = " + json.dumps(data) + ";")
    tmp = os.path.join(TPL, f"_tmp_{os.getpid()}_{int(time.time()*1000)}.html")
    open(tmp, "w", encoding="utf-8").write(html)
    try:
        page.goto("file://" + tmp)
        page.wait_for_function("window.READY === true", timeout=30000)
    except Exception:
        ctx.close()
        raise
    finally:
        os.remove(tmp)
    return ctx, page


def missing(job, out_dir):
    need = job.get("requires", [])
    return [f for f in need if not os.path.exists(os.path.join(out_dir, f))]


def render(browser, job, out_dir):
    if missing(job, out_dir):
        return "skipped"
    ctx, page = page_for(browser, job, out_dir)
    try:
        page.screenshot(path=os.path.join(out_dir, job["out"]), type="jpeg", quality=94)
    finally:
        ctx.close()


def ffdur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def prepare_audio(job, out_dir):
    """Download recitation clips (one per ayah), join them with short pauses and
    give the template the start/end second of every clip."""
    lead, gap, tail = job.get("lead", 1.2), job.get("gap", 0.5), job.get("tail", 2.2)
    def silence(name, sec):
        p = os.path.join(out_dir, name)
        sh(f'ffmpeg -y -loglevel error -f lavfi -t {sec:.3f} -i anullsrc=r=44100:cl=stereo -c:a pcm_s16le "{p}"')
        return p
    seq, segs, t = [silence("_s_lead.wav", lead)], [], lead
    urls = job["audio"]
    for i, url in enumerate(urls):
        mp3 = os.path.join(out_dir, f"_a{i}.mp3")
        for attempt in range(4):
            try:
                rq = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(rq, timeout=60) as r, open(mp3, "wb") as f:
                    f.write(r.read())
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))
        wav = os.path.join(out_dir, f"_a{i}.wav")
        sh(f'ffmpeg -y -loglevel error -i "{mp3}" -ar 44100 -ac 2 -c:a pcm_s16le "{wav}"')
        os.remove(mp3)
        d = ffdur(wav)
        segs.append([round(t, 3), round(t + d, 3)])
        seq.append(wav)
        last = i == len(urls) - 1
        seq.append(silence(f"_s_{i}.wav", tail if last else gap))
        t += d + (tail if last else gap)
    if t > job.get("max_sec", 90):
        raise RuntimeError(f"recitation too long for a reel: {t:.1f}s")
    lst = os.path.join(out_dir, "_list.txt")
    open(lst, "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in seq))
    track = os.path.join(out_dir, "_recitation.wav")
    sh(f'ffmpeg -y -loglevel error -f concat -safe 0 -i "{lst}" -c:a pcm_s16le "{track}"')
    for p in seq + [lst]:
        os.remove(p)
    job.setdefault("data", {})["_segs"] = segs
    job["data"]["_dur"] = round(t, 3)
    return track


BG_LIST = os.path.join(TPL, "tilawat_bg.json")


def pick_bg(job, out_dir):
    """Tilawat reels sit on a real nature clip (night sky, clouds, sea).
    A different clip is used each day; if no clip can be downloaded the template
    falls back to its own animated background, so a reel is never lost."""
    if job.get("template") != "tilawat" or job.get("data", {}).get("style") or job.get("no_bg_video"):
        return None
    try:
        clips = json.load(open(BG_LIST, encoding="utf-8"))
    except Exception:
        return None
    if not clips:
        return None
    day = int((time.time() + 5 * 3600) // 86400)
    p = os.path.join(out_dir, "_bg.mp4")
    for k in range(len(clips)):
        c = clips[(day + k) % len(clips)]
        try:
            rq = urllib.request.Request(c["url"], headers={"User-Agent": UA, "Referer": "https://www.pexels.com/"})
            with urllib.request.urlopen(rq, timeout=120) as r, open(p, "wb") as f:
                f.write(r.read())
            if os.path.getsize(p) > 200000:
                print("background clip:", c.get("name"), c.get("url"))
                return p, c
        except Exception as e:
            print("background clip failed:", c.get("url"), e)
    return None


def video(browser, job, out_dir):
    """Capture an animated HTML template frame by frame and encode an MP4 with music."""
    ensure_ffmpeg()
    if job.get("data", {}).get("sites"):
        # drop websites whose screenshots could not be taken
        job["data"]["sites"] = [s for s in job["data"]["sites"]
                                if os.path.exists(os.path.join(out_dir, s.get("desktopFull", "-")))]
        for s in job["data"]["sites"]:
            if not os.path.exists(os.path.join(out_dir, s.get("mobileFull", "-"))):
                s["mobileFull"] = ""
        if not job["data"]["sites"]:
            raise RuntimeError("no website screenshots available for the reel")
    fps = job.get("fps", 30)
    track = prepare_audio(job, out_dir) if job.get("audio") else None
    bg = pick_bg(job, out_dir)
    if bg:
        job.setdefault("data", {})["_bgvideo"] = True
        job["data"]["_bgkind"] = bg[1].get("kind", "")
    ext = "png" if bg else "jpg"
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
            if bg:
                page.screenshot(path=os.path.join(frames, f"{i:05d}.png"), type="png", omit_background=True)
            else:
                page.screenshot(path=os.path.join(frames, f"{i:05d}.jpg"), type="jpeg", quality=92)
    finally:
        ctx.close()
    if track:
        wav = track          # Quran recitation: never mixed with music
    else:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from make_reel import music
        wav = os.path.join(out_dir, "_music.wav")
        music(dur, wav)
    out = os.path.join(out_dir, job["out"])
    if bg:
        w, h = job.get("w", 1080), job.get("h", 1920)
        slow = bg[1].get("slow", 1.0)
        vf = (f"[0:v]setpts={slow}*PTS,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"fps={fps},eq=saturation=1.1[b];[b][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
        sh(f'ffmpeg -y -loglevel error -stream_loop -1 -i "{bg[0]}" -framerate {fps} -i "{frames}/%05d.png" -i "{wav}" '
           f'-filter_complex "{vf}" -map "[v]" -map 2:a -t {dur:.3f} '
           f'-c:v libx264 -preset medium -crf 19 -r {fps} '
           f'-c:a aac -b:a 160k -ar 44100 -movflags +faststart "{out}"')
        os.remove(bg[0])
    else:
        sh(f'ffmpeg -y -loglevel error -framerate {fps} -i "{frames}/%05d.jpg" -i "{wav}" '
           f'-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r {fps} '
           f'-c:a aac -b:a 160k -ar 44100 -shortest -movflags +faststart "{out}"')
    shutil.rmtree(frames, ignore_errors=True)
    os.remove(wav)
    # cover image for Instagram / Facebook: a frame with the full design, never the blank intro frame
    try:
        segs = job.get("data", {}).get("_segs")
        at = job.get("cover_at") or (segs[0][0] + 1.0 if segs else dur * 0.3)
        cov = os.path.join(out_dir, "cover.jpg")
        sh(f'ffmpeg -y -loglevel error -ss {at:.2f} -i "{out}" -frames:v 1 -q:v 2 "{cov}"')
    except Exception as e:
        print("cover failed", e)


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
