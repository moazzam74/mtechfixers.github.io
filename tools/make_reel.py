import sys, os, json, glob, subprocess, wave
import numpy as np
DUR, FADE, FPS = 3.2, 0.6, 30
def music(total, path):
    sr = 44100
    t = np.arange(int(sr * (total + 0.5))) / sr
    audio = np.zeros_like(t)
    chords = [[261.63, 329.63, 392.00], [220.00, 261.63, 329.63], [174.61, 220.00, 261.63], [196.00, 246.94, 293.66]]
    seg = 2.0
    for i, st in enumerate(np.arange(0, total + 0.5, seg)):
        ch = chords[i % 4]
        m = (t >= st) & (t < st + seg + 0.8)
        tt = t[m] - st
        env = np.minimum(1, tt / 0.5) * np.exp(-tt * 0.5)
        for f in ch:
            audio[m] += env * (0.5 * np.sin(2 * np.pi * f * tt) + 0.12 * np.sin(2 * np.pi * 2 * f * tt))
        for k, f in enumerate(ch + [ch[0] * 2]):
            s = st + k * 0.5
            mm = (t >= s) & (t < s + 0.6)
            u = t[mm] - s
            audio[mm] += 0.28 * np.sin(2 * np.pi * 2 * f * u) * np.exp(-u * 5)
    audio /= (np.max(np.abs(audio)) + 1e-9)
    audio *= 0.45
    fl = int(sr * 1.0)
    audio[:fl] *= np.linspace(0, 1, fl)
    audio[-fl:] *= np.linspace(1, 0, fl)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    st2 = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(st2.tobytes())
def build(imgs, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    n = len(imgs)
    total = n * DUR - (n - 1) * FADE
    music(total, "music.wav")
    args = ["ffmpeg", "-y"]
    for p in imgs:
        args += ["-i", p]
    args += ["-i", "music.wav"]
    fr = int(DUR * FPS)
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0xf5f7ff,scale=2160:3840,zoompan=z='min(zoom+0.0007,1.07)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s=1080x1920:fps={FPS},format=yuv420p,setsar=1[v{i}]")
    last = "v0"
    for i in range(1, n):
        off = round(i * (DUR - FADE), 3)
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={FADE}:offset={off}[x{i}]")
        last = f"x{i}"
    args += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-map", f"{n}:a", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out]
    subprocess.run(args, check=True)
for req in sorted(glob.glob("reel-requests/*.json")):
    d = json.load(open(req))
    out = d["out"]
    if os.path.exists(out):
        continue
    imgs = [p for p in d["images"] if os.path.exists(p)]
    if len(imgs) < 2:
        print("skip", req); continue
    build(imgs, out)
    print("built", out)
