#!/usr/bin/env python3
"""
Recover the TRUE width/height ratio of a rectangle from its perspective image.

The quad you measure in the picture is foreshortened, so its ratio is not the
frame's ratio. Assuming square pixels and the principal point at the image
centre, the real ratio follows in closed form (Zhang & He, whiteboard
rectification). Returns None when the view is too close to parallel for the
focal length to be recoverable, in which case the projected ratio is the best
estimate available.
"""
import numpy as np


MIN_PERSPECTIVE = 0.015   # below this the quad is effectively a parallelogram


def true_ratio(quad, img_w, img_h, with_confidence=False):
    def result(r, conf):
        return (r, conf) if with_confidence else r

    m = [np.array([p[0], p[1], 1.0], float) for p in quad]   # TL, TR, BR, BL
    m1, m2, m3, m4 = m[0], m[1], m[3], m[2]                  # paper's ordering

    d2 = np.dot(np.cross(m1, m4), m3)
    d3 = np.dot(np.cross(m2, m4), m3)
    e2 = np.dot(np.cross(m1, m4), m2)
    e3 = np.dot(np.cross(m3, m4), m2)
    if abs(d3) < 1e-12 or abs(e3) < 1e-12:
        return result(None, 0.0)
    k2, k3 = d2 / d3, e2 / e3

    # k2 = k3 = 1 means the edges are parallel in the image: an affine view that
    # carries no focal-length information, so the recovered ratio is noise.
    persp = max(abs(k2 - 1.0), abs(k3 - 1.0))
    if persp < MIN_PERSPECTIVE:
        return result(None, 0.0)
    conf = float(min(1.0, (persp - MIN_PERSPECTIVE) / 0.06))

    n2 = k2 * m2 - m1
    n3 = k3 * m3 - m1
    u0, v0 = img_w / 2.0, img_h / 2.0

    den = n2[2] * n3[2]
    if abs(den) < 1e-12:
        return result(None, 0.0)
    f2 = -(1.0 / den) * (
        (n2[0] - n2[2] * u0) * (n3[0] - n3[2] * u0)
        + (n2[1] - n2[2] * v0) * (n3[1] - n3[2] * v0)
    )
    if not np.isfinite(f2) or f2 <= 0:
        return result(None, 0.0)
    f = float(np.sqrt(f2))

    A = np.array([[f, 0, u0], [0, f, v0], [0, 0, 1]], float)
    Ai = np.linalg.inv(A)
    a = Ai @ n2
    b = Ai @ n3
    nb = float(np.linalg.norm(b))
    if nb < 1e-12:
        return result(None, 0.0)
    r = float(np.linalg.norm(a) / nb)
    if not (np.isfinite(r) and 0.3 < r < 2.0):
        return result(None, 0.0)
    return result(r, conf)
