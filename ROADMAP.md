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
| B1 | **Badge contrast is roughly half what the config claims.** `ImageConfig` annotates each badge colour "verified WCAG AAA ≥7:1 against white text" — true of the opaque hex, but not of what renders. | 5 | 2 | READY | — |

**B1 detail — measured 2026-09-22**, by rendering real `_pill_tile()` output over flat
backdrops and sampling the *modal* (flat-fill) interior pixel, not an antialiased edge:

| badge | claimed | on black | on white | on grey |
|---|---:|---:|---:|---:|
| video `#134e4a` | 9.5:1 | 4.6:1 | **3.7:1** | 4.1:1 |
| audio `#1e3a8a` | 10.4:1 | 4.9:1 | 3.9:1 | 4.3:1 |
| sub `#7c2d12` | 9.4:1 | 4.7:1 | 3.8:1 | 4.2:1 |
| rating `#4c1d95` | 11.0:1 | 5.3:1 | 4.2:1 | 4.7:1 |

**Every badge on every backdrop fails AAA (7:1). Nine of twelve fail AA (4.5:1).**

Two compounding causes, and the second is the bigger one:

 1. `badge_opacity: 0.65` → the pill is drawn at `alpha=165`, so the poster shows through the
    fill. The contrast figures in the comment were computed at `alpha=255`.
 2. **The white glow is the dominant term.** `_pill_tile()` unconditionally draws
    `fill=(255,255,255,210)` *behind* the pill (`_GLOW_EXPAND=4`, `_GLOW_BLUR=6`), then
    composites the translucent pill on top. So the backdrop under the fill is mostly white
    regardless of the poster — which is why the numbers barely move between black and white,
    and why they are low everywhere. The glow was presumably added to separate the badge from
    dark posters; it is instead washing out every badge on every poster.

A backup palette (P6) will not fix this on its own — the glow has to become conditional, or
the opacity raised, or both. **Do B1 before P6**; P6's whole premise is that the palette is
what determines contrast, and right now it is not.

Reproduce: `/tmp/xt-contrast/measure.py` (throwaway; re-create from this table if gone).

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

**Do B1 first.** As measured above, the thing destroying contrast right now is the
unconditional white glow, not the palette choice — a backup palette layered on top of the
current glow would inherit the same 3.7–5.3:1 ceiling and the work would read as ineffective.

Two implementation traps worth recording before anyone starts:

 - **The pill cache is keyed on appearance only.** `_PILL_CACHE` key is
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
