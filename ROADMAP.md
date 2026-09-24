# Xenotag Roadmap

Items are scored **Value** (1–5: user/correctness impact) and **Complexity** (1–5: implementation effort).
Easy wins = high value, low complexity. Categories: **B** = bug/correctness, **U** = user-facing
behavior, **I** = infrastructure, **P** = polish/UX.

**Readiness** (adopted 2026-09-22, matching van1sh/abtidy/sightline): every item is
**READY** (spec is complete, can be built as written), **NEEDS DECISION** (a human choice is
open — never start one), or **NEEDS MEASUREMENT** (a number has to be taken first; measure,
record, re-label — do not implement in the same pass). Unlabelled items predate this and are
unassessed.

**BLOCKED on \<ID\>** (added 2026-09-23): the spec is settled and no human choice is open, but
another item must land first. Distinct from NEEDS DECISION, where the holdup is a person, and
from NEEDS MEASUREMENT, where it is a number — here the holdup is *another item*, so the thing
to do is go work on that one.

**Tier 0 comes first.** Correctness defects outrank features regardless of Value score.

---

## Tier 0 — Correctness

| ID | Defect | Value | Complexity | Readiness | Issue |
|----|--------|:-----:|:----------:|-----------|-------|
| B1 | **Badge contrast is roughly half what the config claims.** `ImageConfig` annotates each badge colour "verified WCAG AAA ≥7:1 against white text" — true of the opaque hex, but not of what renders. | 5 | 2 | **FIXED 2026-09-22** | — |
| B2 | **A configured badge colour is never checked for contrast.** Any hex the settings UI or `config.yml` supplies is used as-is; the live deployment's palette renders at 2.6:1. | 4 | 2 | READY | — |
| B3 | **`_PILL_CACHE`'s key omits the padding.** Two poster widths can agree on `font_size` and disagree on `pad_h`/`pad_v`, so the first one rendered supplies the tile for both. | 2 | 1 | READY | — |
| B4 | **Two shipped badge colours are the same colour to a colour-blind viewer.** `audio` and `rating` separate by CIEDE2000 **1.9** under deuteranopia — below the threshold at which they differ at all. | 3 | 1 | READY | — |
| B5 | **Nothing has ever been written to Sonarr or Radarr.** `_find_arr_id()` reads `ProviderIds["Sonarr"]`/`["Radarr"]`, a key Jellyfin does not set on any of the 9,414 items — so the \*arr tag write and the \*arr certification fallback are both dead code in production. | 4 | 3 | READY | — |

**B5 — FILED 2026-09-24, found while auditing U1's outward destinations. Not fixed here.**

`pipeline._process_one_item()` builds `sonarr_tags` and `radarr_tags`, then gates the write on
`_find_arr_id(item, "Sonarr")` / `("Radarr")`, which is:

    provider_ids: dict = item.get("ProviderIds") or {}
    raw = provider_ids.get(provider)

**Jellyfin never sets those keys.** Swept across all 9,414 items in the 17 configured libraries
on 2026-09-24, the `ProviderIds` keys present are: `Tmdb` (9,381), `Imdb` (9,328), `Tvdb`
(2,458), `TvMaze` (2,149), `TmdbCollection` (1,546), `AniDB` (582), `TvRage` (480). **`Sonarr`:
0. `Radarr`: 0.** So `sonarr_id`/`radarr_id` is always `None` and
`client.set_managed_tags(...)` is never reached — for any item, ever.

**Corroborated independently and from the other end.** `SonarrClient._get_or_create_tag()`
creates a label on first use, so a single successful write would leave a permanent `xt-*` tag
behind. A read-only `GET /api/v3/tag` on all five instances returns **zero** `xt-*` labels:
sonarr/general 12 labels, sonarr/anime 10, sonarr/4k 0, radarr/general 20, radarr/4k 3 — none
managed. Meanwhile `tags.destinations` has `video` and `audio` both set to
`[poster, jellyfin, sonarr, radarr]`, so the settings UI reports a destination that has never
received anything.

**The same key kills a second feature.** `_get_arr_certification()` reads the identical
`ProviderIds["Sonarr"]`/`["Radarr"]`, so the "Jellyfin has no `OfficialRating`, fall back to the
\*arr certification" path never fires either. **1,497 of 10,575 indexed items (14.2%) have a
blank `content_rating`** — an upper bound on what that fallback could be recovering and is not.

**The fix is not to invent the key.** `SonarrClient.preload()` already builds an id→series cache
and `RadarrClient` the same for movies; both carry `tvdbId`/`tmdbId`, which Jellyfin *does*
supply. Matching on those turns two dead branches into working ones without a Jellyfin plugin.

**Complexity 3 rather than 1 because this item starts writing to live \*arr instances**, and all
five run with `recycleBin` empty. Tag writes do not displace files, but the `_put("/series/{id}",
series)` round-trips the whole series object — so this needs a dry-run/count mode and a
read-back check before it is let near production, and it should say so in its own spec.

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

**B2 — DECIDED 2026-09-23: WARN, do not prevent.** The operator: *"B2 should warn, not
prevent."*

So a configured colour that fails contrast is **rendered as asked** and reported — never
refused. The reasoning that follows from it: a refusal would make xenotag override a deliberate
aesthetic choice, and a badge that silently does not appear is a worse failure than a badge that
is hard to read. The operator owns the trade; the tool's job is to make sure they are making it
knowingly.

**What "warn" should mean, and it is not a log line nobody reads:** surface the measured ratio
**in the Settings UI, beside the colour picker, at the moment of choosing** — with the AA/AAA
thresholds named. A warning emitted at scan time is a warning delivered to nobody.

**Note the measurements below are STALE as of 2026-09-23.** The live config was reset to
defaults that day (`badge_opacity` override removed, all four custom colours removed), so the
"configured" column no longer describes the deployment. **Re-measure with `--config FILE` before
using any of these numbers.** The defect itself is unchanged: nothing checks a configured
colour.

*(original filing follows)*

The four colours in `ImageConfig` are only defaults. The settings UI and `config.yml` accept
any hex and **nothing checks it**. The live deployment at `~/docker/xenotag/config/config.yml`
has replaced all four, and it keeps `badge_opacity: 0.65`, so B1's new default does not reach
it. Measured with the same probe (`--config FILE`), before and after B1:

| badge | configured | opaque hex | black | white | grey |
|---|---|---:|---:|---:|---:|
| video | `#1a7a6e` | 5.2:1 | 3.3 → 9.4 | 2.7 → **2.7** | 3.0 → 4.8 |
| audio | `#6b3a9e` | 7.7:1 | 4.1 → 12.2 | 3.3 → **3.3** | 3.7 → 6.2 |
| sub | `#a86200` | 4.8:1 | 3.1 → 8.9 | 2.6 → **2.6** | 2.8 → 4.6 |
| rating | `#2d2d2d` | 13.8:1 | 5.7 → 16.9 | 4.5 → **4.5** | 5.0 → 8.9 |

B1 fixes the dark-poster case for this palette and **does nothing for the light-poster case**,
exactly as its own lever table predicts: at 0.65 the thing showing through the fill is no
longer the glow, it is the poster. All four still fail AA on white.

Two separate things are wrong here, and only the second is B2:

 - **The opacity.** If this deployment sets `badge_opacity: 1.0` it renders
   5.2 / 7.7 / 4.8 / 13.8 on every backdrop. That is the operator's to change and needs no
   code.
 - **The palette, which no amount of opacity rescues.** Even fully opaque, `#1a7a6e` is 5.2:1
   and `#a86200` is 4.8:1 against white text — AA, never AAA. Those two hexes never had the
   headroom, and nothing in the app ever said so.

The work is a contrast check on the colour inputs — the ratio is ~15 lines and already exists
in the probe. Open question for the operator, which is why this is B2 and not part of B1:
**warn or refuse?** A hard refusal rejects a palette someone deliberately chose; a warning next
to the colour picker (and next to the opacity slider, whose range still reaches 10%) informs
without overriding. The UI already renders a live preview, so the number has somewhere to go.

Reproduce either table: `python3 scripts/measure_badge_contrast.py [--config FILE] [--opacity X]`.

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

**B4 — found 2026-09-23 while speccing the rebrand, and it is not a rebrand problem.**

B1 established that every badge is readable *against its own fill*. Nobody checked the other
half: **whether a video badge is tellable from an audio badge**, which is the entire reason the
four categories carry different colours. They are not, for a substantial minority of viewers.

New probe: **`scripts/measure_palette_separation.py`** (`--config FILE`, `--self-test`). It
reports CIEDE2000 between every pair under normal vision and under simulated protanopia,
deuteranopia and tritanopia (Machado 2009, severity 1.0), and **exits non-zero** when any pair
falls below dE 5 — the point at which two badges are not reliably different colours in situ.
Shipped defaults:

| pair | normal | protanopia | deuteranopia | tritanopia |
|---|---:|---:|---:|---:|
| video/audio | 28.7 | 22.7 | 19.1 | 8.8 |
| video/sub | 40.4 | 18.7 | 26.1 | 47.5 |
| video/rating | 32.2 | 24.8 | 20.1 | 22.6 |
| audio/sub | 39.9 | 44.4 | 49.7 | 48.8 |
| **audio/rating** | 12.1 | **3.6** | **1.9** | 17.7 |
| sub/rating | 40.1 | 48.0 | 52.0 | 30.9 |

`audio` `#1e3a8a` (navy) and `rating` `#4c1d95` (violet) are adjacent hues at nearly the same
lightness. Normal vision separates them on hue alone (12.1 — already the weakest pair). Remove
the red-green axis and there is nothing left: **1.9 under deuteranopia**, which affects roughly
6% of men. Those two badges are the same colour to them.

**Why it went unnoticed:** the rating badge renders top-right and the audio badge bottom-left, so
they rarely sit side by side — the failure is "I cannot tell what kind of badge this is", not
"these two look alike". And every existing check is a *contrast* check, which this palette passes
at AAA across the board.

**The fix is a colour constant, not code.** The `--config` flag measures any candidate before it
ships. Two measured replacements, both AAA and both clearing dE 12 in the worst case, are
recorded under [P10]; the cheapest is to change `rating_badge_color` alone. **B1's
config-persistence caveat applies** — an operator who has saved Settings keeps the old hexes, so
decide whether this is a default change or a migration.

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
| U1 | Tag migration: clean up legacy `mf-*` tags on upgrade from Metafin; `tags.legacy_prefixes` config option | 5 | 2 | **FIXED 2026-09-24** | [#35](https://github.com/bpoulliot/xenotag/issues/35) |
| U2 | Tag lifecycle: remove stale `xt-*` tags when items are deleted from Jellyfin; handle mtime-preserving re-encodes | 5 | 3 | — | [#36](https://github.com/bpoulliot/xenotag/issues/36) |
| U3 | Webhook / event-driven processing: per-item rescan on Sonarr/Radarr/Jellyfin Download events | 5 | 2 | — | [#22](https://github.com/bpoulliot/xenotag/issues/22) |
| U4 | Subtitle language tagging: write `xt-sub-*` tags to Jellyfin/Sonarr/Radarr (ffprobe extraction already exists) | 4 | 2 | — | [#11](https://github.com/bpoulliot/xenotag/issues/11) |
| U7 | ~~**Ratings ingest**~~ — **CLOSED 2026-09-23, premise was wrong**: xenotag already emits certification ratings from `OfficialRating` | 4 | 2 | **CLOSED** | — |
| U8 | **Tag taxonomy pass** — audit the `xt-*` set actually emitted and collapse what is redundant or never queried. | 4 | 3 | **MEASURED 2026-09-22 → READY** | — |
| U9 | ~~Tag queries~~ **RESCOPED: a manual correction to an `xt-*` tag is silently clobbered on the next scan** | 4 | 3 | NEEDS DECISION | — |

**U1 — FIXED 2026-09-24, and the premise was half wrong in a way worth recording.**

U8 read 67 distinct `mf-*` tags / 247 applications / 32 items out of `state.db` and concluded
"U1 has not happened." Reproduced exactly on 2026-09-24 against a copy of the live
`state.db` — same 67 / 247 / 32, now over 10,575 rows. **But the conclusion did not follow.**

**Where the legacy tags were is not where anyone was looking.** `state.db` is xenotag's record
of what it last *wrote*; it is not the library. Swept read-only on the same day:

| where | items looked at | distinct `mf-*` | applications |
|---|---:|---:|---:|
| `media_state.tags_applied` | 10,575 | **67** | **247** |
| live Jellyfin, 17 configured libraries | 9,414 | **0** | **0** |
| live Jellyfin, every item type, no `ParentId` | 83,238 | **0** | **0** |
| all five Sonarr/Radarr instances (`/api/v3/tag`) | — | **0** | **0** |

**So `set_managed_tags()` was right all along, and its legacy strip had already run to
completion.** Not one legacy tag survived anywhere outward. The migration this item was filed
to perform was, on the media server, already done.

**Why the 32 rows survived — two mechanisms, both evidenced, neither of them the one the item
guessed at.** The candidate list proposed "`set_managed_tags` strips only when also writing"
and "the destination matters"; both are false. The real answer is that neither row was *ever
revisited*:

 - **31 of 32 are orphans.** A `/Items?Ids=…` lookup of all 32 IDs returns **one** item. The
   other 31 no longer exist in Jellyfin and their files are gone from disk — displaced
   re-encodes, mostly (`Iron Lung (2026)` appears twice, once as x264 and once as AV1). `state.db`
   has no reconciliation pass, so a row outlives the item it describes. **That is [U2]'s job**,
   not this one's — and measured the same way, **1,162 of the 10,575 rows (11.0%) describe a
   Jellyfin item that no longer exists.** (Not 10,575 − 9,414 = 1,161: one live item has no row
   at all, so the two errors nearly cancel. Count the set difference, not the totals.)
 - **1 of 32 is `probe_failed`.** `Frontier War (2024)` is still in Jellyfin and its file is
   still on disk, but ffprobe failed on it during the 2026-09-23 full scan (`scan_errors` row,
   `error_type='probe_failed'`). `_run_scan()` `continue`s on a `None` probe result
   (`pipeline.py:526-530`) **before** `_process_one_item()`, so the item is never tagged and its
   row is never rewritten. Its Jellyfin item carries zero tags of either prefix.

Every one of the 32 rows held **only** `mf-*` tags and nothing else, with `last_scanned` between
2026-05-06 and 2026-06-01 — i.e. every one was last written by Metafin, before the rename.

**What shipped, therefore, is not an outward migration.** There was nothing outward left to
migrate, and building a forced Jellyfin re-tag would have been building for a problem that does
not exist. What shipped is the *local* half nobody had written:

 - `state.purge_legacy_tags()` — strips `tags.legacy_prefixes` from `media_state.tags_applied`,
   with a dry-run mode, called once at app startup (`main.lifespan`). Startup is the right
   trigger precisely because the rows that hold the residue are the ones no scan ever reaches.
 - `GET /api/legacy-tags` (dry run, reports counts) and `DELETE /api/legacy-tags` (apply).
 - `scripts/audit_legacy_tags.py` — the re-runnable measurement, read-only, with a `--self-test`
   that CI runs. `--live` repeats the Jellyfin and \*arr sweeps above.

**The guard that matters more than the sweep:** `partition_legacy_tags()` keeps any tag carrying
`managed_prefix` **unconditionally**, even when a legacy prefix also matches it.
`legacy_prefixes` is operator-supplied config and nothing stops it holding `"x"` — which
prefix-matches every `xt-` tag in the index. A legacy sweep that eats managed tags is far worse
than the legacy tags, so the managed prefix wins by construction rather than by luck. Pinned in
`tests/test_legacy_tags.py`, and the self-test proves it in both directions (it also checks that
the overlapping prefix *still removes* a genuine `x-`-prefixed legacy tag, or the check would
pass for a sweep that had merely stopped working).

**A prefix is not a namespace, and this was checked rather than assumed.** Of the **15,553**
distinct non-managed tags in the live Jellyfin library, **none** begins with `mf` or `xt` at
all — so `startswith("mf-")` is safe *there, today*. It is not safe by construction; `mfx-`
survives the sweep by test.

**Driven to zero, on the live deployment.** Container stopped, `state.db` backed up to
`state.db.bak-20260924-pre-u1`, dry run first (`rows_examined=32 rows_changed=32
tags_removed=247`, nothing written — asserted), then applied. Re-measured from a fresh
read-only copy: **0 distinct / 0 applications / 0 items**, row count still 10,575. The `xt-*`
histogram is **byte-identical** across the sweep — 194 distinct, 66,068 applications, before and
after — which is the proof that nothing managed was touched, rather than an assertion that it
was not.

**U7 — CLOSED 2026-09-23. The item rested on a misreading, and the misreading was the
assistant's.** The operator: *"rating is the MPAA, TV, government or rating board rating (e.g.,
TV-MA, R, 16, 18) and not the star or numerical review rating."*

That is correct, and it is already what xenotag does. Verified in code: `app/pipeline.py:144`
reads `item.get("OfficialRating")`, `app/clients/jellyfin.py` requests `OfficialRating` in its
`Fields` list and writes it back with an **\*arr certification fallback** when Jellyfin's own
field is blank (`fallback_rating`, `jellyfin.py:159-173`). **`CommunityRating` and
`CriticRating` appear nowhere in the codebase.**

U8's histogram is the independent confirmation — the rating tags in the live library are all
certifications: `xt-R` (2,963), `xt-PG-13` (1,363), `xt-TV-MA` (1,012), `xt-PG` (969),
`xt-TV-14` (740), `xt-NR` (430), `xt-TV-PG` (263), `xt-G` (199), `xt-Not Rated` (187). Not a
single numeric score.

So the "which rating source?" question this item was filed to answer **does not exist**. The
render path it described as ready-and-waiting is not waiting — it is in production.

**What remains of U6's "ratings", if anything, is a numeric-score feature nobody asked for.**
The operator's framing says that is not what "rating" means here. Do not re-file it without a
request.

~~**U7 note — why this is split out of U6.**~~ *(superseded, kept for provenance)* U6 ("Extended metadata tags") is Complexity 5 because
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

**U8 — MEASURED 2026-09-22, in an interactive session. Two of the three hypotheses are FALSE.**

Source: `~/docker/xenotag/config/state.db`, `media_state.tags_applied`, copied and opened
`mode=ro`. **10,480 items, 258 distinct tags.**

The original note proposed looking for (a) tags on ~100% of items, (b) tags on <1%, and
(c) near-perfectly correlated pairs. Measured:

| hypothesis | result |
|---|---|
| (a) tags on ~100%, carrying no information | **NONE.** The most common tag is `xt-1080p` at **76.3%** |
| (b) tags on <1% | **221 of 258 — 86% of the vocabulary** |
| (c) near-perfect correlations | **NONE** at Jaccard ≥ 0.90 among tags on ≥2% of items |

So there is nothing to cut for being universal, and nothing to merge for being redundant.
**The entire finding is the long tail**, and the distribution is the deliverable:

    >=50%     4 tags        1-10%    21 tags       <0.1%   154 tags
    10-50%   12 tags        0.1-1%   67 tags

**Correction to the headline, and it matters: 67 of those 258 tags are legacy `mf-*`, not
`xt-*` at all.** The live vocabulary is **191 `xt-*` tags**, of which **154 (81%) are on under
1% of items**. Still dramatic — but quote 191, not 258.

**Re-measured 2026-09-24, after [U1] removed the legacy contamination.** The index is now
**10,575 items / 194 distinct tags, all `xt-*`** — no correction needed any more, the headline
figure is the `xt-*` figure. The shape is unchanged: **158 of 194 (81%) are on under 1% of
items**, and the head is still `xt-1080p` at **76.3%**, so hypothesis (a) stays false.

    >=50%     4 tags        1-10%    19 tags       <0.1%    96 tags
    10-50%   13 tags        0.1-1%   62 tags

Re-run it with `python3 scripts/audit_legacy_tags.py --db <copy of state.db>` — the vocabulary
counts there are the same ones this table is built from.

What the tail is made of (of the 221 rare tags): **105 codec/HDR/misc, 72 subtitle-language,
37 audio-language, 6 rating, 1 resolution.**

**Two consequences worth acting on:**

1. ~~**This is live evidence that [U1] has not happened.**~~ **DONE 2026-09-24 — and the
   inference was wrong.** The 67 distinct `mf-*` tags / 247 applications / 32 items were real
   and reproduced exactly, but they were **only ever in `state.db`**: the live Jellyfin library
   carried **zero** `mf-*` tags across all 83,238 items, and so did all five \*arr instances.
   The legacy strip in `set_managed_tags()` had already run to completion; what survived was
   xenotag's own record for 32 rows a scan never revisits (31 orphans, 1 `probe_failed`). See
   the [U1] note. They are gone now, and this item's numbers above are re-measured without
   them.

2. **[U4] makes this worse, and should be weighed against it.** U4 adds `xt-sub-*` subtitle
   language tags to more destinations — and **72 of the 83 subtitle-language tags already
   emitted are below 1%**. Shipping U4 as specified widens the tail it is reasonable to want
   narrowed. That is not an argument against U4; it is an argument that U4 and U8 are one
   decision, not two.

**What remains for U8 to decide (why it is READY, not DONE):** whether a tag on <1% of items is
noise or precision. A `xt-sub-HU` on 100 items is useless as a *badge* and may be valuable as a
*query* — which is [U9]. So the cut is not "delete the tail"; it is **per-destination**: the
poster overlay takes the head, the tag destinations can take the tail. `TagDestinations`
already models exactly that, per category — the mechanism exists and is unused for this.

**Method note for whoever implements it:** the histogram is ~20 lines against a copy of
`state.db` and needs no Jellyfin call. Re-run it rather than trusting these numbers; they were
true on 2026-09-22 at 10,480 items.

**U8 note.** "Simplify the tags" needs to start from what is actually there, not from taste.
The measurement: dump the distinct `xt-*` tags across the library with a count for each, then
look for (a) tags on ~100% of items, which carry no information and cost overlay space, (b)
tags on <1%, which are noise, (c) pairs that are near-perfectly correlated. `_tag_config_hash()`
already exists to force a re-tag when the taxonomy changes, so the migration path is in place.
This is a measurement, not an opinion — take the histogram first, re-label, then cut.

**U9 — RESCOPED 2026-09-23 by the operator.** *"Not sure u9 is required. Tags can already be
queried in Jellyfin. Should this take the shape of being able to edit existing tags instead if
the labeling is wrong?"*

**The query half is dropped.** Jellyfin already indexes and searches tags; building a second
query surface in xenotag duplicates it for no gain. U8 wanted a query to learn which tags are
used — Jellyfin can answer that.

**What replaced it is a real defect, found while checking the reframe.**
`JellyfinClient.set_managed_tags()` does:

    user_tags = [t for t in existing if not any(t.startswith(p) for p in all_prefixes)]
    merged = user_tags + new_tags

So tags **without** the `xt-`/`mf-` prefix survive a rescan — but **every `xt-*` tag is replaced
wholesale**. A human who corrects a wrong `xt-*` tag in Jellyfin has their edit **silently
reverted on the next scan**, with nothing logged and nothing to notice.

**The open decision is which of two this becomes:**

 1. **Fix the derivation.** If an `xt-*` tag is wrong, the ffprobe/metadata logic that produced
    it is wrong, and the correct fix is upstream — a manual edit would only paper over it. This
    treats clobbering as correct behaviour and the wrong tag as the bug.
 2. **An override mechanism.** Some corrections a human can make and a probe cannot (a
    mislabelled audio track, a container lying about its language). Those need somewhere to
    live that a rescan respects — and per-item, not global.

**These are not exclusive**, but 1 is much cheaper and may cover most real cases. **Before
choosing, measure how often an `xt-*` tag is actually wrong** — that is the evidence, and
nobody has it. The operator also raised whether this is really Jellyfin metadata editing rather
than xenotag's job; if the answer is (1), it is neither — it is a xenotag derivation bug.

~~**U9 note.** There is currently **no tag query surface at all**~~ *(superseded)* — no `def` in `state.py`,
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

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| I1 | CSRF protection: form token validation on login and settings forms | 4 | 1 | — | [#14](https://github.com/bpoulliot/xenotag/issues/14) |
| I2 | Backup/restore API: download/upload state.db; prevents full rescan after container upgrades | 4 | 2 | — | [#18](https://github.com/bpoulliot/xenotag/issues/18) |
| I3 | Alembic DB migrations: structured schema versioning; required before any further schema changes | 5 | 3 | — | [#15](https://github.com/bpoulliot/xenotag/issues/15) |
| I4 | HTTP connection pooling for Jellyfin/Sonarr/Radarr clients | 3 | 1 | — | [#19](https://github.com/bpoulliot/xenotag/issues/19) |
| I5 | Prometheus metrics endpoint | 3 | 2 | — | [#17](https://github.com/bpoulliot/xenotag/issues/17) |
| I6 | ntfy push notifications: configurable server URL, token, and topic in settings UI; notify on scan complete, scan error, and batch tag events | 3 | 2 | — | — |
| I8 | **Secrets can only live in `config.yml`, which the app rewrites** — no env override, so the host's SOPS pipeline cannot reach them | 4 | 2 | **SHIPPED 2026-09-24** | — |
| I9 | **Sonarr/Radarr API keys cannot be externally managed** — I8's override table is addressed by dotted path, and the `*arr` keys live in a list | 3 | 3 | NEEDS DECISION | — |

**I8 — SHIPPED 2026-09-24.** `jellyfin.api_key`, `auth.secret_key` and `webhooks.secret` can now
be supplied by `JELLYFIN_API_KEY` / `XENOTAG_SECRET_KEY` / `XENOTAG_WEBHOOK_SECRET`, or by the
`_FILE` twin of any of them, and a field the environment supplies is **never written back** to
`config.yml` — not by the Settings page, not by
the raw YAML editor, not by the first-run bootstrap. 27 tests in `tests/test_env_overrides.py`;
the five that matter were confirmed to fail with the strip removed. See "Externally managed
secrets" in the README.

Three decisions the implementation had to make that the note below did not settle:

* **`_FILE` beats the bare variable** when both are set. The file form is what a secrets pipeline
  writes on purpose; a bare variable is the form that arrives by accident, out of a shared compose
  `env_file` or an inherited shell environment. The deliberate source wins.
* **Failure is asymmetric.** A `_FILE` that is unreadable or names an empty file is **fatal at
  startup**: naming a file is unambiguous intent, and continuing with the stale value in
  `config.yml` is precisely the silent failure the trap below warns about. A bare variable set to
  the **empty string** is ignored with a WARNING instead — empty is the signature of an
  unpopulated `${VAR}` interpolation, not of intent, and blanking a working key on that basis
  would take the deployment down for a typo in an unrelated file.
* **Loading never writes.** Enabling an override does not purge the value already sitting in
  `config.yml`; the next save does, and the README says so. A load that rewrote the file would be
  a surprising side effect on a read path, and would fire on every container start.

**The I2 question this note asked, answered: no, `config.yml` should not be in I2's backup.**
Not as a blanket rule — a backup that captures a rendered secret is a leak with a much longer
half-life than the original, because backups are copied, mailed and kept. I8 makes the good
version possible: with the secrets externally managed, what is left in `config.yml` is settings,
and settings are worth backing up. So I2 should back up `config.yml` **through the same strip**
`_persistable()` applies on save — never the raw file — and should say in the UI that restoring it
will not restore secrets. If I2 ships before an operator moves their secrets out, the strip is a
no-op and the backup does contain them; I2 should warn on that case rather than silently including
them.

`webhooks.secret` went in too, past the two fields the item named. It is a fixed dotted path with
no Settings field to make read-only, so it is one table entry and one test on an already-tested
code path — and leaving a plainly-shaped secret out would have been a half-job. `auth.password_hash`
stayed out on purpose: it is a verifier rather than a secret, and the first-run bootstrap has to be
able to write it.

**I9 note — filed 2026-09-24 while implementing I8.**

I8 gave `config.yml` three fields the environment can own, keyed by dotted path in
`ENV_OVERRIDABLE` (`app/config.py`). Every remaining secret in the file is a Sonarr or Radarr
instance key, and those are **not addressable that way**: `sonarr.instances` and `radarr.instances`
are lists of `ArrInstance`, so there is no stable dotted path to a given key. Evidence —
`config.example.yml` shows two `sonarr.instances` entries, each with its own `api_key`, and the
Settings UI (`renderInstances()` in `index.html`) lets an operator add and reorder them freely.

This is NEEDS DECISION rather than READY because the addressing scheme is a real choice with no
obviously right answer, and it is the operator's to make:

* **By index** — `SONARR_0_API_KEY`. Trivial to implement; breaks silently the moment someone
  reorders or deletes an instance in the UI, which is exactly the silent-rotation-failure mode I8
  exists to prevent. Probably disqualifying on its own terms.
* **By instance name** — `SONARR_API_KEY_<NAME>`, e.g. `SONARR_API_KEY_SONARR_4K`. Stable across
  reordering, but `ArrInstance.name` is a free-text field the UI lets you edit, so renaming an
  instance silently detaches its secret. Needs a rule for what happens on a rename, and needs a
  name→variable mangling (case, spaces, hyphens) that is documented rather than guessed.
* **Don't** — leave the `*arr` keys in `config.yml` and say so. They are lower-value than the
  Jellyfin key: they grant access to an already-internal service, and I8's leak was the Jellyfin
  key specifically.

Whichever is chosen, the read-only Settings treatment has to extend to a per-row field in the
instance table, which is more UI work than I8's single input needed — hence complexity 3.

**Original note — measured 2026-09-23, after a credential leak made it concrete.**

`config.yml` holds `jellyfin.api_key`, `auth.secret_key` and the bcrypt `auth.password_hash`.
An assistant session `cat`-ed the file to check badge settings and printed all three into a
transcript. Rotating them is currently a manual, four-step, per-secret job — and the host has a
SOPS + age pipeline (`scripts/materialize-secrets.sh`) that renders secrets for every other
service. **xenotag cannot use it**, for two independent reasons, either of which alone is
disqualifying:

1. **There is no env override to inject into.** `AppConfig` is a `BaseModel`, **not** a
   `BaseSettings`. The only environment variable `load_config()` reads is `CONFIG_PATH`
   (`app/config.py:141`). So there is no `JELLYFIN_API_KEY` or `..._FILE` hook for a secrets
   renderer to populate — the surrounding stack's `_FILE` convention has nothing to attach to.

2. **`config.yml` is read-write application state, not a rendered artifact.** `save_config()`,
   `save_settings()` and `save_auth()` all do `yaml.dump(cfg.model_dump())` and write the
   **whole file back**, secrets included. A SOPS-materialised `config.yml` would be **clobbered
   the moment anyone saves the Settings page** — a secrets pipeline fighting the application,
   with the application winning silently. That failure mode is worse than the status quo,
   because it looks like it works until someone opens Settings.

**The fix, and it is small:** let an environment variable (or `..._FILE`, matching the
convention the rest of the stack already uses) **override** the YAML value at load time, and
make the `save_*` functions **never write an overridden field back**. Roughly:

 - `load_config()` applies env overrides after `model_validate`, recording which fields came
   from the environment;
 - `save_*` omits those fields, so the file never re-acquires a secret it did not supply;
 - the Settings UI shows such a field as externally managed and read-only, rather than
   displaying a value it cannot persist.

**Start with `jellyfin.api_key`**, which is the one that leaked and the one with a real rotation
story; `auth.secret_key` follows the same shape.

**This does NOT mean sharing one Jellyfin key with the rest of the stack.** Verified 2026-09-23:
xenotag's key is *not* the `JELLYFIN_API_KEY` the host's scripts use, and that isolation is
worth keeping — a per-consumer key rotates and revokes independently, and Jellyfin's API key
list becomes an audit trail of who has access. **Same process, different key.** A shared key
means one leak forces a stack-wide rotation, which is exactly why the host's `pub-chroma` ntfy
rotation keeps being deferred.

**Related:** [I2] wants to download and upload `state.db`; the same question applies to
`config.yml`, and both are really "this file holds things it should not hold alone". Whoever
does I8 should say whether I2's backup should include `config.yml` at all once secrets can live
outside it — a backup that captures a rendered secret is a leak with a longer half-life.

**Trap for whoever implements it:** an override that is silently ignored is worse than no
override. If an env var is set and does not take effect — misspelled, wrong nesting, shadowed by
the YAML — the operator believes the secret rotated when it did not. **Log which fields were
overridden at startup**, by name, never by value.

### P — Polish

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| P6 | **Background-aware palette (main + backup)** — sample the poster region under each badge and pick the palette that contrasts with it. | 4 | 4 | NEEDS DECISION | — |
| P7 | **Overlay density / simplification** — fewer, clearer badges by default. | 4 | 3 | NEEDS DECISION | — |
| P8 | **Brand assets: icon, wordmark, favicon set** — replace the Metafin-era dragonfish mark everywhere it renders. | 3 | 2 | **READY** (PNG path; clean exports landed 2026-09-23) | — |
| P9 | **UI theme retoken to the brand palette** — Charcoal/Deep Forest/Sage/Warm Gray/Bone, with the accent lightened to clear AA. | 3 | 3 | READY | — |
| P10 | **Badge palette under a near-monochrome brand** — four badge categories, one brand green. | 2 | 2 | **DECIDED 2026-09-23 → READY** | — |
| P11 | **Brand vectors must reproduce the concept art exactly** — the supplied SVGs draw a different shape, and the PNG fallback is clipped. | 3 | 4 | NEEDS DECISION | — |

**P6 — KEPT 2026-09-23, explicitly as polish.** The operator: *"I still like the p6 idea and
think there's value to ensuring accessibility while allowing things like opacity and glow.
Definitely polish."*

That settles the question B1 raised. B1 found the poster barely matters once the pill is opaque
— the backdrop is invisible *under* the badge — which looked like it removed P6's reason to
exist. **It does not, because opacity and glow stay configurable.** The moment an operator turns
opacity down or the glow back on, the poster underneath becomes visible through the badge and
the contrast question returns. **P6 is what makes those knobs safe to use**, rather than
options that quietly break legibility.

So the framing is: not "pick colours per poster because contrast demands it", but **"keep the
configurable knobs accessible"**. That is polish, and it is worth doing.

~~**P6 note — was gated on B1, which shipped 2026-09-22.**~~ Today there is still **no palette
detection anywhere**: `_pill_tile()`
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
elsewhere? The glow question is now answered: B1 kept it, as a halo *around* the pill rather
than a wash behind it, so P6 does not have to decide its fate.

**P7 + U8 — REDIRECTED 2026-09-23. Hiding metadata is the wrong route.** The operator:
*"Seems like u8 should not be an app choice. Why hide this for even a poster oversaturated with
pills? All pills should be limited by poster margins. Maybe choosing the order of pills (e.g.
en, ja, de listed before anything else) by setting a 'prefer languages' or similar is useful but
I'm not sure hiding metadata is the correct route."*

**This rejects the premise both items were built on.** U8's measurement found 81% of the live
`xt-*` vocabulary sits on under 1% of items, and both U8 and P7 assumed the answer was to *cut*
the tail. It is not. A tag that is rare is not a tag that is worthless — it is often the most
informative one on that particular poster. `xt-sub-HU` on 100 items tells you something about
exactly those 100.

**The two real requirements that replace "cut the tail":**

1. **Pills are bounded by the poster margins, always.** Overflow is a layout defect, not a
   reason to drop information. `_truncate_label()` and `_measure_group_height()` already exist;
   the question is whether the layout *guarantees* containment at every `badge_size` and poster
   aspect, or merely usually achieves it. **Measure that** — it is answerable and nobody has.
2. **Order, not omission.** A `prefer_languages` setting (e.g. `en, ja, de` first, everything
   else after) puts the pills a given operator cares about where they are read first, without
   discarding the rest. This generalises beyond language — any category could carry a
   precedence list.

**What this means for the two items:**

 - **U8 is no longer "collapse the taxonomy".** Its histogram stands as evidence, but
   *"which tags should we stop emitting"* is answered: **none, on these grounds.** Re-label
   accordingly. (Its U1 finding was a defect in the *index*, not in the library, and is
   **fixed** — see the [U1] note.)
 - **P7 is no longer a density budget.** It becomes layout containment plus ordering — and the
   `show_*` booleans stay as the operator's own switch, since turning a category off is a
   choice they make knowingly rather than one the app makes for them.

~~**P7 note.** With `show_video_badges`~~ / `show_audio_badges` / `show_sub_badges` /
`show_rating_badge` all defaulting `True`, plus U4 adding per-language subtitle badges and U7
adding a rating, the default poster gains badges faster than anything removes them —
`_truncate_label()` already exists because labels outgrow the space. The likely shape is a
density budget (N badges max, with a documented precedence order) rather than per-category
booleans, but that is the decision to make. **Sequence this after U8**: the cheapest way to
simplify the overlay is to stop emitting tags that carry no information, and U8's histogram is
the evidence for which those are.

**P8–P10 — Brand rebrand, filed 2026-09-23 from an operator-supplied brand sheet.**

The sheet is canonical and names five colours. Every figure below was **measured** against those
hexes with the WCAG relative-luminance formula — the same one `scripts/measure_badge_contrast.py`
uses — not estimated from the artwork.

| token | hex | on Charcoal | role |
|---|---|---:|---|
| Charcoal | `#171B19` | — | page background |
| Deep Forest | `#203A30` | 1.42:1 | surface / raised surface — **never text** |
| Sage | `#708675` | **4.44:1** | accent — **fails AA**, see P9 |
| Warm Gray | `#B7B2A6` | 8.23:1 | muted text |
| Bone | `#E2DDCF` | 12.82:1 | body text |

Tagline *"Media information. Beyond the basics."*; icon at 128/64/32; wordmark in three weights
(primary / medium / small).

**The one fact that decides the shape of all three items:** the palette splits cleanly by
lightness. Charcoal and Deep Forest are dark enough to sit *under* white text; Sage, Warm Gray
and Bone are light enough to sit *on* a dark background. **Nothing in the brand does both.** So
the UI — light text on dark chrome — is a comfortable fit, while the badges — white text on a
coloured fill — are confined to two of the five colours. That is why P9 is READY and P10 is not.

**P8 — brand assets. The blocking input is the artwork, not a design question.**

What renders the mark today, all of it Metafin-era (`786949b`, "new metafin dragonfish mark"):
`app/static/logo.png` (1254×1254, **RGB, no alpha**), `app/static/favicon.png` (256×256 RGBA),
`app/static/favicon.ico` (16/32/48), referenced from `index.html:7,201` and `login.html:7,32`.

**The open decision is asset intake.** The operator has SVG sources, describes them as rough, and
they are not in the repo. Either **(a)** land cleaned SVGs as the source of truth and generate
every raster from them with a committed script, or **(b)** hand-export rasters once and keep no
source. **Recommend (a)** — the sheet already specifies three icon sizes and three wordmark
weights, so a generator pays for itself the first time a size is added.

Four traps, each of which surfaces as "the rebrand didn't work" rather than as an obvious failure:

1. **`?v=` is the VERSION string, not a content hash.** `routes.py:57-58` reads `VERSION`; the
   templates append `?v={{ v }}`. Swapping the assets **without bumping `VERSION` leaves every
   returning browser on the old mark**, indefinitely.
2. **`logo.png` has no alpha channel** — it is RGB. On a Charcoal page a non-transparent logo
   shows whatever background it was exported against. Export RGBA.
3. **The login logo is circle-cropped.** `login.html:14` sets `border-radius:50%;
   object-fit:cover` on a 56px box. That clips a rounded-square app tile and would crop a
   wordmark to a disc; the sheet's usage examples are rounded squares.
4. **There is no `apple-touch-icon`, no web manifest, and no `theme-color`.** None exist today.
   The rebrand is the moment to add them; `theme-color` should be Charcoal `#171B19`.
5. **The mark is two-tone, so exactly one half of it disappears on any given background — and
   a browser picks the background, not us.** Verified on the real 2026-09-23 artwork at
   16/32/48/64/180px:

   - on **Charcoal** chrome the bone arc carries the mark (Bone on Charcoal 12.82:1) and the
     green swirl fades (Deep Forest on Charcoal **1.42:1**);
   - on **light** chrome it inverts — the green carries it and the bone arc washes out.

   The mark stays *identifiable* either way, because one element always has contrast, but it
   reads as **half of itself**, and at 16px on light chrome it is weak. This corrects an earlier
   note here that blamed the green alone; the real property is the two-tone construction.

   **Fix: give the favicon and app icon a Charcoal backing tile** rather than shipping the bare
   transparent mark. Then both elements always sit on their intended background regardless of
   chrome. Verified legible down to 16px on a light page with a rounded-square tile at ~22%
   corner radius and the mark inset to ~80%. This also matches the brand sheet's own "usage
   examples", which show the icon on a rounded dark tile, not free-floating.

**Asset status, 2026-09-23 — the PNG path is open.** The operator supplied clean exports: an
icon at 936×1026 (content 586×678, 11.9% padding) and a wordmark at 4130×812, both RGBA, both
fully contained, both essentially speckle-free. **[P11] measures them in detail.** So P8 can
ship raster assets now and does not have to wait on the vectors.

The supplied SVGs (`xenotag_icon_vector.svg` plus three wordmark weights, in `dev/xenotag`) do
**not** match the concept art and must not be used as the asset source — that is P11's problem,
and it is now a want rather than a blocker.

**Square the icon on its solid bbox before generating anything.** The supplied canvas is
off-centre (padding L122 T189 R228 B159); generating sizes straight from it bakes the offset
into every icon.

**P9 — UI theme retoken. READY, with one substitution that is not optional.**

Current tokens (`index.html:9-12`) are a dark-blue theme with a cyan accent:

    --bg:#060d1a  --surface:#0c1828  --surface2:#122035  --border:#1a3554
    --text:#ddeeff --muted:#6ba3c8  --accent:#22d3ee
    --green:#34d399 --red:#f87171 --yellow:#fbbf24 --badge-bg:#122035

**Sage cannot be the accent as specified.** `#708675` on Charcoal is **4.44:1** — it misses AA
for body text by 0.06, where the cyan it replaces is 10.76:1. Adopting it unchanged is a
measurable accessibility regression in an app that just spent B1 fixing one. The fix is a
lightened sage that is still plainly the brand colour:

| candidate | on Charcoal | |
|---|---:|---|
| `#708675` (sheet Sage) | 4.44:1 | fails AA |
| `#7E9484` | 5.35:1 | AA |
| `#8CA292` | 6.38:1 | AA |
| **`#96AC9B`** | **7.18:1** | **AAA — recommended** |
| `#A8BCAC` | 8.66:1 | AAA |

Keep `#708675` for non-text fills and rules, where it is fine; use `#96AC9B` wherever the accent
carries text. That is "adjacent to the palette", which is what was asked for.

Proposed mapping, every value measured:

    --bg:#171B19        Charcoal
    --surface:#1C2320   Charcoal, raised     Bone on it 11.81:1 AAA
    --surface2:#203A30  Deep Forest          Bone on it  9.05:1 AAA
    --border:#2C3A33    Deep Forest, lifted  1.46:1 vs bg (current border is 1.56:1 — parity)
    --text:#E2DDCF      Bone                12.82:1 AAA  (current 16.43:1)
    --muted:#B7B2A6     Warm Gray            8.23:1 AAA  (current  7.13:1 — improves)
    --accent:#96AC9B    Sage, lightened      7.18:1 AAA  (current 10.76:1)
    --badge-bg:#203A30  Deep Forest

**Leave `--green` / `--red` / `--yellow` alone.** They are semantic status colours, not brand
colours, and re-tinting them toward sage would make success and failure harder to tell apart.
All three still clear AA on Charcoal as-is: green `#34d399` 9.05:1, yellow `#fbbf24` 10.42:1,
red `#f87171` 6.29:1.

**The retoken is not just the `:root` block, and that is where the Complexity 3 comes from.**
`index.html` has **173 `var()` usages but 82 hardcoded hex literals (46 distinct)** that bypass
the tokens entirely, and **`login.html` uses 12 hardcoded hexes and no tokens at all** — it does
not share the theme. So the work is: move login onto the shared tokens, then sweep the 46
hardcoded values. **The badge-colour swatches are the exception** — those render *badge* colours
and belong to P10, not here.

**P10 — badge palette. NEEDS DECISION, and the decision is a design one.**

Measured as badge fills against the shipped white label text (`badge_text_color: "#ffffff"`):

| brand colour | white text | |
|---|---:|---|
| Charcoal `#171B19` | 17.40:1 | AAA |
| Deep Forest `#203A30` | 12.29:1 | AAA |
| Sage `#708675` | 3.92:1 | **fails AA** |
| Warm Gray `#B7B2A6` | 2.11:1 | **fails** |
| Bone `#E2DDCF` | 1.36:1 | **fails** |

Deep Forest is an *excellent* badge fill — 12.29:1 beats all four shipped colours (9.37–10.95).
The problem is not contrast, it is **counting**: the overlay encodes four categories
(video/audio/sub/rating) by hue and the brand supplies **one** usable dark hue. Three ways out:

1. **Monochrome.** All four fills Deep Forest; the label text already names the category.
   Maximally on-brand, and it discards the at-a-glance cue that hue currently carries.
2. **Four brand-adjacent hues.** Anchor on Deep Forest, rotate hue, hold the dark desaturated
   character.
3. **Deep Forest fill plus a per-category Sage/Bone keyline.** Keeps one fill colour and moves
   the cue to an accent. Most work, and it touches `_pill_tile()`'s glow geometry, which B1 just
   settled — weigh that before choosing it.

**DECIDED 2026-09-23 — the colour coding stays.** The operator: *"colour coding the types of
pills was intentional so all green somewhat reverts that call."* So **(1) monochrome is out**,
and the badges are not obliged to adopt brand colours at all — *"the badges don't necessarily
need to recolour. Those are mostly user preference anyway."*

**Correction: my first proposal for (2) was wrong, and measuring it is what showed that.** I
proposed `#203A30` / `#1C3A42` / `#33401F` / `#2B3540` because all four clear AAA against white
text (12.29 / 12.12 / 11.09 / 12.46). Contrast was the wrong axis. Those four are dark,
desaturated and **at nearly identical lightness**, so they are separated by hue alone — and they
measure a worst-case **dE 0.6** across simulated vision types. **That is worse than the shipped
palette's 1.9**, which [B4] files as a defect. Hue-only separation in a near-monochrome palette
does not survive colour blindness.

**What actually works is staggering lightness, not rotating hue.** Both palettes below were found
by search under the constraints "AAA against white text" and "maximise the worst pairwise
CIEDE2000 across normal, protanopic, deuteranopic and tritanopic vision", and both were then
verified through `scripts/measure_palette_separation.py --config`:

| palette | video | audio | sub | rating | worst dE | worst contrast |
|---|---|---|---|---|---:|---:|
| shipped today | `#134e4a` | `#1e3a8a` | `#7c2d12` | `#4c1d95` | **1.9** | 9.37:1 |
| my first (2), withdrawn | `#203A30` | `#1C3A42` | `#33401F` | `#2B3540` | **0.6** | 11.09:1 |
| **A — hue-preserving** | `#245d59` | `#4e517e` | `#452a20` | `#382b47` | **12.1** | 7.49:1 |
| **B — brand-anchored** | `#203A30` | `#312c4c` | `#50532f` | `#73485b` | **12.3** | 7.48:1 |

**Recommend A.** It keeps the four hue families the operator deliberately chose — teal video,
indigo audio, brown subtitle, violet rating — and only desaturates them toward the brand's muted
character while staggering L\* so the difference survives without the red-green axis. It reads as
the same colour coding, six times further from collapse than what ships today. **B** anchors
`video` on true Deep Forest and reads more strongly as the brand, at the cost of shifting every
category's hue.

**Either is fine; doing nothing is also defensible** — the operator's framing makes these a
default, not a rule. But *doing nothing leaves [B4] in place*, so if the badges keep their
current colours, change `rating_badge_color` at minimum.

Traps, and the first is the one that will actually bite:

 - **Most existing deployments will not see a new palette at all.** B1 already recorded this and
   it applies verbatim: `save_config_from_dict()` dumps the whole model, so **any operator who
   has ever opened Settings and saved has all four old hexes written into `config.yml`**, and
   keeps them. A default-only recolour reaches new installs and nobody else. Decide explicitly
   whether this ships as a default change, a migration, or a prompt — and say which in the
   release notes.
 - **`tests/test_badge_contrast.py` pins `#134e4a`** (lines 173, 175, 190, 191) and asserts on
   *rendered pixels*. A recolour must update those and re-run
   `python3 scripts/measure_badge_contrast.py`. The probe exists so this is not a judgement call.
 - **The README already documents colours that do not exist.** It lists `image.video_badge_color`,
   `audio` and `sub` as defaulting to `"#1e3a5f"` (README:196-198); the real defaults are
   `#134e4a` / `#1e3a8a` / `#7c2d12`, and **`#1e3a5f` appears nowhere in the codebase**. This is
   wrong today, independently of the rebrand — fix it in the same pass, and check
   `config.example.yml:47-50` with it.
 - **This is [B2]'s use case.** B2 — warn on a low-contrast configured colour, beside the picker —
   would catch Sage-as-a-badge-fill at the moment of choosing. **Do B2 first** and P10 becomes
   safe to experiment with; do P10 first and the first operator who tries Sage gets a 3.92:1
   badge and no warning.
 - **[P6] is unaffected but should be told.** A background-aware palette needs a *pair* of
   palettes; if P10 lands monochrome, P6 has one fewer degree of freedom to work with.

**P11 — filed 2026-09-23. The bar is exact, and nothing on hand clears it.**

The operator, rejecting an attempted reconstruction: *"Needs to match concept logo and wordmark
exactly, not 'close enough'. Otherwise we can stick with pngs for now."* That settles the
standard. **An approximation of this mark is not a cheaper version of it, it is a different
mark** — so no parametric or hand-tuned redraw counts, and one was tried, rejected and removed.

**The supplied SVGs draw the wrong shape, and it is a structural error, not a tuning gap.**
`xenotag_icon_vector.svg` is two fat lens shapes in **mirror symmetry**, which reads as an eye.
The concept art is a **C2 pinwheel** — 180° rotational symmetry, slender tapered blades, generous
negative space. Those are different symmetry groups; no amount of nudging the control points on
these paths converges on the concept art. Two consequences:

 - **The same two paths are reused as the wordmark's `O`** at `scale(0.7)`, so the identical
   error appears in all four supplied files. One correct geometry fixes all four; one wrong
   geometry breaks all four.
 - **The letterforms are fine.** X/E/N/T/A/G are clean stroked paths — the distinctive stemless
   three-bar E and the dotted A both match the sheet. Only the `O` is wrong. Whoever does this
   should not redraw the lettering.

**The three weights are also inverted.** `primary` is `stroke-width="10"`, `medium` `11.5`,
`small` `13` — so the file named *small* is the **boldest** of the three, while the brand sheet
captions it *"reduced weight for tight spaces"*. One of the two is wrong. Optical sizing argues
the SVG is right and the caption is backwards (small renderings need more weight, not less), but
**that is the operator's call** and it is cheap either way: six `stroke-width` values per file.

**The PNG fallback is also blocked, which is the part that was not known.** Measured on the
supplied uploads, counting pixels with alpha > 128 along each border:

| asset | size | top | bottom | left | right | verdict |
|---|---|---:|---:|---:|---:|---|
| icon | 856×896 | 5.0% | 16.1% | 0.0% | **24.9%** | **clipped** |
| wordmark A | 1580×236 | 3.2% | 2.9% | 2.1% | 0.0% | no margin |
| wordmark B | 1644×236 | 2.9% | 2.8% | 1.7% | 0.0% | no margin |
| wordmark C | 1476×212 | 0.0% | 2.8% | 0.0% | 0.0% | no margin |

**A quarter of the icon's right edge is solid artwork running off the canvas.** The swirl is cut
off, so that PNG cannot ship as an icon at any size — padding it just centres a truncated mark.
The wordmarks are not clipped so much as *untrimmed*: the glyphs touch the canvas edge, leaving
no room for the margin a logo needs.

**And the only uncropped icon anywhere is too small.** The brand sheet's "ICON (STANDALONE)"
panel is **200×164 px** of actual image data. That is under the 256px favicon source and far
under a 512px app icon, so upscaling it is not an option either.

**What unblocks this, and it is one export, not a redraw:**

 1. **An uncropped icon at 1024×1024**, transparent, with ~10% padding on all four sides.
 2. **Wordmarks re-exported with margin**, at the three weights, transparent.

With (1) in hand, **the exact-match requirement becomes tractable by tracing rather than
drawing** — the blade silhouettes are flat two-colour regions, so an auto-trace of the alpha
plus re-application of the brand gradients reproduces the concept art *by construction* instead
of by eye. That is the recommended method, and it is why this item is Complexity 4 and not 2:
without (1) it is a redraw, with (1) it is a trace.

**UPDATE, same day — the operator supplied clean exports, and the PNG blocker is RESOLVED.**

| asset | canvas | content bbox | min padding | alpha | speckle |
|---|---|---|---:|---|---|
| icon | 936×1026 | 586×678 | 122px (**11.9%**) | RGBA, full range | **0 isolated px** |
| wordmark | 4130×812 | 3280×545 | 131px (**3.2%**) | RGBA, full range | **1 isolated px** |

Both are **fully contained** — no edge of either is clipped — and the alpha is clean enough to
trace: a 9×9 isolation test finds a single stray pixel across 690k solid pixels between them.
**So the PNG path is open and [P8] can ship**, and P11 reverts to what it always really was —
the vector work, wanted for its own sake rather than as the only route to an icon.

Two caveats for whoever traces:

 - **The alpha edge is a ~14px soft glow, not 1–2px antialiasing** (37.6k partial-alpha pixels
   against 2.6k edge crossings). That is *blur, not noise* — the 50% contour is well defined, so
   threshold at alpha 128 and the traced boundary is stable. Do not trace at a low threshold.
 - **The icon's content is 586×678 and off-centre** in its canvas (padding L122 T189 R228 B159).
   Square it on the solid bbox before generating any asset, or every icon size inherits the
   offset. 678px is comfortable for a 512 app icon and below; it is *not* a 1024 source.

**The original ask for a 1024×1024 export still stands for P11's benefit** — a larger source
makes the trace easier to verify against — but it is no longer blocking anything.

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
