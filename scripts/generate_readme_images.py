#!/usr/bin/env python3
"""Generate the images the README shows, under assets/readme/.

Roadmap item P5. Two halves, because they need different things:

1. **Overlay examples** (the default) -- plain Python, no browser. Each is the
   real render path: badge groups built by `pipeline._make_badge_groups()` from
   an invented `MediaInfo`, drawn by `overlay.generate_preview_bytes()` over one
   of the synthetic backgrounds in `app/preview_samples.py`. Never a real
   poster: this repo is public and posters are copyrighted art.

2. **UI screenshots** (`--screenshots URL`) -- needs Playwright and Chromium,
   which CI does not have, so it is a manual mode. Point it at a THROWAWAY
   instance on a scratch config with placeholder service URLs, never at a real
   deployment: whatever the dashboard shows ends up in a public README. One way,
   with nothing reachable from the scratch network but the two containers:

       docker build -t xenotag:readme .
       docker network create --internal xt-readme
       docker run -d --rm --name xt-readme-app --network xt-readme \\
           -e SECURE_COOKIES=false -e XENOTAG_USERNAME=admin \\
           -e XENOTAG_PASSWORD=readme-screens-only \\
           -v "$PWD/scratch-config:/config" xenotag:readme
       docker run --rm --network xt-readme -v "$PWD:/work" -w /work \\
           -e XENOTAG_USERNAME=admin -e XENOTAG_PASSWORD=readme-screens-only \\
           mcr.microsoft.com/playwright/python:<tag> \\
           sh -c "pip install -q pillow && \\
                  python3 scripts/generate_readme_images.py --screenshots http://xt-readme-app:7755"

   The playwright image must match the pip `playwright` version it ships; the
   scratch config needs only placeholder `jellyfin.url` (e.g.
   `http://jellyfin.example:8096`) and must not name a real host.

`--check` re-renders the overlay examples and compares bytes, so CI fails when
the palette or layout moves and the README images were not regenerated. That is
only sound because the render is deterministic: Pillow's wheels bundle their own
FreeType and libjpeg, and the font is DejaVu Sans Bold, unchanged since 2016.
Measured when this was written: Pillow 12.1.1 on an Ubuntu host and 12.3.0 in the
python:3.12-slim image gave byte-identical files. Without DejaVu the overlay falls
back to Pillow's default font and the check fails -- which is correct, the
README would then be showing a different render from the one the app ships.

    python3 scripts/generate_readme_images.py                      # overlay examples
    python3 scripts/generate_readme_images.py --check              # verify, write nothing
    python3 scripts/generate_readme_images.py --screenshots URL    # UI screenshots
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import AppConfig  # noqa: E402
from app.overlay import generate_preview_bytes  # noqa: E402
from app.pipeline import _make_badge_groups  # noqa: E402
from app.preview_samples import _GENERATORS  # noqa: E402
from app.scanner import AudioTrack, MediaInfo, SubTrack  # noqa: E402

OUT = ROOT / "assets" / "readme"

# An invented title's worth of streams -- a typical 4K remux with a second
# audio language, so every badge category shows up.
_MEDIA = MediaInfo(
    resolution="4K",
    languages=["EN", "JA"],
    raw_audio_langs=["eng", "jpn"],
    video_codec="H.265",
    hdr_type="HDR10",
    audio_tracks=[AudioTrack("EN", "TrueHD Atmos"), AudioTrack("JA", "DTS-HD")],
    subtitle_tracks=[SubTrack("EN", "PGS", True), SubTrack("JA", "PGS", True), SubTrack("EN", "SRT", False)],
)
_RATING = "PG-13"

# (output file, background sample, ImageConfig overrides). Everything not
# overridden is the shipped default, so these track the defaults when they move.
_OVERLAYS: list[tuple[str, str, dict]] = [
    ("overlay-desktop.jpg", "sample_teal.jpg", {"badge_size": "desktop"}),
    ("overlay-tv.jpg", "sample_teal.jpg", {"badge_size": "tv"}),
    ("overlay-tv-plus.jpg", "sample_teal.jpg", {"badge_size": "tv_plus"}),
    # B10: rating and tags sharing a corner stack, rating nearest the corner.
    (
        "overlay-stacked.jpg",
        "sample_vibrant.jpg",
        {"badge_position": "bottom-left", "rating_position": "bottom-left"},
    ),
]

# (output file, page name passed to the UI's switchPage()).
_SCREENS: list[tuple[str, str]] = [
    ("ui-dashboard.png", "dashboard"),
    ("ui-preview.png", "preview"),
    ("ui-settings.png", "settings"),
]
_VIEWPORT = {"width": 1280, "height": 900}


def render_overlays() -> dict[str, bytes]:
    rendered = {}
    for name, sample, overrides in _OVERLAYS:
        cfg = AppConfig()
        cfg.image = cfg.image.model_copy(update=overrides)
        groups, rating_group = _make_badge_groups(_MEDIA, _RATING, cfg)
        buf = io.BytesIO()
        _GENERATORS[sample]().save(buf, format="PNG")  # lossless hand-off
        rendered[name] = generate_preview_bytes(groups, rating_group, cfg.image, base_image_bytes=buf.getvalue())
    return rendered


def write_overlays() -> list[Path]:
    written = []
    for name, data in render_overlays().items():
        path = OUT / name
        path.write_bytes(data)
        written.append(path)
    return written


def check_overlays() -> int:
    stale = [
        name
        for name, data in render_overlays().items()
        if not (OUT / name).is_file() or (OUT / name).read_bytes() != data
    ]
    for name in stale:
        print(f"STALE assets/readme/{name}", file=sys.stderr)
    if stale:
        print("Regenerate with: python3 scripts/generate_readme_images.py", file=sys.stderr)
        return 1
    print(f"OK: {len(_OVERLAYS)} overlay examples match the current render")
    return 0


def _optimise_png(path: Path) -> None:
    # UI chrome is flat colour: a 256-colour palette is visually lossless here
    # and roughly a third of the size of the RGB screenshot.
    img = Image.open(path).convert("RGB")
    img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)


def take_screenshots(url: str) -> list[Path]:
    from playwright.sync_api import sync_playwright  # manual mode only

    user = os.environ.get("XENOTAG_USERNAME", "admin")
    password = os.environ["XENOTAG_PASSWORD"]
    base = url.rstrip("/")
    written = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport=_VIEWPORT)
            page.goto(f"{base}/login")
            page.fill("input[name=username]", user)
            page.fill("input[name=password]", password)
            page.click("button[type=submit]")
            page.wait_for_url(f"{base}/")
            for name, tab in _SCREENS:
                page.evaluate(f"switchPage({tab!r})")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)  # preview images and health probes settle
                path = OUT / name
                page.screenshot(path=str(path), full_page=False)
                _optimise_png(path)
                written.append(path)
        finally:
            browser.close()
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--screenshots", metavar="URL", help="take UI screenshots from a throwaway instance at URL")
    ap.add_argument("--check", action="store_true", help="verify the overlay examples are current; write nothing")
    args = ap.parse_args()
    if args.check:
        return check_overlays()
    OUT.mkdir(parents=True, exist_ok=True)
    written = take_screenshots(args.screenshots) if args.screenshots else write_overlays()
    for path in written:
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
