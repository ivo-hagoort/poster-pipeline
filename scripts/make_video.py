#!/usr/bin/env python3
"""
Mockup video renderer.
Reads 7 PNGs from a source dir (named 1.png .. 7.png), builds a 1000x1000
MP4: gentle CENTERED zoom per slide with ease-in-out (alternating in/out,
no pan), soft crossfades, no audio, ~7.7s. High quality (CRF).
Smoothness: large prescale + 60fps to kill zoompan stepping.

Usage:
  python3 make_video.py --src ./src --out ./out/video.mp4
"""
import argparse, os, subprocess, sys

FPS = 60
C = 1.44          # clip duration seconds
D = round(C * FPS)
T = 0.40          # crossfade seconds
S = 1000          # output square size
PRE = 4000        # large prescale -> finer sub-pixel motion
CRF = "18"        # visual quality (lower = better)

ZAMP = [0.05, 0.045, 0.055, 0.05, 0.045, 0.055, 0.05]
ZIN  = [True, False, True, False, True, False, True]


def build(src, out):
    imgs = [os.path.join(src, f"{i}.png") for i in range(1, 8)]
    for p in imgs:
        if not os.path.isfile(p):
            print(f"missing input: {p}", flush=True); sys.exit(1)

    inputs = []
    for img in imgs:
        inputs += ["-i", img]

    P = f"(on/({D}-1))"
    ease = f"(3*{P}*{P}-2*{P}*{P}*{P})"   # smoothstep 0..1

    parts = []
    for i, (amp, zin) in enumerate(zip(ZAMP, ZIN)):
        if zin:
            z = f"1.0+{amp}*{ease}"             # ease 1.0 -> 1+amp
        else:
            z = f"1.0+{amp}-{amp}*{ease}"       # ease 1+amp -> 1.0
        x = "(iw-iw/zoom)/2"                     # centered, no pan
        y = "(ih-ih/zoom)/2"
        parts.append(
            f"[{i}:v]scale={PRE}:{PRE}:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={D}:s={S}x{S}:fps={FPS},"
            f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{i}]"
        )

    prev = "v0"
    for k in range(1, 7):
        off = k * (C - T)
        lbl = f"x{k}"
        parts.append(
            f"[{prev}][v{k}]xfade=transition=fade:duration={T}:offset={off:.5f}[{lbl}]"
        )
        prev = lbl

    filtergraph = ";".join(parts)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filtergraph,
        "-map", f"[{prev}]",
        "-r", str(FPS), "-an",
        "-c:v", "libx264", "-preset", "veryslow",
        "-crf", CRF, "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", out,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAILED\n{r.stderr[-3000:]}", flush=True); sys.exit(1)
    sz = os.path.getsize(out)
    print(f"OK {out} {sz} bytes ({sz/1024/1024:.2f} MB) | {7*C-6*T:.2f}s @ {FPS}fps", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.src, a.out)
