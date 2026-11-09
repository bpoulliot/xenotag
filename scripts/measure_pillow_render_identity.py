#!/usr/bin/env python3
"""Prove poster renders identical (or every difference explained) across two Pillow versions.

Roadmap item I18. A Pillow major (12.3.0 -> 13.0.0 in Dependabot #179) can move
pixels without a test noticing: the suite asserts layout and contrast, not the
bytes a poster ends up with. This instrument renders a fixed fixture set through
the poster path in whichever venv runs it, and a second command compares two
such runs.

    # once, in either venv: the fixed inputs both renders read
    python scripts/measure_pillow_render_identity.py fixtures OUT/fixtures
    # in each venv, from the same checkout
    /venv-a/bin/python scripts/measure_pillow_render_identity.py render OUT/fixtures OUT/a
    /venv-b/bin/python scripts/measure_pillow_render_identity.py render OUT/fixtures OUT/b
    python scripts/measure_pillow_render_identity.py compare OUT/a OUT/b
    # the probe's own check, in one venv (fails in BOTH directions, see below)
    python scripts/measure_pillow_render_identity.py self-test

What a render covers, per scene: every badge category (video, audio, subtitles,
rating, and the "+N" overflow pill), 100% and 65% opacity, P6's adapted palette,
three badge sizes, three corner layouts, the bundled DejaVu font and the
`ImageFont.load_default()` fallback, and the glow (`GaussianBlur`). Per scene it
writes three outputs:

  raw      `render_badge_groups()` on the decoded fixture, saved as PNG -- the
           render itself, losslessly, and the PNG encoder;
  preview  `generate_preview_bytes()` from the fixture's encoded bytes -- the
           decoder, the `LANCZOS` resize and the JPEG q88 encoder;
  apply    `apply_overlay()` on a COPY of the fixture in a scratch folder (never
           a library file) -- the backup, the decode, `_pad_to_portrait()` for a
           landscape fixture, and the JPEG q92 save.

plus the fixtures decoded (the decoder alone), the UI preview-sample generators
(`app.preview_samples`, Pillow drawing), and B1's contrast measurement --
`app.contrast.badge_contrast()` per opacity and the modal interior of every pill
`placed=` reports -- so a pixel change inside a pill is graded on both versions.

`compare` checks sha256 per output and, where bytes differ, the decoded pixels
(differing-pixel count, largest channel delta, bbox). A difference in an encoded
output whose raw render is pixel-identical is attributed to the encoder/decoder
step; a raw difference is a render difference. It exits 0 when every output is
byte-identical, 1 when anything differs (the report says what), and 3 when its
inline check of its own diff (a planted pixel, an unchanged copy) fails -- it
refuses to report then.

`self-test` renders twice in this venv (two subprocesses) and requires byte
identity, then plants a one-pixel change and a bytes-only change (a PNG
re-encoded at another compression level) into a copy and requires `compare` to
report exactly those. It exits non-zero if any of that fails.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops  # noqa: E402

# ── Scenes ──────────────────────────────────────────────────────────────────
SIZES = ("desktop", "tv", "tv_plus")
OPACITIES = (1.0, 0.65)
# (name, ImageConfig overrides): B10's three corner relations.
LAYOUTS = (
    ("apart", {"badge_position": "bottom-left", "rating_position": "top-left"}),
    ("stacked", {"badge_position": "bottom-left", "rating_position": "bottom-left"}),
    ("band", {"badge_position": "top-left", "rating_position": "top-right"}),
)
FONTS = ("dejavu", "default")
RATING = "PG-13"
TEXT_ON = "#ffffff"


def _media(kind: str):
    from app.scanner import AudioTrack, MediaInfo, SubTrack

    if kind == "full":
        # An invented title: a 4K remux with two audio languages and three subs.
        return MediaInfo(
            resolution="4K",
            languages=["EN", "JA"],
            raw_audio_langs=["eng", "jpn"],
            video_codec="H.265",
            hdr_type="HDR10",
            audio_tracks=[AudioTrack("EN", "TrueHD Atmos"), AudioTrack("JA", "DTS-HD")],
            subtitle_tracks=[SubTrack("EN", "PGS", True), SubTrack("JA", "PGS", True), SubTrack("EN", "SRT", False)],
        )
    # "many": enough languages that a small poster overflows into "+N" pills (P7).
    langs = ["EN", "JA", "FR", "DE", "ES", "IT", "KO", "ZH", "PT", "RU", "NL", "SV"]
    return MediaInfo(
        resolution="1080p",
        languages=langs[:6],
        raw_audio_langs=["eng", "jpn", "fra", "deu", "spa", "ita"],
        video_codec="AV1",
        hdr_type=None,
        audio_tracks=[AudioTrack(lang, "AAC") for lang in langs[:6]],
        subtitle_tracks=[SubTrack(lang, "SRT", True) for lang in langs],
    )


def _fixture_specs():
    """(file name, maker) for every input poster. Made ONCE, by `fixtures`."""
    from app.preview_samples import _GENERATORS

    def flat(rgb, size=(600, 900)):
        return Image.new("RGB", size, rgb)

    def gradient(size=(600, 900)):
        ramp = Image.linear_gradient("L")
        h = ramp.rotate(90, expand=True).resize(size)
        v = ramp.resize(size)
        return Image.merge("RGB", (h, ImageChops.invert(v), v))

    specs = [(f"sample-{name.rsplit('.', 1)[0]}.png", mk) for name, mk in sorted(_GENERATORS.items())]
    specs += [
        ("flat-white.png", lambda: flat((255, 255, 255))),
        ("flat-black.png", lambda: flat((0, 0, 0))),
        ("flat-grey128.png", lambda: flat((128, 128, 128))),
        ("gradient.png", gradient),
        # A JPEG input: production posters are mostly JPEG, so the decoder is in the path.
        ("gradient.jpg", gradient),
        # Landscape: apply_overlay() pads it to 2:3 (LANCZOS + GaussianBlur).
        ("landscape.jpg", lambda: gradient((960, 540))),
    ]
    return specs


def make_fixtures(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    for name, mk in _fixture_specs():
        img = mk().convert("RGB")
        if name.endswith(".jpg"):
            img.save(out / name, format="JPEG", quality=90)
        else:
            img.save(out / name, format="PNG")
    print(f"wrote {len(list(out.iterdir()))} fixtures to {out}")


# ── Render ──────────────────────────────────────────────────────────────────
def _set_font(overlay, mode: str, saved_paths: list[str]) -> None:
    overlay._FONT_PATHS[:] = saved_paths if mode == "dejavu" else []
    overlay._font_cache.clear()
    overlay.clear_pill_cache()
    overlay._GLOW_CACHE.clear()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _versions() -> dict:
    import PIL
    from PIL import features

    v = {"Pillow": PIL.__version__}
    for lib in ("jpg", "libjpeg_turbo", "zlib", "freetype2", "raqm", "harfbuzz", "fribidi"):
        try:
            v[lib] = features.version(lib)
        except Exception as exc:  # noqa: BLE001 -- a feature this Pillow does not know
            v[lib] = f"? {exc}"
    return v


def render_all(fixtures: Path, out: Path) -> None:
    from app import overlay
    from app.config import AppConfig
    from app.contrast import badge_contrast, contrast_ratio, grade
    from app.overlay import _parse_color, apply_overlay, generate_preview_bytes, render_badge_groups
    from app.pipeline import _make_badge_groups
    from app.preview_samples import _GENERATORS

    out.mkdir(parents=True, exist_ok=False)
    files = out / "files"
    files.mkdir()
    manifest: dict = {"versions": _versions(), "inputs": {}, "outputs": {}, "pills": {}, "chips": {}}

    def emit(name: str, data: bytes) -> None:
        (files / name).write_bytes(data)
        manifest["outputs"][name] = _sha(data)

    inputs = sorted(p for p in fixtures.iterdir() if p.suffix in (".png", ".jpg"))
    if not inputs:
        raise SystemExit(f"no fixtures in {fixtures}")
    for p in inputs:
        manifest["inputs"][p.name] = _sha(p.read_bytes())
        emit(f"decode--{p.stem}-{p.suffix[1:]}.png", _png(Image.open(p).convert("RGBA")))

    for name, mk in sorted(_GENERATORS.items()):
        emit(f"generator--{name.rsplit('.', 1)[0]}.png", _png(mk()))

    saved_paths = list(overlay._FONT_PATHS)
    scratch = Path(tempfile.mkdtemp(prefix="xt-i18-apply-"))
    try:
        for font in FONTS:
            _set_font(overlay, font, saved_paths)
            for opacity in OPACITIES:
                cfg = AppConfig()
                cfg.image = cfg.image.model_copy(update={"badge_opacity": opacity})
                manifest["chips"][f"{font}-{opacity}"] = _chips(badge_contrast(cfg.image))
                for size in SIZES:
                    for lname, lover in LAYOUTS:
                        for media in ("full", "many"):
                            for adapt in (False, True) if opacity < 1 else (False,):
                                c = AppConfig()
                                c.image = c.image.model_copy(
                                    update={
                                        "badge_opacity": opacity,
                                        "badge_size": size,
                                        "adapt_badge_colors": adapt,
                                        **lover,
                                    }
                                )
                                groups, rating = _make_badge_groups(_media(media), RATING, c)
                                tag = f"{font}-o{int(opacity * 100)}-{size}-{lname}-{media}" + (
                                    "-adapt" if adapt else ""
                                )
                                _render_scene(
                                    tag,
                                    inputs,
                                    c.image,
                                    groups,
                                    rating,
                                    emit,
                                    manifest,
                                    scratch,
                                    render_badge_groups,
                                    generate_preview_bytes,
                                    apply_overlay,
                                    lambda rgb: grade(contrast_ratio(_parse_color(TEXT_ON), rgb)),
                                    lambda rgb: round(contrast_ratio(_parse_color(TEXT_ON), rgb), 4),
                                )
            # The placeholder poster (no base image): Settings' preview without a sample.
            c = AppConfig()
            groups, rating = _make_badge_groups(_media("full"), RATING, c)
            emit(f"placeholder--{font}.jpg", generate_preview_bytes(groups, rating, c.image))
    finally:
        _set_font(overlay, "dejavu", saved_paths)
        shutil.rmtree(scratch)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(
        f"{manifest['versions']['Pillow']}: {len(manifest['outputs'])} outputs, {len(manifest['pills'])} pills -> {out}"
    )


def _chips(bc: dict) -> dict:
    return {
        name: {"worst": round(b["worst"], 4), "grade": b["grade"]}
        for part in ("badges", "backup")
        for name, b in ((f"{part}.{k}", v) for k, v in bc[part].items())
    }


def _render_scene(
    tag, inputs, icfg, groups, rating, emit, manifest, scratch, rbg, gpb, apply, tier, ratio
) -> None:  # noqa: PLR0913
    for p in inputs:
        fx = f"{p.stem}-{p.suffix[1:]}"
        base = Image.open(p).convert("RGBA")
        placed: list = []
        raw = rbg(base, groups, rating, icfg, placed=placed).convert("RGB")
        emit(f"raw--{tag}--{fx}.png", _png(raw))
        for i, (kind, (x, y, w, h)) in enumerate(placed):
            inset = min(5, max(1, w // 4), max(1, h // 4))
            region = raw.crop((x + inset, y + inset, x + w - inset, y + h - inset))
            colors = region.getcolors(maxcolors=max(1, region.width * region.height))
            rgb = max(colors, key=lambda c: c[0])[1]
            manifest["pills"][f"{tag}--{fx}--{i}"] = {
                "kind": kind,
                "rect": [x, y, w, h],
                "interior": list(rgb),
                "ratio": ratio(rgb),
                "tier": tier(rgb),
            }
        emit(f"preview--{tag}--{fx}.jpg", gpb(groups, rating, icfg, base_image_bytes=p.read_bytes()))
        folder = scratch / f"{tag}--{fx}"
        folder.mkdir()
        target = folder / ("poster" + p.suffix)
        shutil.copyfile(p, target)
        written = apply(folder, groups, rating, icfg)
        if written is None:
            raise RuntimeError(f"apply_overlay wrote nothing for {folder}")
        emit(f"apply--{tag}--{fx}.jpg", written.read_bytes())
        shutil.rmtree(folder)


# ── Compare ─────────────────────────────────────────────────────────────────
LAST: dict = {}  # the last compare()'s summary, for self_test()


def pixel_diff(a: Image.Image, b: Image.Image) -> dict:
    """Differing-pixel count, largest per-channel delta and bbox, over RGBA."""
    a, b = a.convert("RGBA"), b.convert("RGBA")
    if a.size != b.size:
        return {"pixels": a.width * a.height, "max": 255, "bbox": None, "size": [a.size, b.size]}
    bands = ImageChops.difference(a, b).split()
    d = bands[0]
    for band in bands[1:]:
        d = ImageChops.lighter(d, band)
    hist = d.histogram()
    return {
        "pixels": sum(hist[1:]),
        "max": max((i for i, n in enumerate(hist) if n and i), default=0),
        "bbox": d.getbbox(),
    }


def _inline_check(sample: Path) -> None:
    """The diff must find nothing in an image against itself and exactly the planted pixel."""
    img = Image.open(sample).convert("RGBA")
    same = pixel_diff(img, img.copy())
    planted = img.copy()
    px = planted.getpixel((0, 0))
    planted.putpixel((0, 0), ((px[0] + 7) % 256, *px[1:]))
    got = pixel_diff(img, planted)
    if same["pixels"] != 0 or got["pixels"] != 1 or got["max"] != 7 or got["bbox"] != (0, 0, 1, 1):
        print(f"SELF-CHECK FAILED: same={same} planted={got}", file=sys.stderr)
        raise SystemExit(3)


def compare(a: Path, b: Path) -> int:
    ma = json.loads((a / "manifest.json").read_text())
    mb = json.loads((b / "manifest.json").read_text())
    if ma["inputs"] != mb["inputs"]:
        print("REFUSED: the two renders read different fixtures", file=sys.stderr)
        return 3
    if set(ma["outputs"]) != set(mb["outputs"]):
        print(f"REFUSED: output sets differ: {sorted(set(ma['outputs']) ^ set(mb['outputs']))[:10]}", file=sys.stderr)
        return 3
    _inline_check(a / "files" / next(n for n in sorted(ma["outputs"]) if n.endswith(".png")))

    print(f"A: {ma['versions']}\nB: {mb['versions']}")
    names = sorted(ma["outputs"])
    same_bytes = [n for n in names if ma["outputs"][n] == mb["outputs"][n]]
    differ = [n for n in names if n not in same_bytes]
    raw_px: dict[str, dict] = {}
    rows = []
    for n in differ:
        d = pixel_diff(Image.open(a / "files" / n), Image.open(b / "files" / n))
        rows.append((n, d))
        if n.startswith("raw--"):
            raw_px[n.split("--", 1)[1].rsplit(".", 1)[0]] = d
    print(f"\noutputs: {len(names)}; byte-identical: {len(same_bytes)}; bytes differ: {len(differ)}")

    by_kind: dict[str, dict] = {}
    for n, d in rows:
        k = n.split("--", 1)[0]
        s = by_kind.setdefault(k, {"bytes_differ": 0, "pixels_equal": 0, "pixels_differ": 0, "px": 0, "max": 0})
        s["bytes_differ"] += 1
        if d["pixels"] == 0:
            s["pixels_equal"] += 1
        else:
            s["pixels_differ"] += 1
            s["px"] = max(s["px"], d["pixels"])
            s["max"] = max(s["max"], d["max"])
    totals = {}
    for n in names:
        totals[n.split("--", 1)[0]] = totals.get(n.split("--", 1)[0], 0) + 1
    print("\nper kind: total / bytes differ / of those pixels equal / pixels differ (worst count, worst delta)")
    for k in sorted(totals):
        s = by_kind.get(k, {"bytes_differ": 0, "pixels_equal": 0, "pixels_differ": 0, "px": 0, "max": 0})
        print(
            f"  {k:12} {totals[k]:5} / {s['bytes_differ']:5} / {s['pixels_equal']:5} / {s['pixels_differ']:5} ({s['px']}, {s['max']})"
        )

    # Encoded outputs whose raw render is pixel-identical: the difference is the codec's.
    codec_only = []
    unexplained = []
    for n, d in rows:
        k, rest = n.split("--", 1)
        if d["pixels"] == 0 or k == "raw":
            continue
        if k in ("preview", "apply"):
            key = rest.rsplit(".", 1)[0]
            if raw_px.get(key, {"pixels": 0})["pixels"] == 0:
                codec_only.append(n)
                continue
        unexplained.append((n, d))
    raw_rows = [(n, d) for n, d in rows if n.startswith("raw--") and d["pixels"]]
    other_rows = [(n, d) for n, d in rows if not n.startswith(("raw--", "preview--", "apply--")) and d["pixels"]]
    print(
        f"\nencoded outputs that differ in pixels while their raw render is identical (codec step): {len(codec_only)}"
    )
    print(f"raw renders that differ in pixels: {len(raw_rows)}")
    for n, d in raw_rows[:40]:
        print(f"  {n}: {d['pixels']} px, max delta {d['max']}, bbox {d['bbox']}")
    print(f"decoded fixtures / generators / placeholder that differ in pixels: {len(other_rows)}")
    for n, d in other_rows[:40]:
        print(f"  {n}: {d['pixels']} px, max delta {d['max']}, bbox {d['bbox']}")

    # B1: the contrast tier of every pill and every chip, on both versions.
    tier_moves = [k for k in ma["pills"] if ma["pills"][k]["tier"] != mb["pills"].get(k, {}).get("tier")]
    interior_moves = [k for k in ma["pills"] if ma["pills"][k]["interior"] != mb["pills"].get(k, {}).get("interior")]
    rect_moves = [k for k in ma["pills"] if ma["pills"][k]["rect"] != mb["pills"].get(k, {}).get("rect")]
    chip_moves = [
        (cfg, n, ma["chips"][cfg][n], mb["chips"][cfg][n])
        for cfg in ma["chips"]
        for n in ma["chips"][cfg]
        if ma["chips"][cfg][n] != mb["chips"][cfg][n]
    ]
    print(f"\npills measured: {len(ma['pills'])} (B: {len(mb['pills'])})")
    print(
        f"  rect moved: {len(rect_moves)}; interior colour moved: {len(interior_moves)}; contrast tier moved: {len(tier_moves)}"
    )
    for k in tier_moves[:20]:
        print(
            f"    {k}: {ma['pills'][k]['ratio']} {ma['pills'][k]['tier']} -> {mb['pills'][k]['ratio']} {mb['pills'][k]['tier']}"
        )
    print(f"chips (badge_contrast): {sum(len(v) for v in ma['chips'].values())}; changed: {len(chip_moves)}")
    for row in chip_moves[:20]:
        print(f"    {row}")
    LAST.update(
        differ=len(differ),
        pixels_equal=sum(1 for _, d in rows if d["pixels"] == 0),
        raw_pixel_rows=[n for n, _ in raw_rows],
        other_pixel_rows=[n for n, _ in other_rows],
        codec_only=len(codec_only),
        tier_moves=len(tier_moves),
    )
    if not differ:
        print("\nVERDICT: byte-identical")
        return 0
    if not raw_rows and not other_rows and not unexplained:
        print("\nVERDICT: renders pixel-identical; only encoded bytes/pixels differ (codec step)")
    else:
        print(
            f"\nVERDICT: pixel differences in {len(raw_rows) + len(other_rows)} render/decode outputs -- explain them"
        )
    return 1


# ── Self-test ───────────────────────────────────────────────────────────────
def self_test() -> int:
    me = Path(__file__).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="xt-i18-selftest-"))
    ok = True
    try:
        make_fixtures(tmp / "fixtures")
        for run in ("r1", "r2"):
            subprocess.run([sys.executable, str(me), "render", str(tmp / "fixtures"), str(tmp / run)], check=True)
        rc = compare(tmp / "r1", tmp / "r2")
        print(f"\n[self-test] same venv twice: compare exit {rc} (want 0)")
        ok &= rc == 0

        # Things that must differ do differ: otherwise a variable the render claims
        # to cover never reached it, and "identical" would be vacuous.
        r1 = json.loads((tmp / "r1" / "manifest.json").read_text())["outputs"]
        scene = "o65-tv-apart-full"
        must_differ = {
            "font path (dejavu vs load_default)": (
                f"raw--dejavu-{scene}--flat-grey128-png.png",
                f"raw--default-{scene}--flat-grey128-png.png",
            ),
            "opacity (100 vs 65)": (
                "raw--dejavu-o100-tv-apart-full--flat-grey128-png.png",
                f"raw--dejavu-{scene}--flat-grey128-png.png",
            ),
            "badge size (desktop vs tv_plus)": (
                "raw--dejavu-o100-desktop-apart-full--gradient-png.png",
                "raw--dejavu-o100-tv_plus-apart-full--gradient-png.png",
            ),
            "layout (apart vs band)": (
                "raw--dejavu-o100-tv-apart-full--gradient-png.png",
                "raw--dejavu-o100-tv-band-full--gradient-png.png",
            ),
            "P6 adapt (off vs on, white poster)": (
                f"raw--dejavu-{scene}--flat-white-png.png",
                f"raw--dejavu-{scene}-adapt--flat-white-png.png",
            ),
            "media (full vs many)": (
                "raw--dejavu-o100-tv-apart-full--gradient-png.png",
                "raw--dejavu-o100-tv-apart-many--gradient-png.png",
            ),
            "fixture (white vs black)": (
                f"raw--dejavu-{scene}--flat-white-png.png",
                f"raw--dejavu-{scene}--flat-black-png.png",
            ),
            "landscape padded (apply is 2:3)": (None, "apply--dejavu-o100-tv-apart-full--landscape-jpg.jpg"),
        }
        for what, (x1, x2) in must_differ.items():
            if x1 is None:
                w, h = Image.open(tmp / "r1" / "files" / x2).size
                good = abs(w / h - 2 / 3) < 0.01
                print(f"[self-test] {what}: {w}x{h} ({'ok' if good else 'NOT 2:3'})")
            else:
                good = r1[x1] != r1[x2]
                print(
                    f"[self-test] {what}: {'differ' if good else 'IDENTICAL -- the variable never reached the render'}"
                )
            ok &= good

        shutil.copytree(tmp / "r1", tmp / "planted")
        m = json.loads((tmp / "planted" / "manifest.json").read_text())
        raw = next(n for n in sorted(m["outputs"]) if n.startswith("raw--"))
        png = next(n for n in sorted(m["outputs"]) if n.startswith("generator--"))
        img = Image.open(tmp / "planted" / "files" / raw).convert("RGB")
        x, y = img.width // 2, img.height // 2
        px = img.getpixel((x, y))
        img.putpixel((x, y), ((px[0] + 1) % 256, px[1], px[2]))
        data = _png(img)
        (tmp / "planted" / "files" / raw).write_bytes(data)
        m["outputs"][raw] = _sha(data)
        g = Image.open(tmp / "planted" / "files" / png)
        g.load()
        buf = io.BytesIO()
        g.save(buf, format="PNG", compress_level=1)
        (tmp / "planted" / "files" / png).write_bytes(buf.getvalue())
        m["outputs"][png] = _sha(buf.getvalue())
        (tmp / "planted" / "manifest.json").write_text(json.dumps(m))

        d_raw = pixel_diff(Image.open(tmp / "r1" / "files" / raw), Image.open(tmp / "planted" / "files" / raw))
        d_png = pixel_diff(Image.open(tmp / "r1" / "files" / png), Image.open(tmp / "planted" / "files" / png))
        bytes_differ = m["outputs"][png] != json.loads((tmp / "r1" / "manifest.json").read_text())["outputs"][png]
        rc = compare(tmp / "r1", tmp / "planted")
        print(f"\n[self-test] planted 1 px in {raw}: {d_raw} (want 1 px, delta 1)")
        print(f"[self-test] re-encoded {png}: bytes differ={bytes_differ}, pixels {d_png['pixels']} (want True, 0)")
        print(f"[self-test] compare exit {rc} (want 1)")
        ok &= d_raw["pixels"] == 1 and d_raw["max"] == 1 and d_raw["bbox"] == (x, y, x + 1, y + 1)
        ok &= bytes_differ and d_png["pixels"] == 0 and rc == 1
        print(f"[self-test] compare summary {LAST} (want differ=2, pixels_equal=1, raw_pixel_rows=[{raw}])")
        ok &= LAST["differ"] == 2 and LAST["pixels_equal"] == 1 and LAST["raw_pixel_rows"] == [raw]
        ok &= not LAST["other_pixel_rows"]
    finally:
        shutil.rmtree(tmp)
    print(f"\nSELF-TEST {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fixtures")
    f.add_argument("out", type=Path)
    r = sub.add_parser("render")
    r.add_argument("fixtures", type=Path)
    r.add_argument("out", type=Path)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    sub.add_parser("self-test")
    args = ap.parse_args()
    if args.cmd == "fixtures":
        make_fixtures(args.out)
        return 0
    if args.cmd == "render":
        render_all(args.fixtures, args.out)
        return 0
    if args.cmd == "compare":
        return compare(args.a, args.b)
    return self_test()


if __name__ == "__main__":
    sys.exit(main())
