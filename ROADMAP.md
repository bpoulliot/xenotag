# Xenotag Roadmap

Items are scored **Value** (1–5: user/correctness impact) and **Complexity** (1–5: implementation effort).
Easy wins = high value, low complexity. Categories: **B** = bug/correctness, **U** = user-facing
behavior, **I** = infrastructure, **P** = polish/UX.

**Readiness** (adopted 2026-09-22, matching van1sh/abtidy/sightline): every item is
**READY** (spec is complete, can be built as written), **NEEDS DECISION** (a human choice is
open — never start one), or **NEEDS MEASUREMENT** (a number has to be taken first; measure,
record, re-label — do not implement in the same pass). Unlabelled items predate this and are
unassessed.

**Tier 0 comes first.** Correctness defects outrank features regardless of Value score.

---

## Tier 0 — Correctness

| ID | Defect | Value | Complexity | Readiness | Issue |
|----|--------|:-----:|:----------:|-----------|-------|
| B1 | **Badge contrast is roughly half what the config claims.** `ImageConfig` annotates each badge colour "verified WCAG AAA ≥7:1 against white text" — true of the opaque hex, but not of what renders. | 5 | 2 | **FIXED 2026-09-22** | — |
| B2 | **A configured badge colour is never checked for contrast.** Any hex the settings UI or `config.yml` supplies is used as-is; the live deployment's palette renders at 2.6:1. | 4 | 2 | READY | — |
| B3 | **`_PILL_CACHE`'s key omits the padding.** Two poster widths can agree on `font_size` and disagree on `pad_h`/`pad_v`, so the first one rendered supplies the tile for both. | 2 | 1 | READY | — |

**B1 — FIXED 2026-09-22.** Measured, fixed and re-measured in one session. The measurement
below was reproduced from scratch first and **agreed with the original to the decimal**, so the
pre-fix table stands as recorded.

Method: render real `_pill_tile()` output over flat backdrops, then take the **modal pixel of
the pill interior, inset 5px** from the pill rectangle. The inset is the trap — `_GLOW_MARGIN`
is 14, so x=14 is the pill's antialiased left *edge*: sampling there reports 4.4:1 on white for
a badge that renders 3.7:1, and 7.2:1 on black for one that renders 4.6:1.

Now a committed, re-runnable probe: **`scripts/measure_badge_contrast.py`** (`--self-test`,
`--config FILE`, `--opacity`, `--show-edge`). The self-test fails in both directions — it
checks that an opaque render *equals* the hex ratio and that a translucent one does *not*.

### Before → after, shipped defaults, white text

| badge | opaque hex | on black | on white | on grey |
|---|---:|---:|---:|---:|
| video `#134e4a` | 9.5:1 | 4.6 → **9.5** | **3.7** → **9.5** | 4.1 → **9.5** |
| audio `#1e3a8a` | 10.4:1 | 4.9 → **10.4** | 3.9 → **10.4** | 4.3 → **10.4** |
| sub `#7c2d12` | 9.4:1 | 4.7 → **9.4** | 3.8 → **9.4** | 4.2 → **9.4** |
| rating `#4c1d95` | 11.0:1 | 5.3 → **11.0** | 4.2 → **11.0** | 4.7 → **11.0** |

Before: every badge failed AAA (7:1) and nine of twelve failed AA (4.5:1). After: **all twelve
clear AAA**, and the rendered figure now equals the opaque-hex figure exactly — which is the
point, because the hex figure is what the config comment quotes.

### The two causes, and which lever fixed which

 1. `badge_opacity: 0.65` → `alpha=165`, so the poster showed through the fill while the
    comment's figures assumed `alpha=255`.
 2. **The white glow was the dominant term.** `_pill_tile()` drew `fill=(255,255,255,210)`
    *behind* the pill, then composited the translucent pill over it — so the surface under the
    fill was near-white whatever the poster was, which is why the numbers barely moved between
    a black and a white backdrop.

Both were needed, and measuring them separately is what showed why:

| lever | black | white | grey |
|---|---:|---:|---:|
| before (glow behind fill, opacity 0.65) | 4.6 | 3.7 | 4.1 |
| glow punched out, opacity still 0.65 | 13.9 | **3.7** | 7.0 |
| glow punched out + opacity 1.0 | **9.5** | **9.5** | **9.5** |

Punching the glow out alone does **nothing** on a white poster — obvious in hindsight, since
removing white from in front of white changes nothing — and it makes the result swing wildly
with the poster (13.9 on black, 3.7 on white). Raising the opacity is what makes the badge
independent of the poster at all. The punch-out is still worth having: it is what stops the
glow washing the fill at *any* opacity below 1.0 the operator picks.

**Fix shipped:** `_pill_tile()` clears the glow from under the pill footprint (deflated 1px, so
the pill's own antialiased edge still lands on glow), and `badge_opacity` defaults to `1.0`.
The glow survives as what it was meant to be — a halo *around* the pill.

`badge_opacity` is still a knob, and it is still a contrast control. Measured floors for the
shipped palette: **0.89 for AAA, 0.73 for AA**; the old 0.65 default rendered 3.7:1.

### Consequences worth knowing

 - **Nothing re-renders on its own.** `apply_overlay()` always composites from the `.orig`
   backup, never from the current poster, so a re-render is idempotent and badges never stack.
 - **An existing deployment does not change until its config does.** `save_config_from_dict()`
   dumps the whole model, so any operator who has ever saved settings has `badge_opacity: 0.65`
   written in `config.yml` and keeps it. The new default only reaches them if they reset it.
 - `_PILL_CACHE`'s key is untouched — the fix adds no input outside `(text, fill_hex,
   text_hex, alpha, font_size)`.

Guarded by `tests/test_badge_contrast.py`, which asserts on **rendered pixels**: asserting on
the hex constants is exactly the check that would have passed throughout this defect's life.
Both halves of the fix were verified to fail the suite when reverted — opacity back to 0.65
fails 10 cases, restoring the glow behind the pill fails 4.

**P6 is now answerable.** The rendered ratio is finally a function of the configured colour, so
a background-aware palette can be evaluated on its own merits.

**B2 — filed 2026-09-22, found while fixing B1. Not fixed; evidence only.**

The four colours in `ImageConfig` are defaults. The settings UI and `config.yml` accept any hex
and nothing checks it. The live deployment at `~/docker/xenotag/config/config.yml` has replaced
all four, and measured with the same probe (`--config`) at its own `badge_opacity: 0.65`:

| badge | configured | opaque hex | on black | on white | on grey |
|---|---|---:|---:|---:|---:|
| video | `#1a7a6e` | 5.2:1 | 3.3:1 | 2.7:1 | 3.0:1 |
| audio | `#6b3a9e` | 7.7:1 | 4.1:1 | 3.3:1 | 3.7:1 |
| sub | `#a86200` | 4.8:1 | 3.1:1 | 2.6:1 | 2.8:1 |
| rating | `#2d2d2d` | 13.8:1 | 5.7:1 | 4.5:1 | 5.0:1 |

B1's fix does not rescue this palette. Made fully opaque it renders 5.2 / 7.7 / 4.8 / 13.8 —
all four clear AA, but **two of the four still fail AAA**, because those two hexes never had
the headroom. The sub badge's amber `#a86200` is 4.8:1 against white text at its very best.
At the deployment's own `badge_opacity: 0.65`, all four fail AA.

The work is a contrast check on the colour inputs — the ratio is ~15 lines and already exists
in the probe. Open question for the operator, which is why this is B2 and not part of B1:
**warn or refuse?** A hard refusal rejects a palette someone deliberately chose; a warning next
to the colour picker (and next to the opacity slider, whose range still reaches 10%) informs
without overriding. The UI already renders a live preview, so the number has somewhere to go.

Reproduce either table: `python3 scripts/measure_badge_contrast.py [--config FILE]`.

**B3 — filed 2026-09-22, found while fixing B1. Not fixed; evidence only.**

`_PILL_CACHE` is keyed `(text, fill_hex, text_hex, alpha, font_size)`, but `_pill_tile()` also
takes `pad_h` and `pad_v`, and those change the tile. `_compute_layout_params()` derives all
three from the poster width by separate roundings, so they can disagree: sweeping widths
200–4000px at the default `badge_size` finds **121 collisions**, the first being **494px and
501px — both `font_size` 36, `pad_v` 2 vs 3**. Same key, different correct tile; whichever
poster is processed first supplies the tile for every later one in that process.

Consequence is small but real: the row layout in `_render_group()` computes `pill_h` from *its*
`pad_v`, so a mis-served tile is a ~2px vertical mismatch between where the row expects the pill
and how tall the pill actually is. Nothing is unreadable; it is wrong, cheap to fix, and it is
the same class of defect P6 is warned about — adding padding to the key is a one-line change.

Recorded executably as a `strict=True` xfail in `tests/test_badge_contrast.py`
(`test_cache_key_covers_padding`); remove the marker when it is fixed.

---

## In Progress

| ID | Feature | Issue |
|----|---------|-------|
| P1 | Audio language override (fix UND tracks via ffmpeg metadata) | [#21](https://github.com/bpoulliot/xenotag/issues/21) |
| P2 | Media browser: name column, codec columns | [#10](https://github.com/bpoulliot/xenotag/issues/10) |
| P3 | Scan history in web UI | [#9](https://github.com/bpoulliot/xenotag/issues/9) |

---

## Near-term

### U — User-facing

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| U1 | Tag migration: clean up legacy `mf-*` tags on upgrade from Metafin; `tags.legacy_prefixes` config option | 5 | 2 | — | [#35](https://github.com/bpoulliot/xenotag/issues/35) |
| U2 | Tag lifecycle: remove stale `xt-*` tags when items are deleted from Jellyfin; handle mtime-preserving re-encodes | 5 | 3 | — | [#36](https://github.com/bpoulliot/xenotag/issues/36) |
| U3 | Webhook / event-driven processing: per-item rescan on Sonarr/Radarr/Jellyfin Download events | 5 | 2 | — | [#22](https://github.com/bpoulliot/xenotag/issues/22) |
| U4 | Subtitle language tagging: write `xt-sub-*` tags to Jellyfin/Sonarr/Radarr (ffprobe extraction already exists) | 4 | 2 | — | [#11](https://github.com/bpoulliot/xenotag/issues/11) |
| U7 | **Ratings ingest** — pull the rating from Jellyfin/\*arr and emit it as a tag + badge. Split out of U6 (see note). | 4 | 2 | NEEDS DECISION | — |
| U8 | **Tag taxonomy pass** — audit the `xt-*` set actually emitted against the library and collapse what is redundant or never queried. | 4 | 3 | NEEDS MEASUREMENT | — |
| U9 | **Tag queries** — filter/search the media browser by tag (`xt-*` and legacy), combinable, from the web UI. | 4 | 3 | NEEDS DECISION | — |

**U7 note — why this is split out of U6.** U6 ("Extended metadata tags") is Complexity 5 because
it bundles seven unrelated sources: genres, original language, runtime bands, series status,
ratings, custom formats. Ratings alone is ~2: the value is already on the Jellyfin item payload
and `rating_badge_color` / `show_rating_badge` / the `rating` destination and `rating_group`
render path **all already exist** in `ImageConfig` and `render_badge_groups()`. The remaining
question is which rating, and that is the decision blocking it:

 - Jellyfin `CommunityRating` (TMDb/IMDb-derived, 0–10), `CriticRating` (RT, 0–100), or the
   user's own `UserData.Rating`?
 - \*arr carries its own ratings too, and they disagree with Jellyfin's.
 - One badge or one per source? The overlay is already dense (see P7).

Decide the source and U7 becomes READY. **Leave the rest of U6 alone** — the other six are a
genuinely separate, genuinely complexity-5 piece of work.

**U8 note.** "Simplify the tags" needs to start from what is actually there, not from taste.
The measurement: dump the distinct `xt-*` tags across the library with a count for each, then
look for (a) tags on ~100% of items, which carry no information and cost overlay space, (b)
tags on <1%, which are noise, (c) pairs that are near-perfectly correlated. `_tag_config_hash()`
already exists to force a re-tag when the taxonomy changes, so the migration path is in place.
This is a measurement, not an opinion — take the histogram first, re-label, then cut.

**U9 note.** There is currently **no tag query surface at all** — no `def` in `state.py`,
`pipeline.py` or `web/routes.py` searches or filters by tag. Tags are written outward to
Jellyfin/\*arr and never read back for browsing. So this is new construction, not an
improvement, and it interacts with U8: deciding which tags are worth keeping is much easier
once you can query them, and knowing which are never queried is exactly U8's evidence. Open
decision: does this filter the local `state.db` index only, or proxy to Jellyfin's own search?

**U10 (re-tag on update) is NOT a new item — it is already covered**, and splitting it would
create a third owner for one behaviour. Re-tagging when a file changes is U2's "handle
mtime-preserving re-encodes" (the detection half: a re-encode that preserves mtime is invisible
to an incremental scan) plus U3's per-item rescan on Sonarr/Radarr/Jellyfin `Download` events
(the trigger half). **Do U3 then U2** — the webhook is Complexity 2 and delivers most of the
benefit; the mtime problem is the residue for files that change without an event.

### I — Infrastructure

| ID | Feature | Value | Complexity | Issue |
|----|---------|:-----:|:----------:|-------|
| I1 | CSRF protection: form token validation on login and settings forms | 4 | 1 | [#14](https://github.com/bpoulliot/xenotag/issues/14) |
| I2 | Backup/restore API: download/upload state.db; prevents full rescan after container upgrades | 4 | 2 | [#18](https://github.com/bpoulliot/xenotag/issues/18) |
| I3 | Alembic DB migrations: structured schema versioning; required before any further schema changes | 5 | 3 | [#15](https://github.com/bpoulliot/xenotag/issues/15) |
| I4 | HTTP connection pooling for Jellyfin/Sonarr/Radarr clients | 3 | 1 | [#19](https://github.com/bpoulliot/xenotag/issues/19) |
| I5 | Prometheus metrics endpoint | 3 | 2 | [#17](https://github.com/bpoulliot/xenotag/issues/17) |
| I6 | ntfy push notifications: configurable server URL, token, and topic in settings UI; notify on scan complete, scan error, and batch tag events | 3 | 2 | — |

### P — Polish

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| P6 | **Background-aware palette (main + backup)** — sample the poster region under each badge and pick the palette that contrasts with it. | 4 | 4 | NEEDS DECISION | — |
| P7 | **Overlay density / simplification** — fewer, clearer badges by default. | 4 | 3 | NEEDS DECISION | — |

**P6 note — gated on B1.** Today there is **no palette detection anywhere**: `_pill_tile()`
takes `fill_hex` from config and calls `_parse_color()`, and it never receives the base image.
The colours are four fixed constants. So "main + backup palette" is new construction.

**B1 has shipped, so this is now answerable.** Before it, a backup palette would have
inherited the glow's 3.7–5.3:1 ceiling and read as ineffective no matter which colours it
picked; the rendered ratio is now a function of the configured colour, which is what P6 needs
to be worth measuring. Note B1's finding that the poster barely mattered: once the pill is
opaque the backdrop is invisible *under* the badge, so P6 buys legibility of the badge against
its surroundings, not of the text against the fill. Be clear which of the two it is selling.

Two implementation traps worth recording before anyone starts:

 - **The pill cache is keyed on appearance only** (`tests/test_badge_contrast.py` has a test
   that fails when this stops being true). `_PILL_CACHE` key is
   `(text, fill_hex, text_hex, alpha, font_size)` — no position, no background. A
   background-aware palette makes the *same* text render differently per poster, so the key
   must gain a palette-selection term or every poster after the first gets the first one's
   colours. This is the kind of bug that looks like "the feature didn't work" rather than a
   cache bug.
 - **Sample the region the badge lands on, not the poster.** `badge_position` is
   `bottom-left` by default and `_compute_layout_params()` already knows the geometry; a
   whole-image dominant colour would be the wrong measurement.

Open decisions: main+backup (pick one of two by luminance threshold) or continuous selection?
Does the operator get to see/override the choice, per the constrained-controls philosophy used
elsewhere? Is the glow retained as a third option for busy backgrounds?

**P7 note.** With `show_video_badges` / `show_audio_badges` / `show_sub_badges` /
`show_rating_badge` all defaulting `True`, plus U4 adding per-language subtitle badges and U7
adding a rating, the default poster gains badges faster than anything removes them —
`_truncate_label()` already exists because labels outgrow the space. The likely shape is a
density budget (N badges max, with a documented precedence order) rather than per-category
booleans, but that is the decision to make. **Sequence this after U8**: the cheapest way to
simplify the overlay is to stop emitting tags that carry no information, and U8's histogram is
the evidence for which those are.

---

## Far-term

### U — User-facing

| ID | Feature | Value | Complexity | Issue |
|----|---------|:-----:|:----------:|-------|
| U5 | Extended ffprobe tags: video profile, bitrate tier, interlacing, frame rate | 4 | 3 | [#24](https://github.com/bpoulliot/xenotag/issues/24) |
| U6 | Extended metadata tags from Jellyfin/\*arr: genres, original language, runtime bands, series status, ratings, custom formats | 4 | 5 | [#25](https://github.com/bpoulliot/xenotag/issues/25) |

### P — Polish

| ID | Feature | Value | Complexity | Issue |
|----|---------|:-----:|:----------:|-------|
| P4 | Mobile-responsive UI: full breakpoint coverage | 3 | 2 | [#26](https://github.com/bpoulliot/xenotag/issues/26) |
| P5 | README sample screenshots and overlay examples | 2 | 1 | [#23](https://github.com/bpoulliot/xenotag/issues/23) |

### I — Infrastructure

| ID | Feature | Value | Complexity | Issue |
|----|---------|:-----:|:----------:|-------|
| I7 | pillow-simd acceleration (marginal gain; ffprobe is the bottleneck, not PIL) | 2 | 3 | [#20](https://github.com/bpoulliot/xenotag/issues/20) |

> **Renumbered 2026-09-22:** this was a second `I6`, colliding with the ntfy item in Near-term.
> Referenced as `I6` in anything predating this date, it means whichever of the two fits context.

---

## Deferred / Out of Scope

| Feature | Reason |
|---------|--------|
| Outbound API rate limiting (Jellyfin/\*arr) | LAN services, no documented limits; natural scan serialization is sufficient |
| Whisper transcription integration | Out of scope; heavy model dependency; use Bazarr instead |
| Bare metal install guide | [#16](https://github.com/bpoulliot/xenotag/issues/16) — low demand, Docker is the primary path |
