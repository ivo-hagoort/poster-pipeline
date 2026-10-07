#!/usr/bin/env python3
"""
Bouwt een poster om naar een doelverhouding met gelijke marge rondom.

Generalisatie van normalize_border.py, dat 2:3 hardcodeert. De sierrand wordt
gedetecteerd, uitgesneden en op een nieuw papiervlak geplaatst met aan alle
vier de zijden dezelfde marge m, zo gekozen dat het canvas exact de gevraagde
verhouding krijgt:

    (fw + 2m) / (fh + 2m) = r   ->   m = (r*fh - fw) / (2 * (1 - r))

Dit werkt alleen als de rand smaller is dan de doelverhouding (m >= 0) en als
de marge visueel aanvaardbaar blijft. Boven --max-margin wordt geweigerd in
plaats van een poster af te leveren die in het papier verdrinkt.

Gebruik:
  python3 normalize_ratio.py IN.png OUT.jpg --ratio 0.7071 [--max-margin 0.12]

Exit 0 = gelukt. Exit 2 = randdetectie onbetrouwbaar of marge te groot.
"""
import argparse
import sys

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

RATIOS = {
    "2:3": 2 / 3,
    "iso": 1 / (2 ** 0.5),
    "3:4": 3 / 4,
    "11:14": 11 / 14,
    "4:5": 4 / 5,
}


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
    corners = [px[0:patch, 0:patch], px[0:patch, w - patch:w],
               px[h - patch:h, 0:patch], px[h - patch:h, w - patch:w]]
    stack = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    return tuple(int(v) for v in np.median(stack, axis=0))


def build(src, dst, ratio, threshold, quality, max_margin, long_edge):
    img = Image.open(src).convert("RGB")
    W, H = img.size
    box = frame_bbox(img, threshold)
    if box is None:
        print("geen rand gevonden", file=sys.stderr)
        return 2

    x0, y0, x1, y1 = box
    fw, fh = x1 - x0 + 1, y1 - y0 + 1
    if fw < 0.5 * W or fh < 0.5 * H:
        print(f"rand te klein ({fw}x{fh} op {W}x{H})", file=sys.stderr)
        return 2

    frame = img.crop((x0, y0, x1 + 1, y1 + 1))

    m = (ratio * fh - fw) / (2 * (1 - ratio))
    if m < 0:
        print(f"rand ({fw / fh:.4f}) is breder dan doel ({ratio:.4f}); "
              f"bijvullen kan niet, hier moet gesneden worden", file=sys.stderr)
        return 2
    if m / fh > max_margin:
        print(f"marge zou {100 * m / fh:.1f}% van de randhoogte worden "
              f"(limiet {100 * max_margin:.0f}%); poster verdrinkt in het papier",
              file=sys.stderr)
        return 2

    m = int(round(m))
    out_w, out_h = fw + 2 * m, fh + 2 * m
    canvas = Image.new("RGB", (out_w, out_h), sample_paper(img))
    canvas.paste(frame, (m, m))

    if long_edge:
        s = long_edge / max(out_w, out_h)
        canvas = canvas.resize((max(1, round(out_w * s)), max(1, round(out_h * s))),
                               Image.LANCZOS)
        out_w, out_h = canvas.size

    if dst.lower().endswith((".jpg", ".jpeg")):
        canvas.save(dst, "JPEG", quality=quality, subsampling=0)
    else:
        canvas.save(dst)

    got = out_w / out_h
    print(f"{dst}: {out_w}x{out_h} ratio {got:.6f} (doel {ratio:.6f}, "
          f"afwijking {abs(got - ratio) / ratio * 100:.3f}%) marge {m}px "
          f"= {100 * m / fh:.1f}% van randhoogte")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--ratio", required=True,
                   help="getal (0.7071) of naam: " + ", ".join(RATIOS))
    p.add_argument("--threshold", type=int, default=200)
    p.add_argument("--quality", type=int, default=97)
    p.add_argument("--max-margin", type=float, default=0.12,
                   help="maximale marge als fractie van de randhoogte")
    p.add_argument("--long-edge", type=int, default=0,
                   help="schaal de lange zijde naar dit aantal pixels")
    a = p.parse_args()
    r = RATIOS.get(a.ratio, None)
    if r is None:
        r = float(a.ratio)
    sys.exit(build(a.src, a.dst, r, a.threshold, a.quality, a.max_margin, a.long_edge))


if __name__ == "__main__":
    main()
