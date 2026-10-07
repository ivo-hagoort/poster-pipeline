#!/usr/bin/env python3
"""
Poster pipeline zonder Make.

Genereert een poster, schaalt hem op, normaliseert de rand naar 2:3 en ISO,
en zet hem in alle greenscreen-mockups. Draait in een GitHub Action; sleutels
komen uit de omgeving, nooit uit een bestand in de repo.

  python3 scripts/pipeline.py --city Vught --type vintagewatercolor --out run/

Omgevingsvariabelen:
  OPENAI_API_KEY        poster genereren
  REPLICATE_API_TOKEN   upscalen

Afsluitcode 0 = klaar. Elke andere waarde = gestopt met uitleg op stderr.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def log(msg):
    print(msg, flush=True)


def post_json(url, payload, headers, timeout=300):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(url, headers, timeout=120):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def download(url, dest, timeout=600):
    req = urllib.request.Request(url, headers={"User-Agent": "poster-pipeline"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return dest


# ---------- 1. genereren ----------

def generate(city, spec, dest):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY ontbreekt")
    prompt = (f"{city}. {spec['gen_prompt']} "
              f"The large bold destination title at the top reads {city}.")
    log(f"[1/5] genereren: {city} ({len(prompt)} tekens prompt)")
    t = time.time()
    res = post_json(
        "https://api.openai.com/v1/images/generations",
        {"model": "gpt-image-2", "prompt": prompt, "n": 1, "size": "1024x1536",
         "quality": "high", "moderation": "low", "output_format": "png"},
        {"Authorization": f"Bearer {key}"}, timeout=900)
    b64 = res["data"][0].get("b64_json")
    if not b64:
        url = res["data"][0].get("url")
        if not url:
            sys.exit(f"geen afbeelding terug: {json.dumps(res)[:400]}")
        download(url, dest)
    else:
        dest.write_bytes(base64.b64decode(b64))
    log(f"      klaar in {time.time() - t:.0f}s, {dest.stat().st_size / 1e6:.1f} MB")
    return dest


# ---------- 2. upscalen ----------

def upscale(src, dest, factor=4):
    token = os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        sys.exit("REPLICATE_API_TOKEN ontbreekt")
    head = {"Authorization": f"Bearer {token}"}
    data_uri = "data:image/png;base64," + base64.b64encode(src.read_bytes()).decode()
    log(f"[2/5] upscalen x{factor} via Replicate")
    t = time.time()
    # Prefer: wait laat Replicate wachten tot het klaar is; valt dit weg, dan pollen
    res = post_json("https://api.replicate.com/v1/models/nightmareai/real-esrgan/predictions",
                    {"input": {"image": data_uri, "scale": factor, "face_enhance": False}},
                    {**head, "Prefer": "wait=60"}, timeout=900)
    out, poll = res.get("output"), res.get("urls", {}).get("get")
    waited = 0
    while not out and poll and waited < 900:
        if res.get("status") in ("failed", "canceled"):
            sys.exit(f"upscale mislukt: {res.get('error')}")
        time.sleep(10); waited += 10
        res = get_json(poll, head)
        out = res.get("output")
    if not out:
        sys.exit("upscale leverde geen resultaat binnen 15 minuten")
    if isinstance(out, list):
        out = out[0]
    download(out, dest)
    log(f"      klaar in {time.time() - t:.0f}s, {dest.stat().st_size / 1e6:.1f} MB")
    return dest


# ---------- 3. naar jpg ----------

def to_jpeg(src, dest, quality=97):
    """Vervangt de images.weserv.nl omweg die Make nodig had."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(src).convert("RGB")
    im.save(dest, "JPEG", quality=quality, subsampling=0)
    log(f"[3/5] jpg q{quality}: {im.size[0]}x{im.size[1]}, {dest.stat().st_size / 1e6:.1f} MB")
    return dest


# ---------- 4. verhoudingen ----------

def build_ratios(src, out_dir, slug):
    results = {}
    jobs = [("2x3", [sys.executable, "-I", str(HERE / "normalize_border.py"),
                     str(src), str(out_dir / f"{slug}-hires.jpg")]),
            ("iso", [sys.executable, "-I", str(HERE / "normalize_ratio.py"),
                     str(src), str(out_dir / f"{slug}-hires-iso.jpg"), "--ratio", "iso"])]
    log("[4/5] verhoudingen opbouwen")
    for name, cmd in jobs:
        p = subprocess.run(cmd, capture_output=True, text=True)
        tail = (p.stdout + p.stderr).strip().splitlines()
        log(f"      {name}: {'ok' if p.returncode == 0 else 'OVERGESLAGEN'} "
            f"- {tail[-1] if tail else ''}")
        if p.returncode == 0:
            results[name] = Path(cmd[-3] if name == "iso" else cmd[-1])
    if "2x3" not in results:
        sys.exit("de 2:3 master kon niet gebouwd worden; randdetectie faalde")
    return results


# ---------- 5. mockups ----------

def build_mockups(poster, out_dir, slug):
    sys.path.insert(0, str(HERE))
    from compose_mockups import composite
    mockups = sorted((ROOT / "mockups").glob("*.webp"),
                     key=lambda p: int("".join(c for c in p.stem if c.isdigit()) or 0))
    if not mockups:
        log("[5/5] geen mockups gevonden, overgeslagen")
        return []
    log(f"[5/5] {len(mockups)} mockups samenstellen")
    made = []
    for i, m in enumerate(mockups, 1):
        dest = out_dir / f"{slug}-mockup-{i}.jpg"
        r = composite(str(m), str(poster), str(dest), "auto")
        made.append(dest)
        log(f"      {i:>2}: {r['mode']:<8} afwijking {r['dev_pct']:>4}%  {dest.name}")
    return made


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--type", required=True, dest="ptype")
    ap.add_argument("--out", default="run")
    ap.add_argument("--skip-generate", metavar="PNG",
                    help="hergebruik een bestaande bron in plaats van opnieuw genereren")
    a = ap.parse_args()

    types = json.loads((ROOT / "poster_types.json").read_text())
    if a.ptype not in types:
        sys.exit(f"onbekend type '{a.ptype}'. Beschikbaar: {', '.join(types)}")
    spec = types[a.ptype]

    slug = f"Travel-{a.city.replace(' ', '')}-{a.ptype}"
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    work = out / "_werk"; work.mkdir(exist_ok=True)
    start = time.time()

    src = Path(a.skip_generate) if a.skip_generate else generate(a.city, spec, work / "source.png")
    up = upscale(src, work / "upscaled.png", spec.get("upscale_factor", 4))
    jpg = to_jpeg(up, work / "upscaled.jpg")
    ratios = build_ratios(jpg, out, slug)
    mockups = build_mockups(ratios["2x3"], out, slug)

    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    report = {"city": a.city, "poster_type": a.ptype, "slug": slug,
              "seconds": round(time.time() - start),
              "title": spec["title_template"].replace("{city}", a.city),
              "price": spec["price"], "masters": {}, "mockups": len(mockups)}
    for name, p in ratios.items():
        im = Image.open(p)
        report["masters"][name] = {"file": p.name, "px": f"{im.size[0]}x{im.size[1]}",
                                   "ratio": round(im.size[0] / im.size[1], 4),
                                   "mb": round(p.stat().st_size / 1e6, 1)}
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    log("\n" + json.dumps(report, indent=1, ensure_ascii=False))
    log(f"\nKLAAR in {report['seconds']}s")


if __name__ == "__main__":
    main()
