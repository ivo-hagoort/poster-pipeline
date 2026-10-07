#!/usr/bin/env python3
"""
Greenscreen poster compositor.

Replaces the green aperture of a mockup with a poster while keeping the
original lighting. Shadows, light bands, falloff and glass reflections are
measured from the green channel and re-applied to the poster in linear light.

Only the green channel is used for shading: on a green screen the red and blue
channels are spill and noise, so any colour inferred from them tints the result.

usage: composite.py <mockup.png> <poster.jpg> <out.jpg> [fit] [strength] [scale]
       fit: cover (default) | stretch | contain
"""
import sys
import numpy as np
import cv2

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from aspect import true_ratio

AA = 4  # supersampling factor for polygon edges


# ---------- colour (sRGB <-> linear) ----------

def to_linear(x):
    x = x.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    y = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.clip(y * 255.0 + 0.5, 0, 255).astype(np.uint8)


# ---------- green detection ----------

def green_core(bgr):
    """Binary mask of the green aperture, holes filled, largest blob only."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s = hsv[:, :, 0], hsv[:, :, 1]
    b, g, r = (bgr[:, :, i].astype(np.int16) for i in (0, 1, 2))
    m = (((h > 33) & (h < 92)) & (s > 70) &
         (g > r + 25) & (g > b + 25)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        m = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8) * 255
    ff = m.copy()
    hh, ww = m.shape
    cv2.floodFill(ff, np.zeros((hh + 2, ww + 2), np.uint8), (0, 0), 255)
    return m | cv2.bitwise_not(ff)


def _order(q):
    s, d = q.sum(axis=1), np.diff(q, axis=1).ravel()
    return np.array([q[np.argmin(s)], q[np.argmin(d)],
                     q[np.argmax(s)], q[np.argmax(d)]], np.float32)


def _rough_quad(binary):
    cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for f in np.arange(0.005, 0.12, 0.002):
        ap = cv2.approxPolyDP(hull, f * peri, True)
        if len(ap) == 4:
            return _order(ap.reshape(4, 2).astype(np.float32)), c.reshape(-1, 2).astype(np.float32)
    return _order(cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32)), c.reshape(-1, 2).astype(np.float32)


def corners(binary):
    """Exact aperture quad: fit a line to each of the four green edges and
    intersect them. approxPolyDP alone cuts the corners, which leaves a green
    rim once the poster is laid in."""
    rough, pts = _rough_quad(binary)
    quad = rough.copy()
    for _ in range(3):
        lines = []
        for i in range(4):
            a, b = quad[i], quad[(i + 1) % 4]
            ab = b - a
            L = np.linalg.norm(ab)
            if L < 1e-6:
                lines.append(None); continue
            u = ab / L
            nvec = np.array([-u[1], u[0]], np.float32)
            rel = pts - a
            t = rel @ u                      # position along the edge
            dist = np.abs(rel @ nvec)        # distance from the edge
            sel = (t > 0.12 * L) & (t < 0.88 * L) & (dist < max(6.0, 0.04 * L))
            if sel.sum() < 12:
                lines.append(None); continue
            P = pts[sel]
            # An occluder (a hand, a leaf) eats INTO the green, so the contour
            # dips inward and drags a plain fit with it. The true edge is the
            # outer envelope, so fit to the outermost band of points only.
            centre = quad.mean(axis=0)
            outward = nvec if np.dot(nvec, a - centre) > 0 else -nvec
            for _ in range(2):
                off = (P - P.mean(axis=0)) @ outward
                lo, hi = np.percentile(off, 45), np.percentile(off, 99)
                keep = (off >= lo) & (off <= hi)
                if keep.sum() >= 12:
                    P = P[keep]
            mean = P.mean(axis=0)
            _, _, vt = np.linalg.svd(P - mean)
            d = vt[0]
            lines.append((mean, d / (np.linalg.norm(d) + 1e-9)))
        new = quad.copy()
        for i in range(4):
            l1, l2 = lines[(i - 1) % 4], lines[i]
            if l1 is None or l2 is None:
                continue
            (p1, d1), (p2, d2) = l1, l2
            A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], np.float64)
            if abs(np.linalg.det(A)) < 1e-6:
                continue
            t = np.linalg.solve(A, (p2 - p1).astype(np.float64))
            new[i] = (p1 + d1 * t[0]).astype(np.float32)
        if np.abs(new - quad).max() < 0.05:
            quad = new
            break
        quad = new
    if not np.isfinite(quad).all() or cv2.contourArea(quad.astype(np.float32)) < 0.5 * cv2.contourArea(rough):
        return rough
    return _order(quad)


def quad_alpha(shape, quad, inset=1.0):
    """Anti-aliased polygon coverage, pulled in by `inset` px to kill fringing."""
    c = quad.mean(axis=0)
    v = quad - c
    n = np.linalg.norm(v, axis=1, keepdims=True)
    shrunk = c + v * (1.0 - inset / np.maximum(n, 1e-6))
    big = np.zeros((shape[0] * AA, shape[1] * AA), np.uint8)
    cv2.fillPoly(big, [np.round(shrunk * AA).astype(np.int32)], 255, lineType=cv2.LINE_8)
    return (cv2.resize(big, (shape[1], shape[0]),
                       interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0)


def occluders(bgr, green, quad_a):
    """Things in front of the aperture (a hand, a leaf): inside the quad but
    clearly not green. Chroma keying gives this for free."""
    inside = quad_a > 0.5
    not_green = green == 0
    occ = (inside & not_green).astype(np.uint8) * 255
    occ = cv2.morphologyEx(occ, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(occ, 8)
    keep = np.zeros_like(occ)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] > 0.004 * inside.sum():
            keep[lab == i] = 255
    return cv2.GaussianBlur(keep.astype(np.float32) / 255.0, (0, 0), 1.2)


# ---------- poster framing ----------

def prepare(poster, tw, th, fit):
    ph, pw = poster.shape[:2]
    if fit == "stretch":
        return cv2.resize(poster, (tw, th), interpolation=cv2.INTER_AREA)
    scale = max(tw / pw, th / ph) if fit == "cover" else min(tw / pw, th / ph)
    nw, nh = max(1, int(round(pw * scale))), max(1, int(round(ph * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    r = cv2.resize(poster, (nw, nh), interpolation=interp)
    if fit == "cover":
        x, y = (nw - tw) // 2, (nh - th) // 2
        return r[y:y + th, x:x + tw]
    out = np.full((th, tw, 3), 255, poster.dtype)
    x, y = (tw - nw) // 2, (th - nh) // 2
    out[y:y + nh, x:x + nw] = r
    return out


# ---------- shading ----------

def shading(bgr, green, quad_a, strength):
    """Scalar light multiplier from the green channel, extended across pixels
    where no green was visible (edges, occluders) so the matte has no seams."""
    g = bgr[:, :, 1].astype(np.float32)
    sel = green > 0
    g_ref = float(np.percentile(g[sel], 95))
    lum = np.clip(g / max(g_ref, 1e-6), 0.0, 1.3)

    need = (quad_a > 0.01) & (~sel)
    if need.any():
        src = np.where(sel, lum, 0).astype(np.float32)
        w = sel.astype(np.float32)
        for k in (9, 21, 45, 91):
            sb = cv2.blur(src, (k, k))
            wb = cv2.blur(w, (k, k))
            fill = np.divide(sb, np.maximum(wb, 1e-6))
            src = np.where(w > 0.02, src, fill * (wb > 1e-4))
            w = np.maximum(w, (wb > 1e-4).astype(np.float32))
        lum = np.where(sel, lum, src)
    lum = np.where(lum > 0, lum, 1.0)
    lum = cv2.GaussianBlur(lum, (0, 0), 0.6)
    return (1.0 + (lum - 1.0) * strength).astype(np.float32)


# ---------- main ----------

def composite(mockup_path, poster_path, out_path, fit="auto", strength=1.0, scale=1):
    mock = cv2.imread(mockup_path, cv2.IMREAD_COLOR)
    poster = cv2.imread(poster_path, cv2.IMREAD_COLOR)
    if mock is None:
        raise SystemExit(f"cannot read mockup {mockup_path}")
    if poster is None:
        raise SystemExit(f"cannot read poster {poster_path}")

    green = green_core(mock)
    quad = corners(green)

    if scale != 1:
        mock = cv2.resize(mock, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        green = cv2.resize(green, (mock.shape[1], mock.shape[0]), interpolation=cv2.INTER_NEAREST)
        quad = quad * scale

    # negative inset = grow outward: covers the last sub-pixel of green, at the
    # cost of a pixel of wooden frame. A green rim is visible, a lost pixel is not.
    qa = quad_alpha(mock.shape[:2], quad, inset=-1.0 * scale)
    occ = occluders(mock, green, qa)
    alpha = np.clip(qa * (1.0 - occ), 0, 1)

    wt = np.linalg.norm(quad[1] - quad[0]); wb = np.linalg.norm(quad[2] - quad[3])
    hl = np.linalg.norm(quad[3] - quad[0]); hr = np.linalg.norm(quad[2] - quad[1])
    projected = float((wt + wb) / (hl + hr))

    # The quad is foreshortened, so its ratio is not the frame's ratio. Laying
    # the poster out at the projected ratio crops the artwork for no reason.
    est, conf = true_ratio(quad, mock.shape[1], mock.shape[0], with_confidence=True)
    aperture = est if (est is not None and conf >= 0.35) else projected

    pr = poster.shape[1] / poster.shape[0]
    dev = abs(aperture - pr) / pr
    mode = fit
    if fit == "auto":
        # A mockup's job is to show the design, not to be dimensionally exact.
        # Nobody has seen the real print, so a few percent of stretch is
        # invisible, while a cropped border reads as a production error.
        # Only crop when the frame is so differently shaped that stretching
        # would visibly distort the artwork.
        mode = "stretch" if dev < 0.20 else "cover"

    ss = 2
    th = int(round(max(hl, hr))) * ss
    tw = max(1, int(round(th * aperture)))
    flat = prepare(poster, tw, th, mode)

    src = np.array([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]], np.float32)
    H = cv2.getPerspectiveTransform(src, quad)
    warped = cv2.warpPerspective(flat, H, (mock.shape[1], mock.shape[0]),
                                 flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)

    sh = shading(mock, green, qa, strength)[:, :, None]

    # Despill the plate inside the aperture: pull G down to the red/blue level
    # so that wherever alpha is soft (occluder edges, sub-pixel borders) what
    # shows through is neutral, never a green fringe.
    plate = mock.astype(np.float32)
    region = cv2.dilate((qa > 0.01).astype(np.uint8), np.ones((5, 5), np.uint8), 2) > 0
    b_, g_, r_ = plate[:, :, 0], plate[:, :, 1], plate[:, :, 2]
    cap = np.maximum(r_, b_)
    over = region & (g_ > cap)
    g_[over] = cap[over]
    plate[:, :, 1] = g_

    a = alpha[:, :, None]
    out = to_linear(plate.astype(np.uint8)) * (1 - a) + to_linear(warped) * np.clip(sh, 0, 2.0) * a
    cv2.imwrite(out_path, to_srgb(out), [cv2.IMWRITE_JPEG_QUALITY, 95])

    return dict(projected=round(projected, 3), aperture=round(float(aperture), 3),
                conf=round(float(conf), 2), poster=round(pr, 3),
                dev_pct=round(dev * 100, 1), mode=mode,
                occluded_pct=round(float(100 * (occ > 0.5).sum() / max((qa > 0.5).sum(), 1)), 2),
                out=out_path)


if __name__ == "__main__":
    a = sys.argv[1:]
    print(composite(a[0], a[1], a[2],
                    a[3] if len(a) > 3 else "auto",
                    float(a[4]) if len(a) > 4 else 1.0,
                    int(a[5]) if len(a) > 5 else 1))
