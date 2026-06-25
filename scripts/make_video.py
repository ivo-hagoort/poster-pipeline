#!/usr/bin/env python3
"""
Mockup video renderer.
Reads 7 PNGs from a source dir (named 1.png .. 7.png), builds a 1000x1000
MP4: subtle right-to-left pan + light zoom (Ken Burns) per slide, soft
crossfades, no audio, ~7.7s, two-pass ~490k (lands ~470 KB).

Usage:
  python3 make_video.py --src ./src --out ./out/video.mp4
"""
import argparse, os, subprocess, sys

FPS = 25
D = 36            # frames per slide -> 1.44s
T = 0.40          # crossfade seconds
C = D / FPS       # clip duration
S = 1000          # output square size
PRE = 2048        # prescale for smooth zoompan
BITRATE = "490k"  # two-pass target; ~470 KB output

# subtle per-slide variation
ZENDS = [1.06, 1.08, 1.07, 1.09, 1.06, 1.08, 1.10]
YDRIFT = [0, -18, 12, -10, 16, -14, 0]


def build(src, out):
    imgs = [os.path.join(src, f"{i}.png") for i in range(1, 8)]
    for p in imgs:
        if not os.path.isfile(p):
            print(f"missing input: {p}", flush=True)
            sys.exit(1)

    inputs = []
    for img in imgs:
        inputs += ["-i", img]   # single still frame; zoompan d= expands it

    parts = []
    for i, (ze, dy) in enumerate(zip(ZENDS, YDRIFT)):
        z = f"1.0+({ze}-1.0)*on/({D}-1)"
        x = f"(iw-iw/zoom)*(1-on/({D}-1))"          # right -> left
        y = f"(ih-ih/zoom)/2+({dy})*(on/({D}-1))"   # centered + tiny drift
        parts.append(
            f"[{i}:v]scale={PRE}:{PRE},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={D}:s={S}x{S}:fps={FPS},"
            f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{i}]"
        )

    prev = "v0"
    for k in range(1, 7):
        off = k * (C - T)
        out_label = f"x{k}"
        parts.append(
            f"[{prev}][v{k}]xfade=transition=fade:duration={T}:offset={off:.5f}[{out_label}]"
        )
        prev = out_label

    filtergraph = ";".join(parts)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    base = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filtergraph,
        "-map", f"[{prev}]",
        "-r", str(FPS), "-an",
        "-c:v", "libx264", "-preset", "veryslow",
        "-pix_fmt", "yuv420p", "-b:v", BITRATE,
        "-movflags", "+faststart",
    ]
    p1 = base + ["-pass", "1", "-f", "mp4", "/dev/null"]
    p2 = base + ["-pass", "2", out]

    for label, cmd in [("pass1", p1), ("pass2", p2)]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{label} FAILED\n{r.stderr[-3000:]}", flush=True)
            sys.exit(1)

    sz = os.path.getsize(out)
    print(f"OK {out} {sz} bytes ({sz/1024:.1f} KB)", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.src, a.out)
