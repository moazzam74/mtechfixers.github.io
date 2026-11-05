import sys, os, json, glob, math, struct, wave, shutil, subprocess

DUR, FADE, FPS = 3.4, 0.6, 30
SR = 44100


def ensure_ffmpeg():
    if shutil.which("ffmpeg"):
        return
    subprocess.run("sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg", shell=True, check=True)


def music(total, path):
    # Soft, original ambient tune (pure Python, no copyright): pad chords + gentle arpeggio
    n = int(SR * (total + 0.5))
    chords = [[261.63, 329.63, 392.00], [220.00, 261.63, 329.63],
              [174.61, 220.00, 261.63], [196.00, 246.94, 293.66]]
    seg = 2.0
    buf = [0.0] * n
    two_pi = 2 * math.pi
    nseg = int(math.ceil((total + 0.5) / seg)) + 1
    for i in range(nseg):
        st = i * seg
        ch = chords[i % 4]
        a = int(st * SR)
        b = min(n, int((st + seg + 0.8) * SR))
        for j in range(a, b):
            tt = j / SR - st
            env = min(1.0, tt / 0.5) * math.exp(-tt * 0.5)
            v = 0.0
            for f in ch:
                v += 0.5 * math.sin(two_pi * f * tt) + 0.1 * math.sin(two_pi * 2 * f * tt)
            buf[j] += env * v
        notes = ch + [ch[0] * 2]
        for k, f in enumerate(notes):
            s = st + k * 0.5
            a2 = int(s * SR)
            b2 = min(n, int((s + 0.6) * SR))
            for j in range(a2, b2):
                u = j / SR - s
                buf[j] += 0.28 * math.sin(two_pi * 2 * f * u) * math.exp(-u * 5)
    peak = max(abs(x) for x in buf) or 1.0
    fl = int(SR * 1.0)
    out = bytearray()
    for j, x in enumerate(buf):
        g = 0.45 / peak
        if j < fl:
            g *= j / fl
        if j > n - fl:
            g *= max(0.0, (n - j) / fl)
        s = int(max(-1.0, min(1.0, x * g)) * 32767)
        out += struct.pack("<hh", s, s)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(out))


def build(imgs, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    n = len(imgs)
    total = n * DUR - (n - 1) * FADE
    music(total, "music.wav")
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for p in imgs:
        args += ["-i", p]
    args += ["-i", "music.wav"]
    fr = int(DUR * FPS)
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0xf5f7ff,scale=2160:3840,"
            f"zoompan=z='min(zoom+0.0006,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={fr}:s=1080x1920:fps={FPS},format=yuv420p,setsar=1[v{i}]")
    last = "v0"
    for i in range(1, n):
        off = round(i * (DUR - FADE), 3)
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={FADE}:offset={off}[x{i}]")
        last = f"x{i}"
    args += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-map", f"{n}:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
             "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
             "-shortest", "-movflags", "+faststart", out]
    subprocess.run(args, check=True)


def main():
    reqs = sorted(glob.glob("reel-requests/*.json"))
    todo = []
    for req in reqs:
        d = json.load(open(req))
        if not os.path.exists(d["out"]):
            todo.append(d)
    if not todo:
        print("nothing to do")
        return
    ensure_ffmpeg()
    for d in todo:
        imgs = [p for p in d["images"] if os.path.exists(p)]
        if len(imgs) < 2:
            print("skip", d["out"])
            continue
        build(imgs, d["out"])
        print("built", d["out"])


if __name__ == "__main__":
    main()
