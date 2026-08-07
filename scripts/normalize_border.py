#!/usr/bin/env python3
"""
Normaliseert de buitenmarge van een posterbestand.

Detecteert de sierrand (de bounding box van alles wat donkerder is dan het
papier), snijdt het canvas weg en bouwt het opnieuw op met exact dezelfde
marge aan alle vier de zijden, op een canvas van precies 2:3.

De sierrand zelf wordt niet vervormd; hij wordt hooguit 1 px herschaald zodat
de maten even zijn en de 2:3 verhouding exact uitkomt.

Gebruik:
  python3 normalize_border.py IN.png OUT.jpg [--threshold 200] [--quality 97]

Exit 0 = genormaliseerd. Exit 2 = randdetectie onbetrouwbaar, niets geschreven.
"""
import argparse
import sys

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def frame_bbox(img, threshold):
    grey = np.array(img.convert("L"))
    dark_rows = np.where(grey.min(axis=1) < threshold)[0]
    dark_cols = np.where(grey.min(axis=0) < threshold)[0]
    if len(dark_rows) == 0 or len(dark_cols) == 0:
        return None
    return dark_cols[0], dark_rows[0], dark_cols[-1], dark_rows[-1]


def sample_paper(img):
    px = np.array(img)
    h, w = px.shape[:2]
    patch = max(4, min(w, h) // 200)
    corners = [
        px[0:patch, 0:patch],
        px[0:patch, w - patch:w],
        px[h - patch:h, 0:patch],
        px[h - patch:h, w - patch:w],
    ]
    stack = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    return tuple(int(v) for v in np.median(stack, axis=0))


def normalize(src, dst, threshold, quality):
    img = Image.open(src).convert("RGB")
    W, H = img.size
    box = frame_bbox(img, threshold)
    if box is None:
        print("geen rand gevonden, threshold te laag of te hoog", file=sys.stderr)
        return 2

    x0, y0, x1, y1 = box
    fw, fh = x1 - x0 + 1, y1 - y0 + 1

    # Sanity: de rand moet het grootste deel van het canvas beslaan.
    if fw < 0.5 * W or fh < 0.5 * H:
        print(f"rand te klein ({fw}x{fh} op {W}x{H}), niets gedaan", file=sys.stderr)
        return 2

    frame = img.crop((x0, y0, x1 + 1, y1 + 1))
    fw -= fw % 2
    fh -= fh % 2
    frame = frame.resize((fw, fh), Image.LANCZOS)

    # Gelijke marge m aan alle zijden met (fw+2m)/(fh+2m) = 2/3  ->  m = fh - 1.5*fw
    m = int(round(fh - 1.5 * fw))
    if m < 0:
        # De rand is breder dan 2:3. Marge kan dan niet gelijk zijn zonder
        # in de rand te snijden; hoogte bijvullen is de veiligste keuze.
        print(f"rand is breder dan 2:3 (ratio {fw / fh:.4f}), opnieuw genereren", file=sys.stderr)
        return 2

    out_w, out_h = fw + 2 * m, fh + 2 * m
    paper = sample_paper(img)
    canvas = Image.new("RGB", (out_w, out_h), paper)
    canvas.paste(frame, (m, m))

    if dst.lower().endswith((".jpg", ".jpeg")):
        canvas.save(dst, "JPEG", quality=quality, subsampling=0)
    else:
        canvas.save(dst)

    check = frame_bbox(canvas, threshold)
    cx0, cy0, cx1, cy1 = check
    margins = (cy0, out_h - 1 - cy1, cx0, out_w - 1 - cx1)
    print(f"canvas {out_w}x{out_h} ratio {out_w / out_h:.6f}")
    print(f"marge top/bottom/left/right {margins}")
    if max(margins) - min(margins) > 2:
        print("marges na normalisatie nog ongelijk", file=sys.stderr)
        return 2
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--threshold", type=int, default=200)
    p.add_argument("--quality", type=int, default=97)
    a = p.parse_args()
    sys.exit(normalize(a.src, a.dst, a.threshold, a.quality))


if __name__ == "__main__":
    main()
