# Xenotag Roadmap

Items are scored **Value** (1–5: user/correctness impact) and **Complexity** (1–5: implementation effort).
Easy wins = high value, low complexity. Categories: **B** = bug/correctness, **U** = user-facing
behavior, **I** = infrastructure, **P** = polish/UX.

**Readiness** (adopted 2026-09-22, matching van1sh/abtidy/sightline): every item is
**READY** (spec is complete, can be built as written), **NEEDS DECISION** (a human choice is
open — never start one), or **NEEDS MEASUREMENT** (a number has to be taken first; measure,
record, re-label — do not implement in the same pass). Finished items carry **FIXED /
SHIPPED / LIVE** (built here) or **CLOSED** (not built: premise wrong, superseded, or declined).

**BLOCKED on \<ID\>** (added 2026-09-23): the spec is settled and no human choice is open, but
another item must land first. Distinct from NEEDS DECISION, where the holdup is a person, and
from NEEDS MEASUREMENT, where it is a number — here the holdup is *another item*, so the thing
to do is go work on that one.

**Readiness sweep, 2026-09-26.** Every open item now carries exactly one label — there are no
unassessed items left, including the far-term tables. The per-item reasons are in each item's
note (search "Sweep 2026-09-26"); a NEEDS DECISION item states its question, the options and a
recommendation there, and a NEEDS MEASUREMENT item states what to measure, on what, and roughly
how long. Five items turned out to be **already built** (U3, U4, P3, I4) or **decided away**
(U8) and are relabelled rather than left open. **The operator took the sweep's recommendation on all 16 of its questions on 2026-09-26**; each
is recorded as an *OPERATOR DECISION 2026-09-26* line under the item's sweep note.

**Tier 0 comes first.** Correctness defects outrank features regardless of Value score.

---

## Tier 0 — Correctness

| ID | Defect | Value | Complexity | Readiness | Issue |
|----|--------|:-----:|:----------:|-----------|-------|
| B1 | **Badge contrast is roughly half what the config claims.** `ImageConfig` annotates each badge colour "verified WCAG AAA ≥7:1 against white text" — true of the opaque hex, but not of what renders. | 5 | 2 | **FIXED 2026-09-22** | — |
| B2 | **A configured badge colour is never checked for contrast.** Any hex the settings UI or `config.yml` supplies is used as-is; the live deployment's palette renders at 2.6:1. | 4 | 2 | **FIXED 2026-09-25** | — |
| B3 | **`_PILL_CACHE`'s key omits the padding.** Two poster widths can agree on `font_size` and disagree on `pad_h`/`pad_v`, so the first one rendered supplies the tile for both. | 2 | 1 | **FIXED 2026-09-24** | — |
| B4 | **Two shipped badge colours are the same colour to a colour-blind viewer.** `audio` and `rating` separate by CIEDE2000 **1.9** under deuteranopia — below the threshold at which they differ at all. | 3 | 1 | **FIXED 2026-09-24** | — |
| B5 | **Nothing has ever been written to Sonarr or Radarr.** `_find_arr_id()` reads `ProviderIds["Sonarr"]`/`["Radarr"]`, a key Jellyfin does not set on any of the 9,414 items — so the \*arr tag write and the \*arr certification fallback are both dead code in production. | 4 | 3 | **LIVE 2026-09-26** (v1.7.0) — all five instances written and read back | — |
| B6 | **A colour that is not six-digit hex renders BLACK, silently.** `ImageConfig` accepts any string and `_parse_color()` returns `(0, 0, 0)` for anything but `#rrggbb` — so a hand-edited `badge_text_color: "#fff"` paints black labels on dark badges. | 3 | 1 | **FIXED 2026-09-27; LIVE (v1.9.0)** | — |
| B7 | **An unmapped audio language becomes its first two characters.** `_lang3_to_lang2()` falls back to `lang3[:2].upper()`, so a malformed tag gives `xt-"E` and `zxx`/`khm`/`per` give `ZX`/`KH`/`PE` — tags that name no language, or the wrong one. | 2 | 1 | **FIXED 2026-09-27; LIVE (v1.9.0)** — production re-tagged 2026-09-27 11:41–12:52Z and read back | — |
| B8 | **A Sonarr/Radarr webhook processes the wrong item; a Jellyfin one processes none.** `find_item_by_provider_id()` filters with `AnyProviderIdEquals`, which Jellyfin 10.11.10 ignores (it returns the whole library, first item first); `get_item_by_id()` requests no `Path`. **Measured 2026-09-27:** resolve by FOLDER, no provider-id fallback. **Value is nil today** — no \*arr has a webhook and Jellyfin has no webhook plugin (U11). | 3 | 2 | **SHIPPED 2026-10-06** ([#117](https://github.com/bpoulliot/xenotag/pull/117)) · **RELEASED v1.11.0** (2026-10-07) | — |
| B9 | **Radarr refuses xenotag's commonest codec labels.** Radarr 6.3 accepts only `[a-z0-9-]` in a tag label, so `xt-h.264`, `xt-h.265`, `xt-dd+` (and `xt-hdr10+`, `xt-truehd atmos`) can never be created there — 4,589 label applications in the B5 dry run. | 3 | 2 | **FIXED 2026-09-27; LIVE (v1.8.0)** — tags respelled (`xt-H264`, `xt-DDplus`), badges unchanged; production re-tagged 2026-09-27 09:00–10:15Z | — |
| B10 | **Setting the tags to top-left drew them over the content rating.** The rating was hardwired to top-left in `render_badge_groups()` and nothing consulted `badge_position`, so the two landed on the same spot — and the README said the rating was always *top-right*. | 4 | 2 | **FIXED 2026-09-25** | — |
| B11 | **The \*arr dry run predicts writes no scan will make.** `run_arr_dry_run()` computes each owned item's tags from its `state.db` row, but `_run_scan()` skips `no_path`/`no_file`/`probe_failed` items before tagging — so their rows are stale and a live scan writes nothing for them. On 2026-09-26, 9 of sonarr/general's 1,061 "would change" were series whose folders hold **no video file at all**. | 2 | 1 | **FIXED 2026-09-26** | — |
| B12(a) | **U9's drift check and B17's read-back compare Jellyfin tags case-sensitively; Jellyfin does not.** Split from B12 on 2026-10-09. Since B5 the \*arr NFO merge respells 82% of items in lowercase (`xt-aac` for `xt-AAC` — no loss, Jellyfin's `Tags=` ignores case), so `_tag_drift()` warns on every one at each full scan: scan 158 logged 6,704 drift lines, 6,700 case-only. | 3 | 1 | **SHIPPED 2026-10-10** ([#146](https://github.com/bpoulliot/xenotag/pull/146)), merged NOT released — `_tag_drift()` and B17's read-back now compare casefolded tag sets; a match still records the tags that were sent. The next release's first full scan should log roughly 0 case-only drift lines (scan 158: 6,700) | — |
| B12(b) | **Jellyfin loses xenotag's tags and nothing notices** (the original B12). A replace-all metadata refresh (~8,000 items on 2026-09-04) or an item re-created by Jellyfin drops the `xt-` tags, and the mtime-driven scan never goes back to an unchanged file. | 3 | 2 | **READY** (decided 2026-10-09, third round): a reconciliation pass on its own schedule `scan.reconcile_schedule`, default daily `0 5 * * *` (after the 03:00 scan), plus a **manual rescan** option; **Jellyfin only** (the \*arrs are not written by it); **mass-write guard** — a per-run write threshold (default 500, setting beside `scan.reconcile_schedule`): past it the pass writes **nothing**, notifies with the count and a sample, and waits for a manual rescan, which bypasses the threshold. Builds after B12(a). Measured 2026-10-07, [docs/measurements/b12-tag-loss.md](docs/measurements/b12-tag-loss.md) | — |
| B13 | **A cropped 2160p file is tagged `1080p`.** `_detect_resolution()` compares the video WIDTH alone against exact thresholds (`>= 3840` → 4K), so a scope crop (3836×1604) or an open-matte crop (3584×2160) falls to `1080p` — 2 of the 69 films on radarr/4k. The same rule puts 1080-line crops at `720p`: **measured 2026-09-27, 639 of the 1,059 items tagged `720p` are 1080-line or near-1920 sources.** | 4 | 2 | **SHIPPED 2026-10-06** ([#122](https://github.com/bpoulliot/xenotag/pull/122)) · **RELEASED v1.11.0** (2026-10-07) — its first scan was the full re-tag with live \*arr writes (see *Release v1.11.0* below) | — |
| B14 | **The badge preview shows labels no poster gets.** The Preview page's sample profiles pass audio and subtitle labels language-first (`EN DTS-HD`, `EN PGS`) and a bare rating (`PG-13`) straight to `generate_preview_bytes()`, while a scan builds them codec-first and prefixed (`DTS-HD EN`, `PGS EN JA`, `Rated PG-13`) in `pipeline._make_badge_groups()` — so the preview under-states pill widths and never shows the grouping the README advertises. | 2 | 2 | **SHIPPED 2026-10-06** ([#116](https://github.com/bpoulliot/xenotag/pull/116)) · **RELEASED v1.11.0** (2026-10-07) | — |
| B15 | **Without bcrypt, the admin password is stored as unsalted SHA-256 — and every existing bcrypt login fails.** `app/auth.py` falls back to `hashlib.sha256` when `import bcrypt` fails (CodeQL #6, #7). Latent: the image pins and imports bcrypt 5.0.0, and prod and dev both hold `$2b$` hashes. | 2 | 1 | **SHIPPED 2026-10-06** ([#115](https://github.com/bpoulliot/xenotag/pull/115)) · **RELEASED v1.11.0** (2026-10-07) | — |
| B16 | **The first-run admin password is written to the container log.** With no `XENOTAG_PASSWORD`, `bootstrap()` logs the generated password at WARNING (CodeQL #8), and it stays a working credential until changed — readable by anyone with `docker logs`, Dozzle or Portainer. | 2 | 1 | **SHIPPED 2026-10-06** ([#125](https://github.com/bpoulliot/xenotag/pull/125)) · **RELEASED v1.11.0** (2026-10-07) | — |
| B17 | **A Jellyfin tag write that does not stick is recorded as applied.** In B9's re-tag Jellyfin undid 22 of 9,340 writes — it re-saved the item with its old tags 60–700 ms after the refresh xenotag requests right after writing — and `state.db` recorded all 22 as tagged; a write that raises (8 client timeouts) is recorded the same way. Incremental scans never retry either. | 3 | 2 | **SHIPPED 2026-10-06** ([#121](https://github.com/bpoulliot/xenotag/pull/121)) · **RELEASED v1.11.0** (2026-10-07) — read-back counts in *Release v1.11.0* below | — |
| B18 | **xenotag reads current Jellyfin tags from a listing that can be stale.** Production's recursive `/Items` listing served pre-re-tag `Tags` for all 6,366 items the re-tag changed, while `jellyfin.db` and `/Items?Ids=` were current; `set_managed_tags()` keeps the non-managed tags from that listing, and every read-back through it is blind. | 3 | 1 | **SHIPPED 2026-10-06** ([#114](https://github.com/bpoulliot/xenotag/pull/114)) · **RELEASED v1.11.0** (2026-10-07) | — |
| B19 | **Three ISO 639-1 codes spell another tag.** Since B7 a language tag is its ISO 639-1 code, and Sindhi is `SD` (= the resolution tag), Divehi `DV` (= Dolby Vision) and South Ndebele `NR` (= the rating). Latent: no stream in production's Jellyfin has any of the three. | 1 | 1 | **CLOSED (decided 2026-10-05)** — accept the overlap; the pinning test stays | — |
| B20 | **An OGM file's `English[eng]` language tag is tagged `UND`.** B7's decided rule sends anything that is not 2–3 ASCII letters to `UND` (with a WARNING); ffprobe reports three old `.ogm` anime series' tracks as `English[eng]`, `Japanese[jpn]`, `English`, `Japanese`, which the old first-two-letters rule got right by luck. Those 3 series lose `EN`/`JA`/`dual-audio`/`sub-EN` at the next re-tag. | 2 | 1 | **SHIPPED 2026-10-06** ([#123](https://github.com/bpoulliot/xenotag/pull/123)) · **RELEASED v1.11.0** (2026-10-07) — `Name[xxx]` reads the code; *Tenchi in Tokyo* only partly fixed (25 bare-name episodes stay `UND`) | — |
| B21 | **Below 100% opacity the poster is not the badge the Settings chips measure.** `_render_group()` pastes each pill tile with itself as the mask, which squares its alpha and premultiplies its RGB: the poster gets the fill at a³ over (1 − a²) of the poster, while B1's instrument — and B2's chips — model a. At 80% the chip says the rating badge is 4.52:1 (AA) on a white poster; the poster renders 3.60:1. The glow is hit at every opacity, 100% included. | 4 | 2 | **SHIPPED 2026-10-06** ([#124](https://github.com/bpoulliot/xenotag/pull/124)) · **RELEASED v1.11.0** (2026-10-07) — option (b); 100% posters byte-identical | — |
| B22 | **An exception that escapes a scan holds the scan lock until restart.** `run_full_scan()` / `run_incremental_scan()` take `progress.try_start()`, and only `_run_scan()`'s own exits call `progress.finish()` — so anything it raises leaves `progress.running` True, and every later scan, scheduled or manual, logs `Scan already in progress, skipping` and does nothing. | 2 | 1 | **SHIPPED 2026-10-06** ([#113](https://github.com/bpoulliot/xenotag/pull/113)) · **RELEASED v1.11.0** (2026-10-07) | — |
| B23 | **The badge preview ignores the poster destinations.** `preview_image()` builds its groups with the default `TagDestinations`, so a category whose saved `tags.destinations` drops `poster` still shows pills on the Preview page that no scan paints. Latent: production's `config.yml` keeps `poster` in all four lists. | 1 | 1 | **SHIPPED 2026-10-06** ([#129](https://github.com/bpoulliot/xenotag/pull/129)) · **RELEASED v1.11.0** (2026-10-07) — `preview_image()` now builds its `AppConfig` with `tags=get_config().tags`; default (all `poster`) stays byte-identical | — |
| B24 | **`/health` reports Jellyfin unreachable without ever contacting it — and the container healthcheck only ever reads that answer.** For a caller with no web session `health()` returns early with a *fabricated* verdict, `jellyfin={"ok": False, "status": "unreachable", "message": "Not authenticated"}` (`app/web/routes.py:175-183`), so a perfectly healthy Jellyfin is reported as down; the message describes the **caller's** missing cookie, not the dependency. `docker-compose.yml:30-34` sets the healthcheck to `curl -f http://localhost:7755/health`, which carries no cookie, so it always takes that branch — and because the branch still returns **HTTP 200 with `status: "ok"`**, `curl -f` can never fail on a dependency problem. The one endpoint whose job is to report dependency health is structurally unable to: unauthenticated it invents the answer, and authenticated it hardcodes `status="ok"` (`:195`) whatever `jf.health()` returned. **Measured 2026-10-06** while verifying the Jellyfin 10.11→12.2 upgrade (`~/docker` TODO I62): prod xenotag reported `jellyfin: unreachable / Not authenticated` while its configured API key was byte-identical to Jellyfin's, `jellyfin:8096` answered 200 from inside the container, and tags read back correctly by `Ids=` — it cost real diagnosis time and briefly looked like an upgrade regression. Fix: either exempt `/health` from the session gate (it leaks only up/down, and the port is bound to `127.0.0.1`) or give the probe its own unauthenticated liveness route and keep the authenticated one for the UI; in both cases report an **unknown** dependency as unknown rather than as `unreachable`, and let `status` follow the checks. | 3 | 1 | **SHIPPED 2026-10-07** ([#132](https://github.com/bpoulliot/xenotag/pull/132)) · **RELEASED v1.11.0** (2026-10-07) — exempted from the session gate; unauthenticated callers get `status: "up"` only, authenticated callers keep full detail with `status` computed from the real checks | — |
| B25 | **An \*arr `api_key_file` can name any file the container can read** (CodeQL #11, `py/path-injection`, `app/config.py:538`). `_apply_arr_key_files()` reads whatever path the row names and sends the contents as that instance's `X-Api-Key`, to a URL the same row sets. Only an admin can set it (config.yml / raw YAML editor). | 2 | 1 | **READY** (operator decided 2026-10-09: allowed roots = `/run/secrets` **only**, option (a)) — fixes CodeQL #11 | — |
| B26 | **On Jellyfin 12.2 the scan never sees a film that sits in a collection.** `JellyfinClient._fetch_items()` (the scan, and B8's folder lookup) and U2's `complete_listing()` ask `/Items?Recursive=true&IncludeItemTypes=Movie,Series` without `CollapseBoxSetItems`. Production Jellyfin 12.2.0 answers with collections collapsed: **8,766 items — 448 BoxSets in place of 1,134 films** — where `CollapseBoxSetItems=false` returns all 9,452 (10.11.10 listed 9,450 on 2026-10-06). The 1,134 films are never re-tagged or re-rendered, the BoxSets become `skip (no_file)` scan errors, and U2 judges the films' folders empty: **switching U2 to `remove` would strip the managed tags of 82 radarr/general objects whose film is live.** | 4 | 1 | **SHIPPED 2026-10-10** ([#143](https://github.com/bpoulliot/xenotag/pull/143)) · **RELEASED v1.11.1** (2026-10-10) — `CollapseBoxSetItems=false` always sent at all four sites, no setting. The post-release full scan (scan 163) listed **9,467** items, 0 BoxSets, and reached all 1,134 hidden films; its deleted-items report shows **strip 0 objects / 0 tags** on every instance — see *Release v1.11.1* | — |
| B27 | **A Settings save blanks the session-signing key.** The Settings page sends `auth: {username}` only, and `PUT /api/settings` restores `password_hash` but not `secret_key`, so every structured save writes `auth.secret_key: ""` to `config.yml` and into the running config. Until the next restart, sessions are signed with `":" + password_hash[:16]` (a constant prefix and 9 bcrypt-salt characters) in place of the 256-bit key. Separately, `GET /api/settings` returns `auth.secret_key` to the browser. Found in production on 2026-10-10. | 3 | 1 | **READY** — see *B27* below | — |

**Build order and release gate, recorded 2026-10-05.** The READY items with no open NEEDS DECISION
sequence as B22, B18, B15, B14, B8, I11, P4a; B17 (relabelled READY below) builds after B18, since
its fix reads the item back by `Ids=` (B18, below). **B21, B13 and B20 (also relabelled READY
below) must merge before the next release** (B20 merged #123, B21 merged #124, 2026-10-06): B13's rule change folds into `_tag_config_hash()`, so
the first scan after it is a forced full re-tag with live `*arr` writes (as B9's and B7's were) —
the release note must say so. *Released together in v1.11.0 on 2026-10-07 — see below.*

**Release v1.11.0 — LIVE 2026-10-07 (the overnight release session).** `bump=minor` from `main`
`0da1c31` (CI green) → release commit `b3858bc`, tag `v1.11.0`, image
`ghcr.io/bpoulliot/xenotag:1.11.0` (= `:latest`), deployed 03:19:50Z. It carried B8, B13, B14,
B15, B16, B17, B18, B20, B21, B22, B23, B24, I9, I11, P4a, P4b, P7 containment, U5, U9's drift
half and the CodeQL #10 change (#135). **Not in it:** P6 (#134, HOLD) and CodeQL #11's fix (B25,
#136, needs a decision). **CodeQL alerts #10 and #11 are both still open on `main` at `b3858bc`**
— #135 did not clear #10 in CodeQL's own analysis; neither alert was touched.
- *Deploy.* `state.db` backed up after a clean stop as `state.db.bak-20261007-pre-release` (WAL
  checkpointed); `:1.10.0` is still pulled for a back-out. Startup logged `state.db schema:
  upgraded (revision 0002)`; healthy, no restarts. **B24:** `/health` without a session answers
  `{"status":"up"}`, with one `ok` and every dependency `healthy`. **I1:** see I1's own note.
- *The forced full re-tag — scan 158* (`full`, 03:27:39Z → 05:07:02Z): 8,766 listed, 8,304 tagged,
  8,256 posters. **\*arr: 684 objects written, 0 write errors, 0 read-back failures, not halted**;
  the only new label is `xt-interlaced` (on three instances). **B17:**
  `xenotag_tag_writeback_mismatch_total` fixed **219**, unresolved **0** — Jellyfin undid 2.6 % of
  the writes (B9's re-tag on 10.11.10: 22 of 9,340) and the one retry fixed every one. **B13:** 660
  Jellyfin items changed class — exactly the 658 predicted movers the scan could reach, plus 2 that
  had no class — and 659 \*arr objects; the other 89 of the 747 are films B26 hides. **U9:** 6,704
  drift warnings, 6,700 of them case-only (B12) and 4 real, all known (B12's re-created *Jimmy
  Carr* item, B17's two `xt-H.264` films, *Frontier War*) and repaired by the write. **U5:**
  `xt-interlaced` on 22 rows. Scan errors: 451 `no_file` (448 of them BoxSets — B26), 11
  `probe_failed` (all on `xtor`, under a host load of ~60). Tag-config hash `56db14293fe03fc6` →
  `f57e91dbac6fbe70`.
- *Library read-back* (GET-only snapshots of all five \*arrs and of every Jellyfin item by `Ids=`,
  before and after; `b9verify.py`): 0 operator tags and 0 non-tag fields changed on any re-tagged
  \*arr object, 0 labels deleted or renamed. On Jellyfin no keyword was lost, but **64 keywords
  ending in a space or a no-break space came back trimmed**: Jellyfin 12.2 trims tags on save
  (xenotag sends them back unchanged; B9's re-tag on 10.11.10 saw no such change). Two series
  whose probe failed still carry a pre-B9 `xt-DD+`.
- *U2's report-mode pass* in the same scan: 2,331 candidates, 1,197 confirmed deleted, **1,134
  answered by id** (B26's hidden films) and **87 rows / 82 radarr/general objects marked STRIP**
  — every one a live film the listing hides. Nothing was written (53 GETs).
- Record: `~/docker/xenotag/release-1.11.0-20261007/README.md` (snapshots, verify output, scan log,
  the tools).

**Release v1.11.1 — LIVE 2026-10-10 (the overnight release session; B26 only).** `bump=patch` from
`main` `76efb97` (CI green) → release commit `fff764a`, tag `v1.11.1`, image
`ghcr.io/bpoulliot/xenotag:1.11.1` (= `:latest`), deployed 15:31:16Z. The operator authorised the
sequence on 2026-10-09: B26 alone, release, deploy, one full scan. Since v1.11.0 the only code on
`main` was B26's: `app/clients/jellyfin.py` and `app/deleted_items.py`, and every other commit was
ROADMAP-only. The release had no Alembic revision and no `TAG_VOCABULARY` change, and the
tag-config hash stayed `f57e91dbac6fbe70`. Nothing was rolled back.
- *Deploy.* `state.db` was backed up after a clean stop as `state.db.bak-20261010-pre-1.11.1`
  (10,660 rows, WAL checkpointed, `integrity_check` ok). `:1.11.0` is still pulled for a back-out.
  Startup logged `state.db schema: current (revision 0002)`; the container came up healthy with no
  restarts. The backup restart regenerated `auth.secret_key`, because a Settings save earlier that
  day had blanked it (filed as **B27**). `arr_sync` (`live`) and `deleted_items` (`report`) were not
  changed.
- *Before the scan* (GET only): the default listing holds **8,781** items (448 BoxSets);
  `CollapseBoxSetItems=false` returns **9,467** (6,984 films, 2,483 series, 0 BoxSets), so the default
  hides **1,134** films. The scan's own `get_items()` over its 17 libraries now returns those 9,467.
- *The full scan, scan 163* (`full`, 15:33:50Z → 17:07:21Z, 94 min): **9,467 listed (0 BoxSets)**,
  9,456 tagged, 9,408 posters. **All 1,134 formerly hidden films** have an index row refreshed by
  this scan, with a poster path; before the deploy their newest row dated from 2026-10-06.
  Scan errors: `no_file` **3** (all under `/media/multi`, which is not mounted; **the 448 BoxSet
  errors are gone**) and `probe_failed` 8 (all on `xtor`).
  - **\*arr:** **95 objects written** (radarr/general 91, sonarr/general 4), **0 write errors,
    0 read-back failures, not halted**, 0 labels created.
  - **B17:** `xenotag_tag_writeback_mismatch_total` fixed **109**, unresolved **1**. The unresolved
    one is *Spectre*: Jellyfin keeps its lowercase NFO spelling, a case-only difference (B12).
  - **B13:** **89 of 89** predicted movers among the hidden films moved to their predicted class
    (81 `720p→1080p`, 8 `480p→720p`), with 90 \*arr resolution relabels. All 747 of B13's movers
    are now done.
  - **U9:** 2,838 drift warnings. 2,835 are case-only (B12; expected until B12(a) ships), and the
    3 real ones were repaired by the write.
  - **Other warnings:** 12 Jellyfin tag-write timeouts on very large series (not recorded, so the
    next scan retries them) and 6 `folder.jpg` permission errors.
- *Library read-back* (GET-only snapshots before and after; `b9verify.py`): on every instance the
  objects whose tags changed equal the report's `written`. 0 operator tags and 0 non-tag fields
  changed on any written object.
  - Two sonarr/anime labels (`xt-de`, `xt-re-encode`) disappeared. Both had **0** users before the
    scan, and xenotag sent no `DELETE`, so this is Sonarr's unused-tag housekeeping.
  - 11 Jellyfin keywords came back trimmed of a trailing space or no-break space. This is
    Jellyfin 12.2 trimming on save, as recorded for v1.11.0.
- *U2's report-mode pass* in the same scan (`status: ok`, 42 GETs, nothing written):
  - the listing is 9,467, and so are its total and its recount;
  - **1,198 candidates, 1,198 confirmed deleted, 0 answered by id** (scan 158 had 1,134);
  - fraction **0.1124** against the 0.15 bound;
  - **strip_objects 0 / strip_tags 0 on all five instances** (scan 158 had 87 rows,
    82 objects and 330 tags).
- Record: `~/docker/xenotag/release-1.11.1-20261010/README.md` (snapshots, verify output, scan log,
  the tools).

**OPERATOR DECISIONS 2026-10-09 (round 5) — release, dependencies, I9, P4, I1, GitHub issues.**

- **Release 1.12.0 — queued for tonight, last in the overnight chain.** A minor release of whatever has landed of
  B12(a), B12(b), B25 and P6 (plus any Dependabot bumps merged before it). Skip-and-release-what-landed: an item that
  did not merge is left out, but **B12(b) never ships without its threshold guard** — if any of its code is on `main`
  without the guard, the release stops. When B12(b) ships, the release first adds its Alertmanager rule to the host's
  `monitoring/prometheus/rules/xenotag.yml` through a `~/docker` worktree and a Gitea PR (never the main `~/docker`
  checkout); activating it in the live checkout is the operator's step. No forced re-tag (none of the four is expected to change
  the tag-config hash; the release checks it and stops if it does), no scan run by the release. B25 is safe to release: production's `config.yml` names no
  `api_key_file`. Backup, deploy, verify and rollback follow the v1.11.0 release.
- **Dependabot PRs #103–#110 — triage authorised and queued before the release**, so 1.12.0 carries the bumps that
  pass. Per-PR rules: patch/minor pip bumps merge when green on a rebased head with no new warnings; SQLAlchemy
  2.0→2.1 (#105) also needs `python -m app.migrate check` and a clean `scripts/verify_state_db_upgrade.py` run on a
  production copy; action bumps that edit `release.yml`/`docker-publish.yml` (#109, #110) stay open unless every
  workflow they touch ran green on the PR, so an unverified workflow cannot reach the release.
- **I9 (move the five \*arr keys out of `config.yml`) — an interactive session with the operator, after 1.12.0.**
  Not an overnight item: it touches compose, secret files and `~/docker/SECRETS.md`. Since B25, key files must sit
  under `/run/secrets`.
- **P4 — the post-feature phone-width audit runs after P6 and B12(b) merge** (P4a/P4b harness; the media browser
  and the `.health-grid` tile truncation), and lays out the options with screenshots; the operator then picks the
  layout. Queued, measurement only.
- **I1 — the browser Settings save through Authentik is an operator action** (one save at
  `https://xenotag.bitmapserv.org`, then `docker logs xenotag | grep Rejected`); no session owes it.
- **GitHub issues closed 2026-10-09**, each with a comment linking its row here: #10 (P2, shipped), #18 (I2), #20
  (I7), #21 (P1), #24 (U5, interlacing only), #25 (U6). Left open: #16 (deferred), #26 (P4), #36 (U2).
- *Recorded for the `~/docker` backlog:* the image-registry question (`~/docker` TODO I59, decision 4: ghcr, Gitea
  or both for xenotag) is **measure first**; the measurement is queued and will file its own item here.

**B25 — FILED 2026-10-06 by the CodeQL #11 item, which stopped before building. NEEDS DECISION.**
The item said to allow only the directories that production's compose file mounts for key files,
not to guess one, and to stop if the mounts were unclear. They are clear, but there are none:

* **The prod container** (`docker inspect xenotag`, mounts only) mounts `/config` and seven
  `/media/*` roots. It has no `/run/secrets` and no compose `secrets:`, and its environment holds
  only `PUID`/`PGID`/`TZ`. **The prod `config.yml` contains no `api_key_file`** (grep for the key name,
  no match), and the host's `materialize-secrets.sh` renders nothing for xenotag. I9 is merged but not
  wired up in production (as recorded under I9), so a root allowlist breaks no live key today.
* **This repo's `docker-compose.yml`** also mounts only `/config` and media. Its `_FILE` lines are
  commented out and point at `/run/secrets/…`.
* **The documentation names one place:** the README (two examples, and the `secrets:` block it
  shows), `config.example.yml` and the I9 entry all use `/run/secrets/<name>`, which is Compose's
  default secret target. Across the host stack (`docker compose config --format json` in
  `~/docker`), 19 secret mounts in 9 services all land under `/run/secrets/`. The only other secret
  directory is one bind mount at `/etc/prometheus/secrets`.

Taken literally, "what prod mounts" means an **empty** allowlist. That refuses every
`api_key_file` and switches off I9, which the operator chose on 2026-09-26. The only grounded
alternative comes from the documentation, not from a mount, and choosing it is exactly the guess
the item forbade. So the decision goes to the operator:

* **(a) `/run/secrets` only.** Recommended. It is the documented convention and Compose's default,
  and nothing in prod breaks. Cost: a Compose long-syntax secret with an absolute `target:`
  elsewhere, or a bind-mounted key directory, is refused. The fix is to mount it under
  `/run/secrets`.
* **(b) `/run/secrets` plus roots added by an environment variable** (for example
  `XENOTAG_KEY_FILE_ROOTS`). The web UI cannot set the environment, so the admin path stays closed.
  Cost: one more setting and one more README section.
* **Considered and not recommended:** allowing `/config`. A key file there sits beside
  `config.yml`, in the directory the app writes and backs up, and keeping keys out of that
  directory is why I9 exists.

**Found while checking. The build needs this, and it changes nothing above:** the only code that
reads the path is `_apply_arr_key_files()`. `_drop_file_backed_keys()` checks only that the field
is set, and the Settings **Test** route (`app/web/routes.py`) compares the path as a string against
the configured instances. Neither opens the file. I8's `<VAR>_FILE` reader (`_read_env_value()`)
takes its path from the environment, not from config. CodeQL did not flag it, and putting it under
the same root would change I8, so that is not part of this item. Once the operator decides, the
build is: resolve the path (symlinks and `..`) before any read, refuse anything outside a root with
a `ConfigError` that names the path, and keep unreadable or empty as fatal. Tests: a path outside
the roots is refused and never read; a symlink inside a root that points outside is refused; a file
inside a root loads as it does today. The 29 tests in `tests/test_arr_key_files.py` use `tmp_path`
key files, so they need the root pointed at `tmp_path`.

**OPERATOR DECISION 2026-10-09: (a) — the allowed `api_key_file` root is `/run/secrets` ONLY. Relabelled
READY.** (b)'s environment-variable roots and `/config` are both declined. The build is the paragraph
above with the root fixed: resolve the path (symlinks and `..`) before any read, refuse anything that
does not resolve under `/run/secrets` with a `ConfigError` naming the path, keep unreadable/empty FATAL.
The tests point the root at `tmp_path` by monkeypatching a module constant — not through an environment
variable, which would be (b) by the back door. **Release note:** an `api_key_file` outside `/run/secrets`
becomes a fatal start error; production uses none today (I9 is not wired up), so nothing breaks. This
note and the B25 row were filed in PR #136 and brought to `main` with the decision on 2026-10-09.

**B26 — FILED 2026-10-07 by the v1.11.0 release session (measured on production, GET only). READY.**
B25 is claimed by the open PR #136 (CodeQL #11), so this takes B26.

- *Measured* on production Jellyfin 12.2.0 with xenotag's API key, through `ReadOnlyTransport` (0
  blocked): `/Items?Recursive=true&IncludeItemTypes=Movie,Series` has `TotalRecordCount` **8,766**
  with no `CollapseBoxSetItems` and with `=true`, and **9,452** with `=false`. The default listing
  holds 5,844 films, 2,474 series and **448 `BoxSet`s**; the `=false` listing holds 6,978 films and
  2,474 series, no BoxSet, and all **1,134** films the default lacks. Every one of the 1,134 answers
  a lookup by `Ids=` (24 GETs), so they exist. The 17 configured libraries, listed one by one as
  the scan lists them, union to the same 8,766.
- *Since when.* U2's report at 2026-10-06 09:01Z, on 10.11.10, listed 9,450 items and confirmed
  1,197 index rows deleted. On 12.2.0 the same index has 2,331 rows missing from the listing:
  the same 1,197 still confirmed deleted, plus these 1,134 films. Production moved to 12.2.0 on
  2026-10-06 (`~/docker` TODO I62). **Not determined:** whether collapsing is 12.2's default for an
  API-key caller or follows a server or user grouping setting. The fix is the same either way.
- *Effect on the v1.11.0 re-tag.* That scan's input was the collapsed listing, so the 1,134 films
  kept their pre-release tags and posters — 89 of B13's 747 movers among them (B13's 2026-10-06
  records). The tag-config hash is now the new one, so **no incremental scan will reach them**:
  they need one full scan after the fix ships. The 448 BoxSets were logged as `skip (no_file)`
  scan errors (their `Path` is under Jellyfin's own `/config/data`, which xenotag does not mount);
  nothing was written for them.
- *Effect on U2.* `DeletedItemsPass.plan()` keeps a deleted row's \*arr object only if a LISTED
  item lives in its folder. 87 of the 1,197 confirmed-deleted rows sit in a hidden film's folder,
  so in `remove` mode their **82 radarr/general objects would lose their managed tags although the
  film is live** (computed from the pre-release index, the by-id snapshot and a GET-only \*arr
  snapshot; the v1.11.0 scan's own report-mode pass is quoted in *Release v1.11.0* above). In
  `report` mode nothing is written. **U2 removal must not be switched on before B26 is released.**
  B8's webhook folder lookup (`list_item_paths()`) goes through the same listing; no webhook is
  configured today.
- *Fix.* Send `CollapseBoxSetItems=false` from `_fetch_items()` and from `complete_listing()` —
  its pages AND its recount — with a test whose fake Jellyfin collapses unless asked not to.
  Check that dev (12.1.0) and the 10.11.10 lab image honour it. After the release that carries it,
  run one full scan (`POST /scan/full`) to reach the 1,134 films.

**B26 — the operator's question, answered 2026-10-09: "is this an extra API param? Can be turned off if
setting."** Yes, it is one query parameter, and it should not be a setting. Measured on production
Jellyfin 12.2.0 by GET only (xenotag's own client from inside the `xenotag` container), 2026-10-09:

| listing (`Recursive=true`) | default | `CollapseBoxSetItems=false` |
|---|---:|---:|
| `IncludeItemTypes=Movie,Series` | 8,779 (incl. BoxSets) | 9,465: 6,983 Movie + 2,482 Series, 0 BoxSet, 0 duplicate Ids |
| `IncludeItemTypes=Movie` | 6,297 = 5,849 Movie + **448 BoxSet** | 6,983 |
| `IncludeItemTypes=Series` | 2,482 | 2,482 |

(13 more items than on 10-07: the library grew.) **Every call site that needs it** — all recursive
Movie/Series listings collapse, the movie-only one too:

1. `JellyfinClient._fetch_items()` — the scan (`get_items()`, per library) **and** B8's webhook folder
   lookup `list_item_paths("Movie")`, which today would miss a collected film's folder.
2. U2's `complete_listing()` — its pages **and** its `Limit=0` recount, or the completeness check
   compares two different populations.
3. `get_sample_items()` / `get_diverse_sample_items()` (the Preview page's samples): 2 of the newest 50
   are BoxSets today, so a collection's poster can be offered as a sample. Cosmetic, but one parameter.

Not needed: `/Items?Ids=` lookups (every hidden film answers by id, measured 10-07), `/Items/{id}`, and
`_get_first_episode_path()` (`IncludeItemTypes=Episode` under a series; episodes are not boxed).
**Downsides — none found.** No duplicates (a film in a collection is listed once, as itself; Ids unique =
rows), no BoxSet rows at all (so the 448 `skip (no_file)` scan errors go away too), and no measurable cost:
a 500-item page with the scan's fields took 1.58/1.78/1.87 s collapsed vs 1.94/2.18/1.38 s with `=false`
(three each — noise), for 8 % more items, which are exactly the films that should be scanned.
**Why not a toggle:** the only thing the toggle could do is turn the bug back on — `true` hides ~1,100 films
from tagging and makes U2 strip live films' \*arr tags. Collapsing is a *display* grouping for browsing
clients; xenotag needs every file. It is not something an operator would want to choose, and B26's own
"not determined" (whether a server/user setting drives the default) is exactly why the client should send
it explicitly rather than inherit it. **Recommendation: hard-code `CollapseBoxSetItems=false` at the four
sites, no setting.** Still to check in the build, as above: dev 12.1.0 and the 10.11.10 lab honour it.

**OPERATOR DECISION 2026-10-09 (second round): B26 gets NO setting.** xenotag always sends
`CollapseBoxSetItems=false` on every recursive Movie/Series `/Items` listing — the four sites above
(`_fetch_items()`, `complete_listing()`'s pages and its recount, and the two Preview sample helpers).
There is no config key, no UI control and no way to turn it off.

**B26 — SHIPPED 2026-10-10 ([#143](https://github.com/bpoulliot/xenotag/pull/143)); released in v1.11.1 (2026-10-10).**
One module-level constant, `COLLAPSE_BOX_SET_ITEMS = "false"` in `app/clients/jellyfin.py`, sent as
`CollapseBoxSetItems` at all four sites: `_fetch_items()` (so both `get_items()` and B8's
`list_item_paths()` carry it), `deleted_items.complete_listing()`'s pages **and** its `Limit=0`
recount, and `get_sample_items()` / `get_diverse_sample_items()`. No change to `_tag_config_hash()`
— B26 is a listing fix, not a vocabulary change, so no incremental scan is forced by this merge
alone. `tests/test_collapse_boxset.py`: a fake Jellyfin collapses two films into a BoxSet unless
the request opts out, exercised through each site and the recount; every test fails against
`origin/main`'s code and passes with the fix, and a one-site-at-a-time mutation check (dropping
the param from each call in turn) turned red only that site's own test(s). Suite 940 → 946.
- *Version check (read-only, GET only), 2026-10-10.* Production Jellyfin 12.2.0, via xenotag's own
  client (`ReadOnlyTransport`) from inside the `xenotag` container: `Movie,Series` default **8,781**
  vs `CollapseBoxSetItems=false` **9,467** (+2 over the operator's 10-09 figures — the library grew).
  jellyfin-dev 12.1.0 (started for this check, then stopped): param accepted (HTTP 200), 6/6 (the
  dev fixture holds no collections). Local `jellyfin/jellyfin:10.11.10` lab (throwaway container,
  scratch `/config`, `--rm`): param accepted (HTTP 200), 0/0 (empty library — not exercised, per
  budget). No write to any Jellyfin. Run output: `~/docker/overnight/projects/xenotag/runs/53-20261010/`.
- *Release note, owed at the next release that carries this:* run one full scan (`POST /scan/full`)
  after deploy to reach the ~1,134 hidden films (B26's premise; the exact count will differ by
  2026-10-10 — re-measure at release time). **U2 removal stays blocked** until that scan's
  deleted-items report shows strip 0 (queue 52's authorisation from 2026-10-06 stands for after
  B26 ships, per the *U2 removal switch* record below). *Both done 2026-10-10: released in v1.11.1,
  scan 163 reached all 1,134 films, and its report shows strip 0. See Release v1.11.1. U2 removal
  went live the same day (see *U2, deleted items*).*

**B27 — FILED 2026-10-10 by the v1.11.1 release session (found in production). READY.**
B25 and B26 are taken, so this takes B27.

- *Found.* The backup step stopped and restarted the container (`docker stop`/`start`, 1.11.0).
  It came back with a **new** `auth.secret_key`, and every session was signed out. The `config.yml`
  copied while the container was stopped was last written 2026-10-10 06:19Z, before the session
  began. Compared by key with the 10-07 backup (values redacted), that write had added the 11
  default keys a structured `/api/settings` save writes (`image.*` colours/opacity/palette/positions,
  `image.prefer_languages`, `deleted_items`, an empty `api_key_file` per \*arr instance), and it had
  left `auth.secret_key` **empty** (64 characters before). On start, `auth.bootstrap()` saw the empty
  key, generated one and saved it. That is the only difference between the copy and the live file.
  **Who made the 06:19Z save was not determined.** The likely candidate is the operator's I1
  browser save.
- *Cause.* `buildSettings()` in `app/web/templates/index.html` spreads the loaded settings, then
  replaces the whole `auth` object with `{ username: getVal('s-auth-username') }`.
  `save_settings()` (`app/web/routes.py`) restores only `body["auth"]["password_hash"]`, so
  `AuthConfig.secret_key` validates to its default `""`. `save_config_from_dict()` writes that to
  disk and installs it as the live config. From then until the next restart, `create_session()` and
  `get_session_user()` use `_session_key("", hash)` = `":" + hash[:16]`. That is `":$2b$12$"` plus
  the first 9 characters of the bcrypt salt, still secret but far below the 256-bit key. Production
  ran that way from 06:19Z to 15:31Z on 2026-10-10. Separately, `config_as_dict_safe()` pops
  `password_hash` but not `secret_key`, so `GET /api/settings` hands the signing key to the browser
  unless `XENOTAG_SECRET_KEY` supplies it.
- *Fix* (no decision needed; neither half changes behaviour anyone relies on):
  - in `save_settings()`, restore `secret_key` from the running config, as `password_hash` already is;
  - in `config_as_dict_safe()`, drop `auth.secret_key` as it drops `password_hash`.
  Tests: a structured save keeps the key on disk and in memory, and a session minted before the
  save still verifies after it; `GET /api/settings` carries no `secret_key`. The raw YAML editor
  (`PUT /config`) is not affected: it writes the submitted text, which carries the key.
- *Effect of a release:* none on its own. The key regenerated in production at the 2026-10-10
  restart is intact today (64 characters).

**B22 — SHIPPED 2026-10-06 ([#113](https://github.com/bpoulliot/xenotag/pull/113)); released in v1.11.0 (2026-10-07).**
`_run_scan_recorded()`'s `except` now calls `progress.finish(error=str(exc))` and then records the
failure and re-raises, as before. It does that only while `progress.running` is still True, so a
`_run_scan()` exit that already called `finish()` stands, and nothing is finished twice. A
`finally` was rejected because it would also run on the happy path. No other caller takes
`try_start()`. `tests/test_scan_lock.py` has 7 tests, and 5 of them fail without the fix: the repro
below for both scan types, the next scan running, and a real `start_scan_run()` that raises
`sqlite3.OperationalError`. The other two check that the happy path and a scan that already
finished call `finish()` exactly once. No tag-hash change, so no re-tag. The filing follows.

*Filed 2026-09-27, found while building I5:* Demonstrated on
the I5 branch: with `_run_scan` replaced by one that raises `RuntimeError("database is locked")`,
`run_incremental_scan()` re-raises, `progress.running` stays **True**, and the next
`run_incremental_scan()` returns at once with `Scan already in progress, skipping`. The scheduler
thread swallows the exception after logging it, so nothing else notices: the UI shows a scan
running forever and no scan runs again until the container restarts. What can raise there today is
narrow — `_run_scan()` catches the Jellyfin listing, each probe and each item, but not its own
`start_scan_run()` / `set_meta()` / `finish_scan_run()` commits (a locked SQLite), nor
`ArrTagSync.resolve_all()`. **Since I5 it is visible**: `xenotag_scan_running` reads 1, the
failure is recorded, and the host's 26-hour no-successful-scan alert fires. **Fix:** in I5's
`pipeline._run_scan_recorded()`, call `progress.finish(error=str(exc))` in the `except` before
re-raising, and test that a second scan then runs. Not done in I5 because it changes scan
behaviour, not observability.

**B21 — SHIPPED 2026-10-06 ([#124](https://github.com/bpoulliot/xenotag/pull/124)); released in v1.11.0 (2026-10-07).** Option (b), as decided.
`_composite_pill()` places each pill in two steps: the glow — now its own image, `_render_glow()`,
cached by tile size — is pasted with itself as the mask exactly as before, so it still lands
squared; the pill (fill + label, `_render_pill_tile()`) is then `alpha_composite`d onto the row
layer, so the fill lands at `badge_opacity`. `app.contrast.render_over()` draws through the same
`_composite_pill()` onto a transparent layer, so the chips, the opacity-slider line and
`measure_badge_contrast.py` sample the pixel the poster gets rather than a model of it.

**Measured** with `scripts/measure_pill_composite.py` (new; self-test fails both ways: a 1-level
nudge, an alpha-254 compositor and B21 itself are each caught, a render against itself is silent),
Pillow 12.3.0, DejaVu Sans Bold, against a copy of the old paste (`legacy_place`, checked
byte-identical to origin/main `5476f24` on all 16 synthetic scenes at 65%):

 - **100% is byte-identical** — the note's open question. The 4 README overlay configs on their
   own sample backgrounds and on flat white, black, grey 128 and a gradient (1000×1500): 20/20
   renders, max difference **0**, **0** pixels. The 16 synthetic ones also match sha256s taken
   from origin/main, stored in `tests/test_pill_composite.py`. Why it holds: at 100% a pill tile's
   alpha only ever takes **(0, 255)** (the rounded rectangle is not antialiased; the label is drawn
   over an opaque fill), measured over 11 labels × 3 badge sizes × 3 poster widths — so no pixel is
   partly pill, and a paste and a composite agree on every one. The "edge may move by a level"
   worry does not arise. At 65% the same scenes move by up to 52 levels.
 - **Chip = poster** for all four badges at 65 / 80 / 90 / 100% on black, white and grey posters:
   difference **0** levels per channel (the tests' tolerance is 0). The old paste reproduces the
   table below to the decimal (e.g. rating at 80% on white: chip 4.52, poster 3.60).
 - **The algebra case:** rating `#73485b` over black at 65% now renders **(74, 47, 59)** =
   fill·a (a = 165/255); the old paste rendered (31, 20, 25) = fill·a³.
 - **The glow is unchanged.** The 1–8 px band left of a pill on grey 128 reads
   120, 117, 115, 115, 116, 118, 120, 122 old and new, at 100% and at 65% — the faint dark shadow
   described below is still there, as (b) chose.
 - **The opacity floors are true again on the poster:** worst shipped badge over black/white/grey,
   1% steps — **AA 80%, AAA 98%** (old paste: 87% / 99%). `config.py` and the README quote 0.80 /
   0.98 and now say it is the poster's figure.
 - `assets/readme/` did **not** move (its fixtures use the 100% default); `--check` passes.

**Consequence for the operator.** Production runs at **100%, so no production poster changes.**
A poster saved below 100% renders at the opacity it names from now on — a saved 0.65 showed the
poster through as if it were ~0.42 — but **nothing is re-rendered until a full scan** (there is no
overlay hash), so such posters move at the next full scan, not at upgrade.

[P6]'s probe self-test (`measure_adaptive_palette.py --self-test` on PR #95's branch) fails its
three end-to-end checks on that branch and **passes with this fix merged in** (checked on a local
scratch merge, not pushed). P6 is unblocked; its branch needs `main` merged.

The filing follows.

**B21 — FILED 2026-09-27, found while building P6.** It blocked
[P6], whose whole regime is below 100%.

**The mechanism.** `_render_group()` places every tile on a transparent row layer with
`overlay.paste(tile, (x - gm, y - gm), tile)` — the tile as its own mask. Pillow's masked paste
blends *every* band, alpha included, so each layer pixel becomes `rgb·a`, `alpha a²` (a = the
tile's alpha / 255), and the layer is then alpha-composited onto the poster as if unpremultiplied.
Net: the fill lands at **a³**, the poster shows through at **(1 − a²)**. At 100% (a = 1) the pill
interior is exact, which is why nothing at the shipped default ever disagreed. B1's instrument
(`app.contrast.render_over()`) composites the tile directly, at **a**, and so do B2's chips, the
opacity-slider line and `measure_badge_contrast.py` — none of them measures the path a poster
takes. Algebra checked against a render: rating `#73485b` over black at 65% — predicted fill·a³ =
`(31,20,25)`, rendered `(31,20,25)`; the instrument says `(74,47,59)`.

**Measured** (origin/main `03e983a`, Pillow 12.3.0): one `1080p` pill through
`render_badge_groups()` on a flat 1000×1500 poster, rating hidden, B1's sampler (modal pixel, 5 px
inset) on the rectangle `placed=` reports. White poster, white text, chip → poster:

| opacity | video | audio | sub | rating |
|---|---|---|---|---|
| 65% | 4.17 → **2.54** | 4.38 → **2.59** | 3.28 → **2.32** | 3.19 → **2.30** |
| 80% | 6.63 → **4.53** | 7.00 → **4.68** | 4.75 → **3.71** | 4.52 → **3.60** |
| 90% | 8.98 → **7.24** | 9.54 → **7.60** | 6.06 → **5.28** | 5.76 → **5.09** |

On a **black** poster the error runs the other way (rating at 65%: chip 11.93, poster 17.91): a
translucent pill is darker than configured everywhere and lets through more of the poster. The
opacity floors the config comment, the README and B1/B2 quote — **80% for AA, 98% for AAA** — are
**87% and 99%** for what renders. B2's chip is the one place an operator looks, and below 100% it
gives a wrong verdict with nothing to say so (silent wrongness). The Preview page's images take the
real path, so the chip and the preview beside it disagree.

**The glow, at every opacity.** The same paste squares the glow's alpha, and the glow was
blurred as unpremultiplied RGBA, so its RGB is already pulled toward the transparent black. On a
mid-grey (128) poster at **100%**, the pixels 1–8 px left of a pill read **115–122** — the
"white glow" B1 kept as a halo renders as a faint dark shadow, on every production poster today.

**Why a decision, not a fix.** Any fix changes posters; a re-render only happens on a full scan
(there is no overlay hash), so nothing moves until then. The options:

 - **(a) Composite correctly everywhere** (`overlay.alpha_composite(tile, dest=…)`; negative
   `dest` is accepted, checked on 12.3.0). The fill matches `badge_opacity` and B1/B2's numbers
   become true; the glow becomes the white halo it is drawn as — a visible change on **every**
   poster, production's included, and `assets/readme/` must be regenerated (its `--check` is in CI).
 - **(b) Composite the pill correctly, keep today's glow pixels.** Below 100% the fill matches
   the configured opacity; at 100% posters stay as they are — *not measured*: the pill's
   antialiased edge may move by a level, so "byte-identical at 100%" needs a render comparison.
 - **(c) Keep the render, re-derive the instrument.** The chips and floors get honest numbers
   (87% / 99%); `badge_opacity` keeps meaning something other than the alpha its comment
   documents, and B1's model stays wrong for every later measurement.

**Recommend (b):** production (100%) keeps its look, the documented opacity becomes true below it,
and [P6]'s probe re-runs unchanged — its self-test's end-to-end check is exactly this comparison
and fails today. Either (a) or (b) moves every poster saved below 100% toward the opacity it
names (a saved 0.65 renders at an effective ~0.42 today).

**OPERATOR DECISION 2026-10-05:** (b) — composite the pill correctly (`overlay.alpha_composite`),
keep today's glow pixels; at 100% posters stay as they are (byte-identical at 100% is not yet
confirmed by a render — *confirmed 2026-10-06, see the SHIPPED block above*). Relabelled **READY**; must merge before the next release — see the
build-order note above.

**B20 — SHIPPED 2026-10-06 ([#123](https://github.com/bpoulliot/xenotag/pull/123)); released in v1.11.0 (2026-10-07).**
`_lang3_to_lang2()` reads a tag shaped exactly `<name>[<2-3 ASCII letters>]` (one anchored regex,
`[^\[\]]+\[([a-z]{2,3})\]`, matched after lowercasing and stripping) as its bracketed code, which
then goes through the same rule as any code: `English[eng]` → `EN`, `Japanese[jpn]` → `JA`,
`Cantonese[yue]` → `YUE` (a well-formed code outside the table keeps its letters, as `yue` does),
`Foo[aac]` / `None[zxx]` → `UND` with the WARNING, `Unknown[und]` → `UND` quietly. Anything else
keeps B7's rule and is `UND` with the WARNING: bare `English`/`Japanese` (decided not (c)), `[eng]`
alone (no name), `Foo[eng][jpn]` (a name may not contain brackets), `English[]`, `English[e]`,
`English[engl]`, `English[eng]x`, and `"eng"`. External subtitle file names are unaffected (they are
gated on the plain 2-3-letter shape first). Tests in `tests/test_language_codes.py`.

**Census, 2026-10-06** — `scripts/measure_language_renames.py` (self-test passed; its self-check:
the pre-B7 rule reproduces 9,383 of 9,438 index rows), read-only on copies of production's
`jellyfin.db` and `state.db` taken with `-wal`/`-shm`. Against the pre-B7 labels: 47 changed index
rows on `origin/main` before this fix, 45 after. The index has one row per `.ogm` series that it
sees, so the per-episode figure comes from the same `jellyfin.db` copy, every episode carrying one of
the shapes, run through `_lang3_to_lang2()` and the script's `language_tags()`:

| Series | Episodes | Stream tags | Before B20 | After B20 |
|---|---|---|---|---|
| *Hyper Police* | 25 | `English[eng]`, `Japanese[jpn]`, sub `English[eng]` | `UND` | `EN JA dual-audio sub-EN` |
| *Power Dolls* | 2 | `Japanese[jpn]`, `English[eng]`, sub `English[eng]` | `UND` | `EN JA dual-audio sub-EN` |
| *Tenchi in Tokyo* | 1 | bracketed, as above | `UND` | `EN JA dual-audio sub-EN` |
| *Tenchi in Tokyo* | 25 | `English`, `Japanese`, sub `English` | `UND` | **`UND`** (unchanged) |

So two series are fixed whole and ***Tenchi in Tokyo* is only partly fixed**: 25 of its 26 episodes
still lose `EN`/`JA`/`dual-audio`/`sub-EN` at the next re-tag, each logging a WARNING per track
naming the file — those are the metadata to fix (option (a) for that series: a remux), not this
rule. The one other malformed tag in the library, `"eng"` (*The Uncomfortable Truth*, one audio
stream), stays `UND` as B7 decided.

**B20 — FILED 2026-09-27, found while fixing B7. Fixed 2026-10-06 (above).**

B7's operator decision sends a language tag that is not 2–3 ASCII letters to `UND` with a
WARNING naming the file; its example was `"e`. A census of production's `jellyfin.db` (copied
2026-09-27; every embedded audio and subtitle stream in the library) found **six** such values,
and only one is garbage: `"eng"` (1 audio stream, *The Uncomfortable Truth*). The others come
from old `.ogm` episodes — `English[eng]` (28 audio + 28 subtitle streams), `Japanese[jpn]` (28
audio), `English` (25 audio + 25 subtitle), `Japanese` (25 audio) — in **3 series** on the
index: *Power Dolls*, *Hyper Police*, *Tenchi in Tokyo*. The pre-B7 rule cut them to `EN`/`JA`,
right by accident; B7 makes each series `xt-UND` and drops `xt-EN`, `xt-JA`, `xt-dual-audio`
and `xt-sub-EN` (audio goes to the \*arrs too). Nothing is silent: every scan logs a WARNING per
track naming the file. **Question:** fix the files' metadata (remux; the warnings are the list),
or teach the rule one more shape? Options: **(a)** leave B7's rule, fix the metadata; **(b)**
accept `Name[xxx]` and read the bracketed code (it is then an ordinary 3-letter code); **(c)**
(b) plus English language names (`English`, `Japanese`) looked up in the ISO table's names —
the generated table would need to carry them. **Recommendation: (b).** The bracketed code is an
ISO 639-2 code already, so nothing is guessed; bare names (c) are a lookup by English spelling,
which is a new vocabulary. The census: `scripts/measure_language_renames.py`.

**OPERATOR DECISION 2026-10-05:** (b) — accept `Name[xxx]` and read the bracketed code. Relabelled
**READY**; must merge before the next release — see the build-order note above.

**B19 — FILED 2026-09-27, found while fixing B7. Not fixed here. NEEDS DECISION.**

B7 made every language tag its ISO 639-1 code, from the complete table. Three of those codes
are also tags xenotag emits for something else, in the same `xt-` namespace: **Sindhi `SD`**
(the resolution tag), **Divehi `DV`** (Dolby Vision) and **South Ndebele `NR`** (the rating
"NR"). Before B7 they were `SN`, `DI` and `NB` (the first-two-letters fallback), which were
wrong but distinct. B7's acceptance test (no 3-letter fallback collides with any other tag)
holds; this is the 2-letter half, and the test pins the three
(`tests/test_language_codes.py::test_the_2_letter_codes_that_are_also_other_tags_are_known`) so a
new one is seen. **Latent:** the 2026-09-27 census of production's `jellyfin.db` found no
stream in `snd`, `div`, `nbl`, `sd`, `dv` or `nr` (0 of 257,545 audio and subtitle streams; the
same query finds the 10 `khm`/`gla` streams). **Question:** does a language that spells
another tag keep its standard code? Options: **(a)** yes — accept the overlap, it is 0 items;
**(b)** those three take their 3-letter form (`SND`, `DIV`, `NBL`), a hand-kept exception list
next to the generated table; **(c)** give language tags their own namespace (`xt-lang-EN`, as
subtitles already have `sub-`) — a full vocabulary rename on every item. **Recommendation:
(a)**, revisited only if a library ever carries one of them; (c) is the only clean fix and costs
a re-tag of everything for no item today.

**OPERATOR DECISION 2026-10-05:** (a) — accept the overlap (Sindhi `SD`, Divehi `DV`, South
Ndebele `NR`); 0 items today. Relabelled **CLOSED**; revisit only if a library ever carries one of
the three. The pinning test
(`test_the_2_letter_codes_that_are_also_other_tags_are_known`) stays, as the guard against a
fourth collision.

**B18 — SHIPPED 2026-10-06 ([#114](https://github.com/bpoulliot/xenotag/pull/114)); released in v1.11.0 (2026-10-07).**
`JellyfinClient.get_current_tags(ids)` reads `GET /Items?Ids=<csv>&Fields=Tags` in batches of
`TAG_READ_BATCH = 100`. The request line is ~3.6 kB (3,552 B measured on production), under the
8 kB default that Kestrel and nginx accept. `_run_scan()` now tags probed items in batches of 100,
and each batch starts with one such read. Each item in the batch gets a copy whose `Tags` are the
current ones, and both `set_managed_tags()` and U9's drift warning read that copy. The write is
otherwise unchanged. An id the read does not return, or a read that fails, keeps the listing's copy:
this is logged once per scan, the total is logged at the end, and it never fails the item. On a
cancelled scan the last, unprocessed batch is not written. Cost: one read-only GET per 100 items,
about 95 on a full scan of the current 9,450 items, and nothing extra per item. Webhooks already read
by `Ids=`. B17 reuses the method for its read-back. Production probe on 2026-10-06 (GET-only, through
`ReadOnlyTransport`): 9,450 listed, 9,450 returned by 95 batch reads, 0 missing. **The listing agreed
with `Ids=` on all 9,450**, so the stale state of 2026-09-27 had cleared by then; why it cleared is
still unknown. Tests: `tests/test_current_tags.py`.

*Filed 2026-09-27, found while reading back B9's re-tag:*

`_run_scan()` takes every item from `jf.get_items()` — the recursive `/Items` listing — and
`set_managed_tags()` builds the new tag list from that copy's non-managed tags. On production
(Jellyfin 10.11.10) that listing was **stale**: an hour after the re-tag it still served the
pre-re-tag `Tags` for all 6,366 items the re-tag had changed, and for *Frontier War*, whose tags
Jellyfin itself changed at 2026-09-26 19:05Z; it agreed with the database only for the 3,033
items whose tags had not changed. At the same moment `jellyfin.db` (a copy), `/Items?Ids=`,
`SearchTerm=` queries and the `Tags=` filter were all current: 180 items read by `Ids=` (150
re-tagged, 30 not) equal the database 180/180 and the listing 30/180. *War Dogs* alone:
`Ids=`, `Recursive+Ids` and `SearchTerm=War Dogs` return its new `xt-H264`; `NameStartsWith=War
Dogs`, with or without `MediaSources`, returns no `xt-` tag. The listing matched `state.db` for
the 20 items the 2026-09-26 09:00Z scan wrote, so it went stale between 09-26 09:01Z and 19:05Z.
Why is Jellyfin's business and was not determined (no restart was tried; production Jellyfin was
not in scope).

What it costs xenotag: (1) a non-managed tag Jellyfin gained after the listing went stale is
**dropped** by xenotag's next write of that item — not measured for this run, because the only
pre-scan reads went through the same listing; (2) every read-back through the listing is blind:
B9's first read-back reported *0 of 9,399 items changed*, and B5's cross-check and B12's filing
used the same call. **Fix:** read each item's current tags with `GET /Items?Ids=…&Fields=Tags`
(batched) immediately before `set_managed_tags()`, and use the same read for any read-back
(B17). Evidence and probes: `~/docker/xenotag/b9-retag-20260927/` (`jf_variants.py`,
`jf_ids.py`, `jf_db_snap.py`). Since U9's drift warning (2026-09-27) a stale listing also
produces false `Tag drift` warnings in a scan; the warning reads `jf.get_tags(item)`, the same copy
`set_managed_tags()` uses, so this fix corrects both.

**B17 — SHIPPED 2026-10-06 ([#121](https://github.com/bpoulliot/xenotag/pull/121)); released in v1.11.0 (2026-10-07).**
As decided, (a) + (c). `_process_one_item()` no longer writes the `state.db` row when the tag POST
returns. After the write, and the refresh if the overlay rewrote the poster, the item's tags are read
back by `Ids=` (B18's `get_current_tags()`). The read waits until `TAG_READBACK_SETTLE_S = 2.0` s
have passed since the item's last write or refresh. If the item's `xt-` tags match what was written,
the row is recorded as before. If they do not, the item is written once more, from the tags just
read (so a non-managed tag gained meanwhile is kept), and read again. Fixed by the retry, it is
recorded and counted in `xenotag_tag_writeback_mismatch_total{result="fixed"}`. Still wrong, the
`xt-` tags that were **read** are recorded, one WARNING (`Tag write did not stick: …`) names the item
and the diff, and `{result="unresolved"}` counts it. That row's mtime advances, so incremental scans
leave it; U9's drift check then compares against what Jellyfin really has. **Nothing is recorded**
(no `tags_applied`, no mtime advance), so the next scan retries the item, when the tag write raises
(the 8 client timeouts), when the retry raises, when a read-back raises, or when the read does not
return the id. The scan never fails on any of these; it logs one `Tag read-back:` summary line at
the end. The refresh is requested exactly as before ((b) was not chosen).
- **Batched, per B18 batch.** A scan reads back each 100-item batch with one `Ids=` GET once the
  batch's writes are done. That is one extra read-only GET per 100 items written, about 95 on a full
  scan of the current 9,450 items (so ~190 `Ids=` reads in all, with B18's). A batch with undone
  writes adds one POST per undone item and one more GET. It is not merged with the next batch's B18
  read: the ids differ, and 200 ids are two GETs anyway at `TAG_READ_BATCH = 100`. A webhook reads
  back its one item at once.
- **Settling: partly measured.** The bursts re-saved items 60–700 ms after the refresh. 2 s is about
  three times the worst of those, bounded, and waited at most once per batch (twice with a retry).
  The wait counts from the batch's last write, so earlier items have had longer, and the probe pool
  keeps working meanwhile. **A loaded production Jellyfin may need more**; an undo landing after the
  read is not caught. That cannot be measured from here without writing to production. The counter
  and the WARNING are how to tell after a release.
- **Release note:** this changes production scan behaviour at the next release. The first scan
  after it may log `Tag write did not stick` or `Tag read-back:` lines that earlier releases never
  printed, because the case was not looked for, not because it is new. *v1.11.0's first scan
  (2026-10-07, host load ~60) logged 219 undone writes, every one fixed by the retry, 0 unresolved
  and 0 unrecorded. An undo landing after the read-back would still be missed.*
- Tests: `tests/test_tag_readback.py` covers a write that sticks; identical write calls on the happy
  path, with the read-back after the refresh; undone once, then fixed by the retry; undone twice
  (records what was read); only undone items retried; a write that raises; a read-back that raises
  (the scan finishes); a retry whose read-back raises; an id not returned; the settle delay; and the
  single-item path. All 11 fail against the pre-B17 `pipeline.py`.

*Filed 2026-09-27, found while reading back B9's re-tag:*

`_process_one_item()` writes the tags (`POST /Items/{id}`), and when the overlay rewrote the
poster — every item, in a full scan — asks Jellyfin to refresh the item
(`POST /Items/{id}/Refresh?ReplaceAllMetadata=false&ReplaceAllImages=false`). In B9's re-tag, for
**22 of 9,340** such items `jellyfin.db` shows the item re-saved **60–700 ms after the refresh
call** with exactly the tags it had before the write (`DateLastSaved` against the POST and
refresh times in xenotag's log), in four bursts (09:05, 09:12–09:14, 09:46, 10:13–10:14Z). Both
calls returned 204. Separately, 8 tag writes timed out client-side (30 s) on the largest series
(*SpongeBob SquarePants*, *Bleach*, *One Piece*, *Bob's Burgers*, *King of the Hill*, *South
Park*, *The Simpsons*, *Teen Titans Go!*); all 8 landed server-side about 30 s later — this time.

Either way `upsert_media_state(tags_applied=…)` records the tags as applied (a tag error is only
logged), so `state.db` says the item carries tags it does not, and the mtime-driven incremental
scan never goes back. The 22 films still carry `xt-H.264`/`xt-H.265`/`xt-DD+` on Jellyfin while
their Radarr copies carry the new labels; only the next full re-tag reaches them. **Options:** (a)
read the item back by `Ids=` (B18) once the refresh has settled, retry once, and record
`tags_applied` from what was read; (b) stop requesting the refresh after a tag write (it exists for
the poster; Jellyfin's library monitor already sees the file change — unverified) or request it
before the write; (c) record nothing on a failed write so the next scan retries. Recommendation:
(a) + (c) — (b) changes how posters reach clients and needs its own measurement.

*Seen again 2026-09-27 in v1.9.0's re-tag (scan 148):* 21 of the 22 films were respelled, but 2
films read back **by id** still carry `xt-H.264` after the scan's tag write returned 204 and its
refresh was sent — B9's leftovers and new lost updates are indistinguishable from here — and 5 of
the largest series timed out client-side again (30 s). No new evidence on the options.

**OPERATOR DECISION 2026-10-05:** (a) + (c) — read the item back by `Ids=` (B18) once the refresh
has settled, retry once, and record `tags_applied` from what was read; record nothing on a failed
write so the next scan retries. Relabelled **READY**; depends on B18 and builds after it.

**B16 — FILED 2026-09-27 by I13 (CodeQL #8). NEEDS DECISION.**

`auth.bootstrap()` runs at every start; when `auth.password_hash` is empty and `XENOTAG_PASSWORD`
is unset it generates a password and logs it at WARNING (`auth.py:130–135`). Reproduced: the logged
value verifies against the hash it stored, and it stays valid until someone changes it in Settings.
The log is stdout only, but on this host that means `docker logs`, Dozzle and Portainer (both
running), plus any log shipper added later; prod's json-file driver keeps 3 × 10 MB. **Not exposed
today** — prod has a `$2b$` hash and 0 `FIRST RUN` lines — so this is about fresh installs.
The alert is accurate; whether to change the behaviour is a taste call, hence the label. Options:
(a) keep it and dismiss #8 as *won't fix* — the Jenkins/Portainer convention;
(b) write the password to a 0600 file under `/config` (e.g. `initial-password`), log only the path,
delete the file on the first password change; (c) refuse to start without `XENOTAG_PASSWORD`
(breaks the `docker run` quickstart); (d) keep logging it, but force a change at first login.
**Recommendation: (b)** — same first-run experience, the secret lives where `config.yml` already
does. Needs no schema change.

**OPERATOR DECISION 2026-10-05:** (b) — write the generated first-run password to a 0600 file
under `/config` (`initial-password`), log only the path, delete the file on the first password
change. Relabelled **READY**.

**SHIPPED 2026-10-06 ([#125](https://github.com/bpoulliot/xenotag/pull/125)); released in v1.11.0 (2026-10-07).**
`bootstrap()` writes a generated password to `initial-password` beside `config.yml`, created by
`os.open(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)`, and logs only the path. An existing file is
never overwritten or re-logged: with a hash stored, one WARNING says it is still there; with no hash,
its password is adopted (a first run whose save never landed). Empty/unreadable file or unwritable
`/config` → no hash, an ERROR naming the recovery. `POST /api/auth/change-password` deletes the file;
`XENOTAG_PASSWORD` creates none. README, `config.example.yml` and the login hint point at the file.
`tests/test_first_run_password.py` (12) greps the captured log for the password; mutation-checked.
**Prod unaffected:** it holds a `$2b$` hash, so the generation branch never runs (live `/config`
not touched). CodeQL #8 should close as *fixed* on `main`'s next analysis — not dismissed by hand.

**B15 — SHIPPED 2026-10-06 ([#115](https://github.com/bpoulliot/xenotag/pull/115)); released in v1.11.0 (2026-10-07).**
`app/auth.py` imports bcrypt unconditionally — no `except ImportError` branch remains, so an
install without bcrypt fails at import. A stored `sha256:` hash no longer verifies, and
`bootstrap()` logs an ERROR naming the recovery (blank `auth.password_hash`, restart); it does not
reset the hash. `tests/test_auth_no_sha256.py` pins all of it; its ERROR and import tests fail on
the old code. `requirements.txt` still pins `bcrypt==5.0.0`; the Dockerfile installs it.
CodeQL #6/#7 should close as *fixed* on `main`'s next analysis — not dismissed by hand.

*Filed 2026-09-27 by I13 (CodeQL #6, #7):*

`app/auth.py` wraps `import bcrypt` in `try/except ImportError` and, on failure, hashes the admin
password as `"sha256:" + hashlib.sha256(pw).hexdigest()` — unsalted and fast, so a copy of
`config.yml` (a backup, a leaked file) gives the password up to a GPU or a lookup table. Probe with
bcrypt made unimportable: two hashes of `hunter2` are identical and equal its plain SHA-256. It is
also a lockout: under the fallback, `verify_password()` rejects every `$2b$` hash, so an install
that loses bcrypt silently locks its admin out, with one startup WARNING as the only trace.
**Latent** — `bcrypt==5.0.0` is pinned in `requirements.txt`, installed by the Dockerfile, imports
in the prod container, and prod and dev both store `$2b$`.

**Fix (contained):** delete the `except ImportError` branch and import bcrypt unconditionally, so a
missing bcrypt fails at import instead of downgrading; fix the section comment ("bcrypt preferred,
sha256 fallback"). Only an install without bcrypt can hold a `sha256:` hash; have `bootstrap()` log
an ERROR naming the recovery when it sees one — blank `auth.password_hash` in `config.yml` and
restart, which re-runs the first-run path. Test: a `sha256:` hash does not verify. #6/#7 should
then close as *fixed* on `main`'s next analysis.

**B14 — SHIPPED 2026-10-06 ([#116](https://github.com/bpoulliot/xenotag/pull/116)); released in v1.11.0 (2026-10-07).**
`routes.preview_image()` parses its request into a `MediaInfo` (`_preview_media_info()`: one track
per comma-separated `LANG CODEC` token; a one-word token is a codec with language `UND`) and calls
`pipeline._make_badge_groups()`, as `scripts/generate_readme_images.py` does. `_preview_order()`, the
second copy of the ordering, is removed. The request shape is unchanged, so `index.html` is
untouched: that is the smallest change and moves nothing on the page. `tests/test_preview_groups.py`
reads `PREVIEW_PROFILES` out of `index.html` and checks that each profile's preview bytes equal
`generate_preview_bytes()` over the groups a scan builds from a hand-written `MediaInfo`, with and
without `prefer_languages`. A spy test fails if the route builds groups itself again. Against the
old route, 11 of its 12 tests fail. Suite 750 → 762. `generate_readme_images.py --check` is
unchanged (the overlay examples never went through this route). **`assets/readme/ui-preview.png`**
(the manual Playwright screenshot) still shows the old labels, so it needs re-taking with
`--screenshots` the next time the README screenshots are refreshed. Found while fixing: **B23** (the
preview ignores `tags.destinations`), filed below.

**B23 — FILED 2026-10-06, found while fixing B14. SHIPPED 2026-10-06 ([#129](https://github.com/bpoulliot/xenotag/pull/129)); released in v1.11.0 (2026-10-07).** A scan paints a category only if
`"poster"` is in its `tags.destinations` list (`_make_badge_groups()`). The preview passed
`AppConfig(image=…)`, so it always used the default destinations, which include `poster` everywhere.
An operator who took `poster` off, say, subtitles still saw subtitle pills in the preview. That
predated B14, which kept it as it was. **Fix:** `preview_image()` now passes the saved
`get_config().tags` alongside the previewed `ImageConfig`. Latent: production's `config.yml` has
`poster` in all four lists (read by key, 2026-10-06), so this never showed there.

*Filed 2026-09-26, found while screenshotting the Preview page for P5:*

`PREVIEW_PROFILES` in `index.html` hands `/preview/image` label strings (`audio="EN DTS-HD,JA AAC"`,
`rating="PG-13"`) and `routes.preview_image()` splits them on commas into `BadgeGroup`s as-is. A scan
never produces those strings: `pipeline._make_badge_groups()` groups audio by codec (`DTS-HD EN`,
`AAC JA`), subtitles by format (`PGS EN JA`), and prefixes the rating (`Rated PG-13`). Seen side by
side in `assets/readme/ui-preview.png` (preview) and `assets/readme/overlay-*.jpg` (the real path).
The preview is the only place an operator judges a palette or a badge size before a scan rewrites
every poster, so a narrower pill there is the wrong evidence. **Fix:** have the preview build its
groups through `_make_badge_groups()` from a synthetic `MediaInfo` per profile, as
`scripts/generate_readme_images.py` does, so the two cannot drift again.

**B13 — FILED 2026-09-26, found while taking B5 live. Not fixed here.**

The B5 read-back compared every film that sits in both Radarr instances. Two 4K copies carry
`xt-1080p`, each planned from its own 4K item — not a crossed write. `ffprobe` on the files:
*Return to Silent Hill* (`WEBDL-2160p`) is **3836×1604**, *Dr. Strangelove* (`Bluray-2160p`) is
**3584×2160**. `scanner._detect_resolution()` returns the first `RESOLUTION_THRESHOLDS` label
whose width the stream reaches — 3840 / 1920 / 1280 / 854 — so four pixels of crop costs a
whole class, and a 2160-line picture is called 1080p. The same rule puts any 1080p encode
cropped below 1920 wide at `720p` (how often that happens here is the measurement below).

NEEDS MEASUREMENT before a rule is chosen (a tolerance on width, height-or-width, or the
nominal class from the larger dimension): how many items sit just under a threshold. Jellyfin's
item `MediaStreams` already carry `Width`/`Height` (`ITEM_FIELDS` requests them), so it is a
read-only sweep, no probing. Any fix renames tags already on Jellyfin and the \*arrs.

*Sweep 2026-09-26 — **NEEDS MEASUREMENT**, confirmed; queueable as written.* **What:** for every
video item in the 17 configured libraries, the first video stream's `Width`×`Height` from one
paginated read-only `/Items` sweep of production Jellyfin (the same kind of sweep U1 and B5 ran),
bucketed by how far below each threshold it falls (0–1%, 1–5%, 5–10% of 3840/1920/1280/854) and by
aspect (scope, flat, 4:3, open matte). **Record** the table, the width/height pairs that each
candidate rule (width tolerance; height-or-width; the larger of `W/16` and `H/9` rounded to the
nearest class) classifies differently from today, and how many live tags each rule would rename.
**On:** production Jellyfin, GET only. **Roughly 1 h**, no code shipped. The rule choice after it
is then a NEEDS DECISION with numbers attached.

**B13 — MEASURED 2026-09-27 (03:40–03:44Z), relabelled NEEDS DECISION. Nothing implemented.**

*Instrument.* `scripts/measure_resolution_thresholds.py` (its `--self-test` runs in CI; the live
sweep is a manual mode). One paginated `/Items` sweep of **production** Jellyfin over the 17
`cfg.jellyfin.library_ids`, deduplicated across libraries as `get_all_items()` does: **2,632
GETs through `ReadOnlyTransport`, 0 blocked**, nothing written. What the scan tags is what was
measured: a **movie's** own item streams (the file at the item's `Path`, which is what the scan
probes, since `ITEM_FIELDS` has no `MediaSources`), and a **series'** first episode, fetched with
the scan's own query (`_get_first_episode_path`: `SortName` ascending, `Limit 1`, `MediaSources[0]`).
The stream is the first `Type=Video` by `Index`; Jellyfin gives PGS subtitles a Width/Height too,
so the type filter matters. Today's class comes from **importing** `_detect_resolution()`.
**Population:** 6,944 movies + 2,455 series = **9,399 tagged items** (10 with no video stream:
B11's 9 empty series and 1 movie), plus all **67,321 episodes** as an untagged distribution. The
17 libraries hold every Movie/Series/Episode on the server (whole-server counts are identical).
*Instrument check:* today's rule on Jellyfin's `Width`×`Height` reproduces the class `state.db`
recorded (a copy, from the scan's own ffprobe) for **9,387 of 9,387** items with a row; 2 have
no row. Jellyfin reports the two filed cases as **3836×1604** and **3584×2160**, as ffprobe did.
*Stream edge cases:* 6 tagged items carry two video streams (5 movies, 1 series; 22 episodes);
the first by `Index` was taken, which is ffprobe's first too, since **no** item has an embedded
image ahead of its video stream (the flag is proven to fire on a fixture). 158 tagged items carry
more than one `MediaSource` (138 movies, 20 series' first episodes). Of the movies' extra sources,
132 are HD/4K twins — separately swept and separately tagged items — and 8 are versions inside one
item that the scan never probes (2 of the 8 would class differently from the item's own file).

*How far under a threshold the width falls* (count; bands are `(0,1]`, `(1,5]`, `(5,10]` % below):

| threshold | band | movies | series | episodes |
|---|---|---:|---:|---:|
| 3840 | 0–1% | 1 | 0 | 0 |
| 3840 | 1–5% | 0 | 0 | 0 |
| 3840 | 5–10% | 1 | 0 | 0 |
| 1920 | 0–1% | 243 | 28 | 1,385 |
| 1920 | 1–5% | 15 | 2 | 18 |
| 1920 | 5–10% | 84 | 1 | 11 |
| 1280 | 0–1% | 6 | 1 | 128 |
| 1280 | 1–5% | 3 | 0 | 1 |
| 1280 | 5–10% | 0 | 1 | 80 |
| 854 | 0–1% | 5 | 5 | 94 |
| 854 | 1–5% | 1 | 1 | 26 |
| 854 | 5–10% | 4 | 0 | 0 |

*Aspect of those banded tagged items* (storage W/H): under 1920, the 0–1% band is 89 scope
(≥2.2), 87 flat (1.80–2.2) and 95 16:9 — ordinary crops of every shape — while the 5–10% band is
81 open-matte (1.40–1.70, mostly 1792/1800×1080) and 4 16:9. The 3840 rows are the two filed
films (scope, open matte). All 9,389 tagged items with video: 2,280 scope, 1,605 flat, 4,137 16:9,
601 1.40–1.70, 745 4:3, 21 narrower.

*Width-only misses that no band shows:* **1,059 tagged items are `720p` today; 583 of them are
≥ 1728 wide or 1080 high** — 131 are 1440×1080 alone (4:3 HD, or anamorphic HDV), 26 are
1792×1080. That, not the two 4K films, is the bulk of B13.

*What each rule changes, against today's imported rule* (tagged items; class names are unchanged
under every rule, so no new tag label is created). **Renames:** one on Jellyfin per item, plus one
per \*arr instance that owns it, counted from B5's go-live plans (`rg-live`, `sg-final-plan`,
`sa-live`, `r4-live`, `s4-live`: 9,347 plan rows = every object B5 wrote) — no changed item is
owned by two instances. Video tags go to `poster` too, so every changed item is also **one
poster re-render**.

| rule | items changing class | Jellyfin + \*arr renames | transitions | 3836×1604 | 3584×2160 |
|---|---:|---:|---|---|---|
| width −1% | 289 | 289 + 289 | 720p→1080p 271, SD→480p 10, 480p→720p 7, 1080p→4K 1 | 4K | 1080p |
| width −2% | 303 | 303 + 303 | 720p→1080p 283, SD→480p 11, 480p→720p 8, 1080p→4K 1 | 4K | 1080p |
| width −5% | 311 | 311 + 311 | 720p→1080p 288, SD→480p 12, 480p→720p 10, 1080p→4K 1 | 4K | 1080p |
| width −10% | 402 | 402 + 402 | 720p→1080p 373, SD→480p 16, 480p→720p 11, 1080p→4K 2 | 4K | 4K |
| height-or-width (exact) | 1,195 | 1,195 + 1,176 | **SD→480p 810**, 720p→1080p 322, 480p→720p 60, 1080p→4K 2, 480p→1080p 1 | 1080p | 4K |
| height-or-width −1% | 1,538 | 1,538 + 1,518 | **SD→480p 834**, 720p→1080p 639, 480p→720p 60, 1080p→4K 3, 480p→1080p 2 | 4K | 4K |
| **height-or-width −1%, HD only** | **714** | 714 + 712 | 720p→1080p 639, 480p→720p 60, SD→480p 10, 1080p→4K 3, 480p→1080p 2 | 4K | 4K |
| **height-or-width −5%, HD only** | **745** | 745 + 743 | 720p→1080p 667, 480p→720p 61, SD→480p 12, 1080p→4K 3, 480p→1080p 2 | 4K | 4K |
| height-or-width −5% | 1,604 | — | SD→480p 871, then as the HD-only row | 4K | 4K |
| nominal `max(W/16, H/9)·9`, nearest | 1,562 | 1,562 + 1,543 | **SD→480p 810**, 720p→1080p 685, 480p→720p 60, 480p→1080p 4, 1080p→4K 3 | 4K | 4K |

"HD only" = width **or height** within the tolerance for 4K / 1080p / 720p (heights 2160 / 1080 /
720); below 720p, width alone with the same tolerance, as today. The third 1080p→4K is a
2960×2160 (1.37:1) file. Commonest pairs each rule moves (all counts in the probe's output):
width rules — 1904×1072 (26), 1904×1024 (22), 1916×1080 (20), 1918×1080 (18), 1918×802 (17);
height-or-width adds 1440×1080 (131), 1440×1072 (28), 1792×1080 (26), 960×720 (24); the rules
that use height for 480p also move 720×480 (305), 640×480 (270), 704×480 (46) and 720×576 (19)
DVD-shaped frames from SD to 480p. HD-only −5% adds 31 items over −1%, every one a visibly
cropped 1080-line source (1888×800, 1424×1056, 1820×1040 …). Nominal alone rounds 1136×960 and
1200×900 up to 1080p and would put a 2880-wide frame at 4K (the 1620-line midpoint).

*Delivery — measured from the code, and part of the decision.* `media_state` stores the class
label, not `Width`/`Height`, and `_detect_resolution()` is not part of `_tag_config_hash()`, so a
new rule reaches **only items whose file mtime changes**: an incremental scan skips the other
~740, and the index-driven \*arr dry run keeps reading the old `resolution` from their rows. Any
fix needs a forced full re-tag (roughly 1 h, like the 2026-09-27 one) or stored dimensions.

*Could not determine.* Whether a series' first episode is representative of the rest (episodes are
untagged; only their distribution is above). The second video stream's size on the 6 two-stream
items. The alternate versions of the 20 series' first episodes (only the movies' were read). Per-
file ffprobe `Width`×`Height` beyond the class-level agreement with `state.db` (the class matched
on every item; the pixel numbers were not re-probed).

**Decision needed (the operator's):**

1. **Which rule.** Width tolerance alone fixes the scope crop but not the 3584×2160 open matte, and
   leaves every 1440×1080 and 1792×1080 frame at 720p. Height-or-width without a tolerance fixes
   the open matte but not the scope crop. Rules that also use height at the 480p boundary move
   810–871 DVD-shaped files from SD to 480p — a change nobody filed. **Recommendation: height-or-
   width, 5% tolerance, HD classes only** (745 items; both filed cases → 4K; 667 1080-line crops
   leave 720p; DVDs stay SD). Choose −1% instead (714) if a 5% margin feels loose — the difference
   is the 31 heavier crops listed above.
2. **Are DVDs 480p or SD?** Today they are SD. Moving them is +859 items on top of the rule above
   (height-or-width −5% minus its HD-only form). **Recommendation: leave SD** — it is a naming
   choice, not the defect B13 filed.
3. **How the new rule reaches existing items.** (a) Fold a resolution-rule version into
   `_tag_config_hash()`, so the upgrade forces one full re-tag — against that function's stated
   intent that an upgrade does not force a rescan; or (b) store `width`/`height` in `media_state`
   (an Alembic revision, now possible since I3) so this and any later rule change re-classify from
   the index — which still needs one re-probe to fill the columns. **Recommendation: (a)** for this
   fix; (b) only if another rule change is expected.

No option introduces a tag label: `xt-4k`, `xt-1080p`, `xt-720p`, `xt-480p` and `xt-sd` are all
legal in Radarr's `[a-z0-9-]` (B9). Value 3→4 (hundreds of items, not two) and Complexity 1→2
(the delivery question) on this measurement.

**OPERATOR DECISION 2026-10-05:** height-or-width, 5% tolerance, HD classes only (745 items; both
filed cases move to the class the file lists); DVDs **stay SD**. Delivery: **(a)** — fold a
resolution-rule version into `_tag_config_hash()`, so the upgrade forces one full re-tag.
Relabelled **READY**; must merge before the next release, with B20 and B21 — see the build-order
note above. The forced re-tag writes live `*arr` tags, same as B9's and B7's did.

**B13 — SHIPPED 2026-10-06 ([#122](https://github.com/bpoulliot/xenotag/pull/122)); released in v1.11.0 (2026-10-07).**
`scanner._detect_resolution()` now uses `RESOLUTION_CLASSES` ((3840, 2160, 4K), (1920, 1080, 1080p),
(1280, 720, 720p), (854, no height, 480p)) with `RESOLUTION_TOLERANCE_PCT = 5`. A stream is in a class
when its width or height reaches the class's value less 5% (integer test, `size·100 ≥ class·95`), and
the highest class wins. 480p has no height, so it is width ≥ 812 alone, and DVD frames stay SD.
`RESOLUTION_RULE_VERSION = 2` is folded into `_tag_config_hash()` (`|resolution-rule:2`), so the
default-config hash goes from `ed8a1890a06dc045` to `7a0b22544d89aff4`.

**⚠ Release note — the next release's first scan is a FULL RE-TAG WITH LIVE `*arr` WRITES**, like
v1.9.0's. It re-probes every file and moves about 747 items' resolution tags: one rename on Jellyfin
plus one on each owning `*arr` (about 741, counted from B5's go-live plans), and one poster re-render
each. Production is live on all five `*arr`s with an empty recycle bin. *It ran on 2026-10-07 as
v1.11.0's scan 158: the 658 movers the scan could reach all moved to the predicted class, and 659
\*arr objects were renamed; the other 89 movers are films B26 hides — see Release v1.11.0.*
After the re-tag, read back by `Ids=` (B18), not the recursive listing.

*Proof.* The probe now keeps the pre-B13 rule frozen as `before_b13()` (what the library's tags were
written with) and imports the shipped rule as the `shipped` row. Its `--self-test` (CI) fails unless
`shipped` equals `hw-5%-hd` on a 0–4200 × 0–2300 grid. It was shown to fail on a 1% tolerance and on
using height at 480p, and to pass again when restored. `tests/test_resolution_rule.py` pins:
- the filed files and the commonest moved pairs (1440×1080, 1792×1080, 1904×1072, 1918×1080 → 1080p);
- the DVD shapes (SD) and the 5% edges;
- that the hash moves with the version;
- that an install with unchanged config on the new code re-tags once and then not again, with a
  control where the current version stored is not re-tagged.

Suite 788 → 835.

*Live re-sweep 2026-10-06* (production, 2,501 GETs through `ReadOnlyTransport`, 0 blocked; the five
B5 plans; a copy of `state.db`). The library has grown to 6,977 movies + 2,473 series. The `shipped`
row equals `hw-5%-hd`: **747** items, 741 `*arr` renames: 720p→1080p 669, 480p→720p 61, SD→480p 12,
1080p→4K 3, 480p→1080p 2. Against the table's 745, only 720p→1080p moved (+2):
- 4 movers were created after the 09-27 sweep (09-28 and 10-03 ×3, all 720p→1080p, none in a B5
  plan);
- the owned movers fell from 743 to 741, so 2 have left the library or been replaced.

The two departed items could not be named, because the 09-27 per-item records were not kept. The
pre-B13 rule still agrees with `state.db` on 9,438 of 9,438 items, and `shipped` disagrees with it
on exactly the 747.

**B12 — FILED 2026-09-26, found while taking B5 live. Not fixed here.**

**MEASURED 2026-10-07 — relabelled NEEDS DECISION.** Full write-up, tables and re-run commands:
[`docs/measurements/b12-tag-loss.md`](docs/measurements/b12-tag-loss.md); probes
`scripts/measure_nfo_tag_drift.py` (production DB copies, `--self-test`) and
`scripts/measure_nfo_tag_merge.py` (a throwaway Jellyfin 10.11.10 and 12.2, same result on both).

- **The premise is wrong: the five lost their tags on 2026-09-04, before the 09-24 write.** In the
  2026-09-08 `jellyfin.db` backup, every item last saved before 09-04 carries `xt-` tags (1,304 of
  1,304), and 7,950 of the 8,038 saved 09-04 to 09-08 carry none. 5,454 were saved on 09-04 alone,
  5,113 of them 04:00–08:00Z from every drive, four of the five films at 07:05–07:08Z. Those were
  refreshes of existing items, not re-creations, and each kept only its NFO tags and provider
  keywords. In the lab, only `replaceAllMetadata=true` does that (B12 doc, step s10). **Who sent it
  could not be determined:** the server log starts 2026-10-04, the ActivityLog does not record
  refreshes, and no host script sends `true`. The 09-23/24 full scans restored the rest. On the
  five, the tags on 09-26 were exactly the 09-08 set, so those writes did not stick (B17's class;
  unprovable without a snapshot from 09-24..26). The 09-27 re-tag restored them. Read by `Ids=` on
  12.2.0 at 02:10Z 10-07, all five carry their `xt-` tags.
- **The hourly refresh timer is ruled out.** `FullRefresh` with `replaceAllMetadata=false` keeps every
  tag, xenotag's spelling included, on both versions, and Tdarr never transcoded the five after
  August. So is xenotag's own `refresh_item()`.
- **The \*arr NFO lead is real, but it respells; it does not remove.** After each \*arr refresh
  rewrites an NFO, Jellyfin's real-time monitor merges it into the item: NFO tags first, one copy per
  tag regardless of case. The \*arr's lowercase `xt-aac` replaces `xt-AAC`, and tags no \*arr
  carries (`xt-sub-EN`, the rating) are kept. By 10-06 18:12Z this had happened to **7,732 of 9,445**
  tracked items, and the rule predicts 7,732 of the 7,734 drifted exactly; the other 2 are B17
  films. The same happens on 12.2 (60 more after the upgrade). Jellyfin's `Tags=` filter ignores
  case (3,694 for `xt-H264` and for `xt-h264`), so this costs Jellyfin users nothing. **But U9's
  `_tag_drift()` compares case-sensitively and will warn on ~7,800 items at the next full scan.**
- **Item re-creation is a second, smaller loss path.** The 12.2 upgrade removed and re-added
  *Jimmy Carr: Stand Up* (same Id, new `DateCreated`). The rebuilt item has only NFO and provider
  tags, so it lost `xt-sub-EN` and `xt-Not-Rated`, and no incremental scan will go back to it.
- **Fix, recommended (not built):** (a) compare Jellyfin tags case-insensitively in `_tag_drift()`
  and B17's read-back; this needs no decision. (b) A reconciliation pass: read every tracked item by
  `Ids=` (~95 GETs) and re-write any item missing a managed tag, regardless of case. Nothing else
  repairs a 09-04-style wipe or a re-creation, because the mtime-driven scan never returns to an
  unchanged file. **Decision needed on (b):** how often it runs, and whether it may write to items
  whose file did not change. Not recommended: adopting the \*arrs' lowercase in Jellyfin (that would
  undo B9's spelling) or disabling the \*arrs' NFO tags.

**OPERATOR DECISION 2026-10-09 — B12 is split in two.**

- **B12(a) — READY.** Case-insensitive compare in `_tag_drift()` and B17's read-back, as recommended
  above; it needed no decision. It must land **before or with** B12(b): a reconciliation pass that
  compares case-sensitively would re-write the ~7,800 lowercase-respelled items on every run, and the
  next \*arr NFO merge would respell them again — a write loop that repairs nothing.
- **B12(b) — the reconciliation pass. Decided:** it runs **on a schedule**, and the operator also gets
  **a manual "rescan" option** (a button / `POST` route beside the scan ones). **Implied by the choice, not
  separately asked:** it writes to items whose file has not changed — that is the point of reconciling
  (nothing else repairs a 09-04-style wipe or a re-created item). Its writes are the scan's own: managed
  `xt-` tags only, through `set_managed_tags()` with B17's read-back; it never touches a non-managed tag.
  A missing tag is judged **regardless of case** (B12(a)). Read side, as measured: every tracked item by
  `Ids=`, ~95 GETs.
- **Still open (why B12(b) stays NEEDS DECISION, narrowed):**
  1. **Default cadence.** Recommend its own `scan.reconcile_schedule` cron (empty = off, like
     `scan.schedule`), default daily `0 5 * * *` — after the 03:00 scan (scan 158, a full one, took 99 min).
     The read side is cheap enough for daily; a wipe would be repaired within a day.
  2. **Scope: Jellyfin only, or the \*arrs too?** Recommend Jellyfin only — every loss B12 measured was
     Jellyfin-side, and the \*arr copies were unaffected; the \*arrs are re-written by the scan already.
  3. **Worth a confirming nod (flagged, not blocking):** after a mass wipe the pass would re-write
     thousands of items in one run — the v1.11.0 re-tag's scale, hours under host load. It is the repair
     working as intended, but the operator may want a per-run write cap, or a log/ntfy line when it
     writes more than N items. Posters are out of scope: the pass restores tags, not overlays.

**OPERATOR DECISION 2026-10-09 (second round) — B12(b)'s cadence is ACCEPTED.**

- **Decided (open question 1):** the pass has **its own setting, `scan.reconcile_schedule`** (a cron
  string; empty = off, like `scan.schedule`), **default daily `0 5 * * *`** — after the 03:00 scan. The
  manual rescan option decided earlier stays: both triggers run the same pass.
- **Still open — question 2, scope.** Jellyfin only, or the \*arrs too. The recommendation is
  **Jellyfin only** (reasons in point 2 above); the operator has **not yet accepted** it.
- **Still open — question 3, the mass-write guard.** The operator asked why the guard would cap
  *writes per run* rather than *job time*, or have no cap at all, and will decide after an explanation.
  The options on the table: (i) a **per-run write threshold** — past N writes the pass halts and
  notifies, so a mass wipe is repaired only after the operator looks; (ii) **notify and continue** —
  past N writes it sends one ntfy/log line and finishes the repair; (iii) a **time cap** — the pass
  stops after T minutes and the next run resumes; (iv) **no cap**. No recommendation is recorded as
  accepted.

B12(b) therefore stays **NEEDS DECISION** on questions 2 and 3 only; B12(a) is READY regardless.

**OPERATOR DECISION 2026-10-09 (third round) — B12(b) → READY.** Both open questions answered.

- **Decided (question 2, scope): Jellyfin only.** The reconciliation pass reads and re-writes
  Jellyfin items only; it **does not write the \*arrs** (the recommendation, now accepted — every loss
  B12 measured was Jellyfin-side, and the scan already writes the \*arrs).
- **Decided (question 3, the mass-write guard): option (i), a per-run write THRESHOLD.** The pass
  first works out which items need re-writing. If that count is **more than N** — a new setting next
  to `scan.reconcile_schedule`, **default 500** — the pass **writes nothing at all** (not the first
  N: none), sends a notification naming **the count and a sample** of the items, and waits for the
  operator to start it by hand. **The manual rescan bypasses the threshold** — that is the path after
  the operator has reviewed the alarm. At or under N, the scheduled pass repairs as specified above.
- **Rationale, recorded as the operator gave it:** a mass event — the 2026-09-04 REPLACE refresh
  wiped the tags of ~8,000 items — must **raise an alarm, not be silently repaired**: a quiet repair
  leaves its upstream cause undiscovered (09-04's sender was never identified). A **time cap** bounds
  how long the pass runs, not how much it changes, so it would spread the same mass write over
  several nights. **No cap** hides the upstream cause outright. Notify-and-continue (ii) was not
  chosen for the same reason: the repair would already be done when the operator reads the alert.
- **Spec note (not a new decision):** per the I5/I6 decision of 2026-09-26 xenotag does not post
  to ntfy itself; the notification takes I5's path — a `xenotag_*` metric for the halted pass plus an
  Alertmanager rule in `~/docker/monitoring`, which reaches ntfy — and the count and sample also go in
  a WARNING log line and the run's record, where the operator reviews them before the manual rescan.
- **Unchanged:** cadence and manual trigger (second round), managed `xt-` tags only via
  `set_managed_tags()` with B17's read-back, missing tags judged regardless of case. **Still builds
  after B12(a)**, which must land first or with it.

*The filing, as written on 2026-09-26:*

Measured during the go-live: for every item it planned (9,356), Jellyfin's current `xt-` tags
against `state.db`'s `tags_applied`. **9,346 match exactly; 10 carry no `xt-` tag at all.** Five
are B11's empty series. The other five are films the 2026-09-24 full scan reached and tagged —
*Steel Magnolias*, *Swearnet: The Movie*, *War Dogs*, *What Happens After the Massacre?* (22:51Z),
*What If* (22:56Z) — with their files on disk, `LockData`/`LockedFields` unset. Their Jellyfin
`Tags` now hold TMDB keywords, `luxe`, and on two of them `av1` + `nav1s` (the AV1 batch
encoder's marker). So something rewrote those items' tags wholesale after xenotag's write, and
xenotag's incremental scan is mtime-driven: an unchanged file is never re-tagged, so the loss
stays until the next full scan. (The 2026-09-27 03:00 scan is one — B5's switch forces it — so
the five should be re-tagged then; whether they lose the tags again is the first thing to look at.) The \*arr copies are unaffected (B5 wrote them from the fresh
09-24 probes).

NEEDS MEASUREMENT: *what* rewrites them — a Jellyfin metadata refresh that replaces tags with
provider keywords, or an outside writer (the `nav1s`/`av1` tags point at the AV1 batch script,
`/mnt/media/xtor/encodes/nav1s.sh`) — measured by watching one item's `Tags` across a refresh
and across an encode, before choosing between a reconciliation pass and fixing the writer.

*2026-09-27 — U9's drift warning (released in v1.11.0) detects this class of loss:* the next
scan or webhook that reaches such an item logs `Tag drift: … lacks [xt-…]` and counts
`xenotag_tag_drift_total` before re-writing it; it does not fix B12, and an item whose file never
changes is still not reached by an incremental scan.

*Sweep 2026-09-26 — **NEEDS MEASUREMENT**, confirmed, with two leads the filing did not have.*

 - **`nav1s.sh` never calls Jellyfin.** It has no HTTP call at all; it writes only container
   and stream metadata (`title=`, per-stream `language=`) into the file it muxes (grep of the
   script, 2026-09-26). So if the `av1`/`nav1s` tags come from the encode, they arrive through
   Jellyfin's own metadata refresh of the new file, not from the script.
 - **A host job issues exactly such refreshes, hourly.** `jellyfin-refresh-transcoded.timer`
   (`~/docker/scripts/jellyfin-refresh-items.py`, see `~/docker/MAINTENANCE.md`) POSTs
   `/Items/{Id}/Refresh?metadataRefreshMode=FullRefresh&replaceAllMetadata=false` for every file
   Tdarr transcoded since its last run — 1 to 53 items an hour between 2026-09-24 16:00 and
   09-25 11:00 (journal; it logs counts, not names). Whether a FullRefresh with
   `replaceAllMetadata=false` still replaces `Tags` with the TMDB provider's keywords is the
   question. All five films also live under `/media/luxe/movies/…`, which is the likeliest
   source of the `luxe` tag.

**What to measure:** (1) on **jellyfin-dev** with a copied fixture: write `xt-` tags to the item,
issue the same `FullRefresh` call the host script sends, and read `Tags` back — once with
`replaceAllMetadata=false`, once `true`; (2) read-only on production: whether any of the five
paths appear in the Tdarr job history or the refresh script's state file between 2026-09-24
22:51Z and 09-26; (3) after the 2026-09-27 03:00 full re-tag restores them, re-read the five
items' `Tags` once a day for a week (GET only) and note when, if ever, they go again. **Roughly
1–2 h** of active work plus the passive week. The fix is then a decision between a reconciliation
pass in xenotag, a tag-preserving change to the refresh script (a `~/docker` change, not this
repo), or both.

*2026-09-27 — data points from B9's re-tag (recorded for B12; not a measurement of it).*

 - **Before the re-tag** (08:55Z, recursive listing): still no `xt-` tag on any of the five —
   *Steel Magnolias* `av1`, `nav1s`, `luxe` + 22 keywords; *Swearnet* `luxe` + 3; *War Dogs*
   `av1`, `nav1s`, `luxe` + 6; *What Happens After the Massacre?* `luxe`, `slasher`; *What If*
   `luxe` + 23; none locked. That listing was stale (B18), so this is Jellyfin's state at some
   moment between 09-26 09:01Z and 19:05Z, not at 08:55Z.
 - **The re-tag started 2026-09-27 09:00:00Z** (scan 147, ended 10:14:59Z); **step (3)'s week
   restarts from it.** The five were written 09:12:40–09:17:47Z, and `jellyfin.db` at 10:18Z holds
   *Steel Magnolias* `xt-1080p xt-H264 xt-UND xt-AAC xt-sub-EN xt-PG`; *Swearnet* `xt-1080p
   xt-H264 xt-UND xt-AAC xt-NC-17`; *War Dogs* and *What Happens After the Massacre?* `xt-1080p
   xt-H264 xt-UND xt-AAC xt-R`; *What If* `xt-1080p xt-AV1 xt-EN xt-Opus xt-sub-EN xt-PG-13`.
   **Look for these new spellings** (`xt-H264`, not `xt-H.264`).
 - **Read them by `/Items?Ids=`, never through the recursive listing** — an hour after the write
   the listing still showed all five without an `xt-` tag (B18).
 - **A lead the filing did not have: the \*arrs write NFO files.** All five \*arrs run the Kodi
   (XBMC) / Emby metadata consumer (`GET /api/v3/metadata`), which writes an object's tag labels
   into its NFO as `<tag>`; the five films' non-keyword tags (`av1`, `nav1s`, `luxe`) are exactly
   their Radarr labels. After B5 wrote the \*arr labels (11:12–11:27Z), the \*arrs rewrote **9,349
   NFOs before the re-tag, 9,333 of them between 2026-09-26 14:00Z and 20:00Z** (file mtimes);
   9,347 NFOs now carry `xt-` labels. Jellyfin
   follows them: *Frontier War* (not reached by the re-tag — ffprobe timeout) was saved at
   19:05:23Z, three minutes after Radarr rewrote its NFO, with exactly the NFO's tags (`luxe`,
   `1-admin-jellyfin`, `xt-und`, `xt-aac`, `xt-720p` — the \*arr's lowercase spelling); *Adults*
   (series, not reached) carries Sonarr's lowercase spellings beside xenotag's own `xt-sub-EN`
   and `xt-TV-MA`; *Boruto* gained Sonarr's `dual-audio` during the re-tag, after xenotag's own
   refresh call. In all three the NFO's tags were **added** — so whether a refresh can remove
   tags, B12's symptom, is still the question. The re-tag changed 6,043 \*arr objects' labels and
   none of their NFOs had been rewritten by 10:40Z; on 09-26 the rewrite came 3–9 h after the
   labels changed, which makes the next rewrite a natural moment to read the five and a sample.
 - *Data point, 2026-09-27 (v1.9.0's re-tag, Jellyfin read by id before and after):* **28 items
   carried a lowercase variant of a tag xenotag writes in another case** before the scan — `xt-aac`
   19, `xt-und` 15, `xt-en` 12, `xt-av1` 9, `xt-opus` 6 — the \*arrs' lowercase spelling, as with
   *Frontier War*; xenotag's full write replaced each with its own spelling. 0 items lost a
   non-managed tag in that scan.

**B11 — FILED 2026-09-26, found while taking B5 live; FIXED 2026-09-26 (below the sweep note).**

The B5 go-live (below) was driven from the index, like the dry run, and its independent
cross-check (every \*arr label must also be on the item's own Jellyfin tags) flagged 5 series
whose Jellyfin item carries **no** `xt-` tag while `state.db` has a full row. All 5, and 4 more,
are `probe_failed` in the 2026-09-26 09:00 scan (`scan_errors.last_seen`), with `last_scanned`
between 2026-06-02 and 2026-08-09. Measured on the host: **9 of the 10 series folders hold zero
`.mkv`/`.mp4` files** — the episodes are gone, the series remain in Jellyfin and Sonarr, no first
episode resolves, so the scan probes the *folder* and fails (48 Hours, Dateline NBC, Frontline,
Hollywood Demons, A Plan to Kill, Bodies in the Water, Fatal First Dates, The Tonight Show
Starring Jimmy Fallon, Unlocked: A Jail Experiment). The tenth, *Mating Season*, has 10 files,
ffprobe failed on an episode, and it has no row (the dry run's `no_probe_record: 1`).

So the dry run over-counts by exactly the owned items the latest scan could not reach, with
tags describing files that no longer exist; the go-live wrote those 9 and then removed them
again (B5 below). **Fix (READY):** in `run_arr_dry_run()`, skip items whose `scan_errors` row
was seen by the latest scan, and report them as their own category ("owned, but the scan
cannot reach it") instead of as "would change" — the rule the go-live driver used. Not
covered: why Jellyfin keeps a series with no episodes (the \*arr side keeps it too — it is the
library's choice, not xenotag's), and whether such rows should also go from the index (U2).

*Sweep 2026-09-26 — **READY**, with "seen by the latest scan" pinned down from the code so the
implementer does not have to rediscover it:*

 - `scan_errors` is **cleared at the start of every full scan** (`pipeline.py`,
   `clear_scan_errors()` when `not incremental`) and **never cleared when an item later
   succeeds** on an incremental scan. So "has a `scan_errors` row" is the wrong test — it would
   skip an item that failed once and has since been tagged.
 - "Seen by the latest scan" is not quite right either, and *Frontier War* (U1, 2026-09-23)
   shows why: it was `probe_failed`, but its May row carried the file's current mtime, so **incremental scans skip
   it at the mtime filter** and its `last_seen` only moves on a full scan. After an incremental
   run it would look reachable and be planned from its May row.
 - **The rule that covers every case is per item:** unreachable if it has a `scan_errors` row
   whose `last_seen` is **later than its `media_state.last_scanned`** (or it has no row at all —
   today's `no_probe_record`). Failed-then-fixed: the fresh row is later, so it is planned.
   *Frontier War* and B11's nine: every row is older than its error, so all are reported as
   unreachable. No dependence on `scan_runs`. (Checked on a read-only copy taken 2026-09-26:
   all nine errors are from 09-26 09:00; **four of the nine rows were last written 2026-09-24
   23:2x**, not "between 2026-06-02 and 2026-08-09" as filed above — the rule holds either way.) (`no_path`/`no_file` are
   recorded before the mtime filter, on every scan.)
 - `error_type` takes **four** shapes, not the two the `ScanError` comment names: `no_path`,
   `no_file`, `probe_failed`, and `process_error: <exception text>`. Count all four in the new
   category; fix the model comment in the same PR (stale prose).
 - Acceptance: a unit test with one item per `error_type` whose error is newer than its row
   (all four reported as unreachable, none as "would change"), one whose row is newer than its
   error (planned normally), and one *Frontier War* case (row older than the error, same
   mtime). No live step; the production re-run is the operator's.

*FIXED 2026-09-26 — the sweep's rule, as specified.* `run_arr_dry_run()` now reads each item's
newest `scan_errors` row (`pipeline._unreachable_errors()`) and, when it is later than the
item's `media_state.last_scanned` (or the row has no `last_scanned`), calls
`ArrTagSync.note_unreachable()` instead of planning it. That counts the item in the report as
`items.unreachable` plus `items.unreachable_<error type>` (the `process_error: <text>` shape is
folded to `process_error`), and on each non-refused owner as `instances.<label>.unreachable`
with up to five named examples — so "would change / synced" no longer includes them. Only
owned items are counted there (an unowned item has nothing to plan). `no_probe_record` is
unchanged and still wins when there is no row. The Settings hint line, the CLI summary and the
scan log line say "Owned, but the scan cannot reach it"/"unreachable"; the scan log only when
non-zero. **Inert on the scan and webhook paths:** neither consults `scan_errors` nor calls
`note_unreachable()`, and a test drives `_process_one_item()` with a newer error row present and
asserts the same single `sync_item()` call and zero unreachable counts. The `ScanError`
comment now names all four `error_type` shapes.

Tests (`tests/test_arr_sync.py`): one item per `error_type` whose error is newer than its row —
this one **failed on `origin/main` with `would_change` 4 where 0 is right**; a failed-then-fixed
item (planned normally); *Frontier War* (May row, same mtime, a later incremental `scan_runs`
row — still unreachable); an error with no row (still `no_probe_record` only); and the scan-path
inertness test. Suite **220 → 225 passed**.

**Measured on a copy of production `state.db`** (+ `-wal`/`-shm`, taken 2026-09-26 after the
05:28 local write; hash unchanged after the read) with `scripts/measure_unreachable_rows.py`,
which applies the same classifier with no Jellyfin or \*arr call (so ownership is *not*
determined there) and refuses to report unless it agrees with a one-query SQL reference; its
self-test fails if the classifier returns nothing or marks everything new. Of **10,583** index
rows, **10** Jellyfin items have an error row: **9 unreachable**, all `probe_failed` — exactly
the nine empty series named above (errors 2026-09-26 09:00Z; rows 2026-06-02 to 2026-09-24) —
**1 with no row** (*Mating Season*, the `no_probe_record: 1`), and **0** fixed since. The
production dry-run re-run, which puts the per-instance numbers on it, is the operator's.

**B9 — FILED 2026-09-25, found while fixing B5. Not fixed here.**

Measured on the dev instances, which run production's exact versions: **Radarr 6.3.0.10514
answers `400 "Allowed characters a-z, 0-9 and -"`** for `xt-H.265`, `xt-DD+ Atmos`, `xt-HDR10+`
and `xt-TrueHD Atmos`; **Sonarr 4.0.18.2978 accepts all of them** (it lowercases, as both do).
B5 ships the measured rule (`RadarrClient.LABEL_PATTERN`): a label Radarr would refuse is
reported and skipped, never attempted, and a 400 at create time is handled the same way — so
nothing breaks, but those tags never reach Radarr. The production dry run (below, in B5) puts a
number on it — label applications refused:

| label | radarr/general | radarr/4k |
|---|---:|---:|
| `xt-h.264` | 3,062 | — |
| `xt-h.265` | 981 | 67 |
| `xt-dd+` | 468 | 10 |
| `xt-"e` (that is B7) | 1 | — |

So the video-codec tag would be missing from roughly 3 in 5 films on radarr/general (4,043 of 6,862). NEEDS DECISION because every fix
changes the tag vocabulary and there is a trap in the obvious one: stripping the illegal
characters turns `xt-hdr10+` into `xt-hdr10`, **colliding with HDR10**, and `xt-dd+` into
`xt-dd`, **colliding with Dolby Digital**. The choices are (a) an explicit map (`+` → `plus`,
`.` → `-`: `xt-hdr10plus`, `xt-ddplus`, `xt-h-264`) applied to Radarr only, so Radarr's labels
differ from Jellyfin's and Sonarr's; (b) the same map applied everywhere, which renames tags
already on ~9,400 Jellyfin items; or (c) leave Radarr without them.

*Sweep 2026-09-26 — **NEEDS DECISION**, confirmed.* **Question:** which spelling do the codec
tags take, and where? **Recommendation: (b), one vocabulary legal in every destination** — e.g.
drop `.`, `+` → `plus`, space → `-` (`xt-h264`, `xt-h265`, `xt-ddplus`, `xt-ddplus-atmos`,
`xt-hdr10plus`, `xt-truehd-atmos`). Why: every cross-destination comparison (B5's read-back
cross-check, B12's drift, any future reconciliation) then compares strings directly instead of
through a map, and (c) hides metadata, which the 2026-09-23 P7+U8 direction rejects. What it
costs, checked in code: tag strings are built in `tagger.py` from the scanner's display names
(`"H.265"`, `"DD+"`), so a map applied in `build_tags()` renames **tags only — poster badge text
is unaffected**; the map must enter `_tag_config_hash()`, which makes the next scan a full
re-tag (~1 h) that replaces the old tags on Jellyfin and Sonarr by itself; Sonarr keeps the old
labels (`xt-h.264`…) as unused entries in its tag list. **Also ask:** does anything outside
xenotag — a Jellyfin smart collection, a filter, a script — select on the dotted spellings? If
yes, (a) is the safer choice. Acceptance for whichever spelling: a test over the full emitted
vocabulary that no two source labels map to the same output (the `hdr10+`/`hdr10` trap).

**OPERATOR DECISION 2026-09-26:** (b) — one spelling legal in every destination (`xt-h264`, `xt-h265`, `xt-ddplus`,
`xt-ddplus-atmos`, `xt-hdr10plus`, `xt-truehd-atmos`), tags only with badge text unchanged, the map
entering `_tag_config_hash()` so the next scan is one full re-tag; first check that nothing outside
xenotag selects on the dotted names, and if something does, fall back to (a), a Radarr-only map.
**Release and deploy are authorised** for this item.

*FIXED 2026-09-27 — option (b), as decided.*

**Nothing outside xenotag selects on the old spellings** (checked first, read-only, 2026-09-27
05:3xZ), so (b) applies, not the Radarr-only fallback:

 - `grep -rIiF` for `xt-h.264`, `xt-h.265`, `xt-dd+`, `xt-hdr10+`, `xt-truehd atmos`,
   `xt-dd+ atmos` — and, since the rule respells ratings too, `xt-not rated`, `xt-ma15+`,
   `xt-ma 15+`, `xt-16+` and the other `+` ratings — over `~/docker` (text files, no `.gz`/`.git`)
   and `~/dev` except this repo: the only hits are xenotag's own reports and scripts and the
   overnight notes about this item. Both greps found a planted control file. Unreadable to the
   search: crowdsec's hub, Home Assistant's auth store, a gitea ssh dir, pihole logrotate,
   calibre's shader cache, jf-users' db, a wireguard token — none a media-tag selector.
 - Jellyfin (production, 10.11.10, GET only): none of 27 users has `BlockedTags`/`AllowedTags`
   (parental-control tag filters, the one native feature that selects by tag); no smart-playlist
   or auto-collection plugin among the 16 installed; the 480 collections and 15 playlists are
   static lists.
 - All five \*arrs, `GET /api/v3/tag/detail`: **0** of the 142 `xt-` labels has any use besides
   series/movie ids (delay/release profiles, restrictions, indexers, download clients,
   notifications, import lists, auto-tagging). The check can see one: operator labels such as
   `luxe` and `core-tv` show their auto-tagging use. Sonarr holds `xt-h.264`/`xt-h.265`/`xt-dd+`
   (general, anime) and `xt-h.265`/`xt-dd+` (4k); Radarr holds none.

**The spelling.** `tagger.tag_label()` drops `.`, spells `+` as `plus` and a space as `-`, and
`build_tags()` applies it to every label after the prefix (the prefix is config, kept verbatim).
Case is left alone — every \*arr lowercases a label itself — so Jellyfin carries `xt-H264`,
`xt-H265`, `xt-DDplus`, `xt-DDplus-Atmos`, `xt-HDR10plus`, `xt-TrueHD-Atmos`, and the \*arrs
store exactly the decided `xt-h264` … `xt-truehd-atmos`. **Badge text is unchanged**: badges are
built from the display names by `_make_badge_groups()`, which never calls `build_tags()`; 24
`generate_preview_bytes()` renders (four profiles carrying every respelled label, three badge
sizes, two poster sizes) are byte-identical before and after, and all 24 differ from each other.

**Ratings are respelled too**, because the rule is the spelling of every tag and production
sends ratings to Jellyfin (`tags.destinations.rating: [poster, jellyfin]`). On the index,
**348** items carry one: `xt-Not Rated` 188, `xt-MA15+` 86, `xt-16+` 33, `xt-12+` 14, `xt-18+` 9,
`xt-MA 15+` 5, `xt-14+` 4, `xt-6+` 3, `xt-15+` 3, `xt-0+` 2, `xt-R18+` 1 → `xt-Not-Rated`,
`xt-MA15plus`, `xt-16plus`, … (`MA15+` and `MA 15+` stay distinct: `MA15plus`, `MA-15plus`). No
item on the index carries `HDR10+`, `DD+ Atmos` or `TrueHD Atmos` today. B7's `xt-"E` is not
touched (`"` is B7's to fix) and stays the one label Radarr refuses.

**The re-tag.** `_tag_config_hash()` hashes config, so a spelling change in code would not
move it and an unchanged file would keep the old spelling forever. It now always folds in
`|vocab:{TAG_VOCABULARY}` (`tagger.TAG_VOCABULARY = 2`; 1 = display names verbatim): the
shipped defaults go `3163f57ce472c152` → `09d02a2ffe47df66`, production's config
`aabcd4f06c79b714` → `811c4c22ae05ed4a` (the old value reproduced from a replica of production's
tag settings first). The upgrade's first scan is therefore a full re-tag. The old labels go by
the existing managed-prefix sweep — on Jellyfin `set_managed_tags()` keeps only non-`xt-`/`mf-`
tags, on the \*arrs `plan_tags()` removes any managed tag not desired — which was **proved on the
dev stack**, not assumed: a full scan with `origin/main`'s code (hash `aabcd4f06c79b714`, the
same as production's) left `xt-H.264` on all 6 Jellyfin items and `xt-h.264` on Sonarr's Firefly
(Radarr refused it for Serenity); an **incremental** scan with this code then logged "Tag config
changed — forcing full re-tag" and left `xt-H264` on all 6 items (Jellyfin **12.1.0** — dev's
version, see I14), `xt-h264` instead of `xt-h.264` on Firefly (Sonarr 4.0.18.2978), `xt-h264`
added to Serenity with 0 refusals (Radarr 6.3.0.10514), every non-`xt-` tag unchanged and 0
read-back failures. Sonarr keeps the old label as an unused entry in its tag list, as expected;
they are harmless and are left (deleting a label is an outward write nobody asked for).

**Tests** (`tests/test_tag_vocabulary.py`, and the pinned values in `tests/test_arr_sync.py`):
over the full emitted vocabulary — the scanner's resolution, video and audio tables, every
branch of `_detect_hdr()` and of `_normalize_audio_codec()`, every language, `sub-` tags, the
two audio-count tags and every rating on production's index — every label is legal in Radarr
and no two share a spelling once lowercased. The same checks **fail on the naive strip map**
(`hdr10`: HDR10/HDR10+, `dd`: DD/DD+, `ma15`: MA15+/MA 15+) and on the old spelling; a scan-path
test swaps `xt-h.265`/`xt-dd+` for `xt-h265`/`xt-ddplus` on a Sonarr object and lands them on a
Radarr one, operator tags kept. Suite **272 → 284** passed. `RadarrClient.LABEL_PATTERN` stays
as the guard. Evidence and scripts: `~/docker/xenotag/b9-retag-20260927/`.

**B9 — LIVE 2026-09-27 (release v1.8.0). Production re-tagged 09:00–10:15Z; every \*arr object
and every Jellyfin item read back.**

**Release.** v1.8.0 — `bump=minor`, because a tag-vocabulary change is user-visible (release
commit `eefda5d`, published 05:47:40Z, `docker-publish` green, `ghcr.io/bpoulliot/xenotag:1.8.0`
= `:latest`). It carries everything merged since v1.7.0: **B9** (#88), **I3** Alembic (#78),
**I12** (#77), **B11** (#76), **I10** (#74), **P5** (#79), the B13 / I7 / B8 probe scripts
(#81, #83, #84), I13's pinning tests (#85), and roadmap-only PRs (#72, #73, #75, #80, #82, #86,
#87).

**Deploy** at 05:49:43Z (23:49 MDT; no scan running — the last had ended 09-26 09:00:58Z).
`state.db` was backed up first (`config/state.db.bak-20260927-pre-b9` + `-wal`/`-shm`; checked:
no `alembic_version` table, hash `20142cb0e93c4394`), then pull and
`docker compose up -d --no-deps xenotag`; healthy. **This was the first production run of I3's
stamp** and it stamped rather than refused: the log reads `Running stamp_revision -> 0001` then
`state.db schema: stamped (revision 0001)`, and a copy of the live database has
`alembic_version = 0001`.

**The re-tag** was the scheduled 03:00 MDT scan, 09:00:00Z → 10:14:59Z (75 min). `scan_runs` 147
records it as `incremental`, the type it started as; the hash differed, so Phase 1b queued all
9,399 items. 9,388 tagged, 9,340 posters rewritten, 11 `probe_failed` (B11's empty series,
*Mating Season*, and ffprobe timeouts on *Frontier War* and *Adults*). The stored hash is now
`811c4c22ae05ed4a`, as predicted.

| instance | objects written | labels created | already current | refused | read-back failures / errors / halts |
|---|---:|---:|---:|---:|---|
| sonarr/general | 745 | 3 | 309 | 0 | 0 / 0 / 0 |
| sonarr/4k | 7 | 2 | 0 | 0 | 0 / 0 / 0 |
| sonarr/anime | 859 | 3 | 496 | 0 | 0 / 0 / 0 |
| radarr/general | 4,365 | 3 | 2,498 | 1 (`xt-"e`, B7) | 0 / 0 / 0 |
| radarr/4k | 67 | 2 | 2 | 0 | 0 / 0 / 0 |
| **total** | **6,043** | **13** | **3,305** | **1** | **0** |

Predicted beforehand from the snapshots (each object against its own Jellyfin item, respelled):
748 / 7 / 859 / 4,362 / 67.

**Read back** with `b9verify.py` (self-test: a clean re-tag passes and 8 planted faults — operator
tag removed, other field changed, old label left, label deleted, crossed tag, Jellyfin user tag
lost, old spelling left on Jellyfin, HALTED report — each fail), the 08:55Z snapshot against
10:15Z, every object. Two pre-scan snapshots three hours apart differed in nothing, so every
difference below is the scan's.

 - **\*arrs:** no label deleted or renamed; the 13 new ones are `xt-h264`, `xt-h265` and
   `xt-ddplus`. **0 operator tags changed and 0 other fields changed on all 9,618 objects.**
   Sonarr: 1,607 of the 1,609 objects that carried a dotted label now carry the new one instead;
   the other two are *Adults* (not reached) and *Daredevil: Born Again*, whose file changed (now
   `xt-h265` + `xt-dd`, correctly). Radarr: the 4,432 films written gained only the three new
   labels and lost nothing — **4,589 label applications** (`xt-h264` 3,062, `xt-h265` 1,048,
   `xt-ddplus` 479), exactly the number B9 was filed with.
   Twins: all 69 radarr/4k and all 7 sonarr/4k objects equal their own Jellyfin item, and no
   HD/4K pair shares a tag set.
 - **Jellyfin**, read from a copy of `jellyfin.db` (see the trap below): of the 6,143 items that
   carried a dotted, plus or space tag, **6,117 are respelled**; 9,363 of 9,399 carry exactly
   their old set respelled; **0 non-managed tags lost** (one gained: *Boruto*'s `dual-audio`, from
   Sonarr's NFO). 180 items read through `/Items?Ids=` (150 re-tagged, 30 not) all equal the
   database, with 0 old spellings among the 150. `Tags=xt-H264` finds 3,660 items; `Tags=xt-H.264`
   finds 18.
 - **The 26 not respelled:** 3 of B11's empty series and *Adults* (not reached), and **22 films
   whose write Jellyfin undid** — each re-saved 60–700 ms after xenotag's own refresh call, with
   its old tags (filed **B17**). Their Radarr copies carry `xt-h264` and the like; `state.db`
   says they are tagged, so only the next full re-tag reaches them.
 - The other 10 whose set changed beyond the respelling are B12's five films (their `xt-` tags
   are back), *Frontier War* (changed by Jellyfin itself on 09-26 — B12's note), *Daredevil*, and
   three series that had no `xt-` tag before and have one now.

**The trap: the listing is stale (filed B18).** The first read-back used the recursive `/Items`
listing, as B5's did, and reported **0 of 9,399 items changed** while `jellyfin.db` held the new
tags. That listing served stale `Tags` for every item whose tags had changed since some moment
between 2026-09-26 09:01Z and 19:05Z; `Ids=`, `SearchTerm=`, the `Tags=` filter and the database
were current. Read Jellyfin tags back by `Ids=` or from a database copy.

**Seen, not B9's:** 8 Jellyfin tag writes timed out client-side on the largest series and landed
anyway (B17); 6 overlay errors `Permission denied` on `folder.jpg` under `/media/luxe/tv/anime/`
(poster side, file permissions); *Cunk on Britain* (sonarr/general) and *Laughing Target*
(radarr/general) carry no `xt-` label although their Jellyfin items do — untouched by this scan,
and the same before it. The \*arrs will write the new labels into their NFO files at their next
refresh (B12's note).

**Where things are:** `~/docker/xenotag/b9-retag-20260927/` (its README lists every snapshot and
script). **Back out** (not needed): redeploy `:1.7.0` — its hash differs, so its first scan
re-tags to the old spelling, which Radarr refuses again.

**B8 — SHIPPED 2026-10-06 ([#117](https://github.com/bpoulliot/xenotag/pull/117)); released in v1.11.0 (2026-10-07).**
Built to the Spec (READY) below. `_resolve_webhook_jf_item()` matches Sonarr's `series.path` /
Radarr's `movie.folderPath` against `JellyfinClient.list_item_paths()` (a `Fields=Path` listing of
`Series` or `Movie` only) with B5's `_norm_path()` and `item_folders()`, imported from
`app/arr_sync.py`. One match is fetched by `get_item_by_id()`, none is logged "not in Jellyfin yet",
more than one is logged and refused, a payload with no path returns `None` without a request.
`find_item_by_provider_id()` is deleted — grep found no caller beyond the two replaced branches.
`get_item_by_id()` asks for `ITEM_FIELDS`, so the Jellyfin branch gets `Path`. Tests:
`tests/test_webhook_resolution.py` (15; 14 fail on the pre-fix code). Still unwired (U11), so nothing
runs this path until a webhook exists.

**B8 — FILED 2026-09-25, found while fixing B5. Not fixed here.**

Two defects in `handle_webhook()`'s item resolution, both latent — the production container
logged **0** webhooks in the 30 days to 2026-09-25:

 1. **Sonarr/Radarr payloads resolve to the wrong item.** `find_item_by_provider_id()` asks
    `/Items?AnyProviderIdEquals=Tvdb.<id>`. Jellyfin 10.11.10 ignores the parameter: a read-only
    GET on production for The Expanse (`Tvdb.280619`) returned `TotalRecordCount` **9,419** —
    the whole library — with *Accident Man* first, and the function returns `items[0]`. Reproduced
    end to end on dev: a Sonarr `Download` webhook for Firefly (tvdb 78874) probed, tagged and
    re-badged **"Anime Show (2023)"**. The wrong item is processed *consistently* (its own file's
    tags go to its own Jellyfin item, and B5's sync derives the \*arr owner from that item, so no
    \*arr is cross-tagged) — but the item the webhook was about is not processed at all.
 2. **Jellyfin `ItemAdded` payloads resolve to an item with no path.** `get_item_by_id()` asks for
    `Fields=Tags,Genres,Studios,ProviderIds,Overview,OfficialRating` — no `Path`, no
    `MediaSources` (checked on dev: neither key is in the response) — so `handle_webhook()` always
    logs "no accessible file" and returns.

NEEDS MEASUREMENT: which Jellyfin 10.11 query filters by provider id server-side (or whether a
client-side filter over a `ProviderIds`-only listing is the fix), measured on dev before choosing.

*Sweep 2026-09-26 — **NEEDS MEASUREMENT**, confirmed; queueable.* **What:** on **jellyfin-dev**
(which runs **12.1.0**, not production's 10.11.10 — see I14) with the seeded *Firefly* / *Serenity*: (1) whether any `/Items` filter
narrows by provider id server-side — the `AnyProviderIdEquals` spellings, `HasTvdbId`/`HasTmdbId`
plus a client-side check — recording `TotalRecordCount` for each; (2) the **folder** route, which
needs no provider query at all: Sonarr's payload carries `series.path`, Radarr's
`movie.folderPath`, and B5 already matches \*arr objects to Jellyfin items by folder — measure
whether a path match resolves both fixtures; (3) on production, read-only, the time and bytes of
one `/Items?Recursive=true&IncludeItemTypes=Series,Movie&Fields=ProviderIds,Path` listing (the
client-side fallback's cost); (4) read-only `GET /api/v3/notification` on all five \*arrs — does
any of them even have a webhook pointed at xenotag? (0 webhooks logged in 30 days suggests none;
if none, B8's value is nil until one is added, which is itself worth recording.) **Roughly 1–2 h.**
Part 2 of the defect (`get_item_by_id()` requests no `Path`/`MediaSources`) needs no
measurement — it rides along with whichever fix follows.

**B8 — MEASURED 2026-09-27 (04:07–04:40Z), relabelled READY.** Instrument:
`scripts/measure_webhook_resolution.py` (`--self-test` runs first on every invocation and fails
on a sabotaged classifier, filter verdict or path normaliser; the resolver it scores against is
B5's own `InstanceIndex`/`item_folders()`, imported rather than copied). Only GETs reached
production Jellyfin and the five live \*arrs; prod xenotag was idle and its next scan is 09:00Z.
The dev \*arrs got two throwaway webhooks and a throwaway film and series, all removed again.

 1. **No `/Items` parameter filters by provider id, on either version.** Production's own
    OpenAPI document (10.11.10) lists **86** `/Items` query parameters: none is a provider-id
    equality and none is a path filter; the only provider parameters are the booleans
    `hasTvdbId`/`hasTmdbId`/`hasImdbId`. Eight spellings were sent anyway, each for an id in the
    library and for one that is not (`987654321`, checked absent first):
    `AnyProviderIdEquals=` `Tvdb.id` (today's) / `tvdb.id` / `Tvdb:id` / `Tvdb=id`,
    `ProviderIds.Tvdb=`, `TvdbId=`, `tvdbId=`, `HasAnyProviderId=` — and the same eight for Tmdb.
    **Production: all 16 return `TotalRecordCount` 9,399 for the present id AND the absent one** —
    the whole library, i.e. ignored. jellyfin-dev (12.1.0): the same, 6 of 6 every time, so a
    Jellyfin upgrade does not fix this. `searchTerm=<id>` returns 0 both ways (it searches names).
 2. **The folder route resolves everything B5 can own, and nothing wrong.** Every object in every
    production catalogue (9,618: radarr 7,106, radarr-4k 75, sonarr-general 1,074, sonarr-4k 7,
    sonarr-anime 1,356) was turned into the payload its webhook would carry and resolved against
    one production listing, scored against B5's ownership (id AND folder agree):

    | Route | right | wrong | more than one | none | one item, no B5 owner |
    |---|---:|---:|---:|---:|---:|
    | **folder** (`item_folders()` of the payload path) | **9,359** | **0** | **0** | 257 | 2 |
    | provider id, filtered client-side | 9,209 | 0 | 150 | 257 | 2 |
    | folder, then provider id if folder finds none | 9,359 | 0 | 0 | 256 | **3** |

    **HD/4K twins:** all 133 films and 12 series that exist in both instances resolve to their own
    copy by folder (145/145); by provider id, 1 of 145 is right and 144 return both copies. The 6
    other provider-id multiples are unrelated items sharing an id (e.g. *Heavenly Chronicles* and
    *Utsu no Miko* on one Tvdb id). **The provider-id fallback adds 0 right answers and 1 wrong
    one:** radarr-4k's *Mission: Impossible – Rogue Nation* has no Jellyfin item, and its Tmdb id
    finds the HD film, which a 4K webhook must not process. The 257 folder misses are all objects
    with no Jellyfin item: 247 films with `hasFile: false` and 10 series with 0 episode files (9
    sonarr-general, 1 sonarr-anime; none has a Jellyfin series of its name either). The 2 "no B5 owner" cases are right for a webhook: *Laughing Target*'s
    Jellyfin item has **no provider ids at all** (only the folder can find it), and *Cunk on
    Britain* is B5-ambiguous (Jellyfin's Tmdb id is also *Cunk on Earth*'s), so B5 still refuses
    the \*arr write. Dev fixtures: Firefly and Serenity both resolve by folder and by id (1/1 each).
 3. **Payload fields, captured from real events on the dev \*arrs** (Sonarr 4.0.18.2978 and Radarr
    6.3.0.10514, production's versions; `SeriesAdd`/`SeriesDelete`, `MovieAdded`/`MovieDelete`,
    `Test`): Sonarr sends `series.path`, Radarr `movie.folderPath`, and each **equals the object's
    `path`**. Both also carry `instanceName`, but all five production instances are named plain
    "Sonarr"/"Radarr", so it cannot tell HD from 4K; the folder does.
 4. **Cost of the listing:** `/Items?Recursive=true&IncludeItemTypes=Series,Movie&Fields=ProviderIds,Path`
    on production returned 9,399 items (6,944 films, 2,455 series) in **14,592,300 bytes**, in
    0.213 / 0.314 / 0.235 s over three consecutive calls from the host (warm; a cold cache was not
    measured). Adding `MediaSources` makes it **38.7 MB and 17.1 s**, so the listing must not ask
    for it. For scale: a webhook already fetches the whole radarr catalogue (37 MB) when \*arr sync
    is on.
 5. **Part 2, confirmed on production:** `get_item_by_id()`'s fields return neither `Path` nor
    `MediaSources` (a film and a series checked); adding `Path` returns it. `MediaSources` is not
    needed: 138 films have more than one media source (the *Merge Versions* plugin), and for **all**
    of them `MediaSources[0].Path` is the item's own file (0 mismatches of 6,944 films).
 6. **Is any webhook pointed at xenotag? No.** `GET /api/v3/notification` returns an **empty list on
    all five** production \*arrs (0 notifications of any kind). The reader was checked both ways:
    it lists the webhook on the dev \*arrs while one exists and 0 after it is removed. Production
    Jellyfin has **no Webhook plugin** (16 plugins, none a webhook sender), so the Jellyfin source
    is dead too. **B8's value is nil until a webhook is added**, and adding one is a decision (U11).

**Spec (READY).**

 - **Sonarr/Radarr: resolve by folder only.** Take the payload's `series.path` (Sonarr) or
   `movie.folderPath` (Radarr), list `/Items?Recursive=true&IncludeItemTypes=Series` (or `Movie`)
   `&Fields=Path`, and keep the items where `_norm_path(payload_path) in item_folders(item)` —
   B5's two helpers from `app/arr_sync.py`, imported (or moved somewhere both modules can import),
   **never a second copy**. Exactly one match: fetch it with `get_item_by_id()` and process it.
   None: log "not in Jellyfin yet" and return; the nightly scan will reach it. More than one: log
   and return, never guess (none happen in production today). A payload with no path: return None,
   as a missing id does today.
 - **No provider-id fallback,** and delete `find_item_by_provider_id()` (its only callers are the
   two branches being replaced): no parameter can make it work on 10.11.10 or 12.1.0, and as a
   client-side fallback it only ever added the wrong twin.
 - **Part 2:** `get_item_by_id()` requests `ITEM_FIELDS` (which carries `Path`) so it returns what
   the scan's listing does. `MediaSources` is optional (item 5 above).
 - **Tests:** HD and 4K items with the same Tvdb/Tmdb id resolve to the one in the payload's folder;
   a trailing slash on the payload path still matches; a film folder never matches a series item
   or the other way round; a folder with no item returns None without a provider-id lookup; the
   Jellyfin branch receives an item with `Path`.

**Not measured, and it bounds what the fix buys.** The \*arrs notify nothing, Jellyfin included, so
Jellyfin learns of an import only from its real-time monitor (on for all 17 media libraries, with
`LibraryMonitorDelay` 60 s and `LibraryUpdateDuration` 30 s) or its 08:00 library scan. A webhook is
handled immediately, so a **new** import will usually resolve to nothing yet, and an **upgrade**
will find the item with its old file still in `Path`, which the handler already turns into "no
accessible file". Both fail safe and are left for the nightly scan. The import-to-Jellyfin lag on
production's media mounts was not measured. It matters only if U11 wires a webhook, so it belongs
to U11.

**B7 — LIVE 2026-09-27 (v1.9.0).** The release's first scan — scan 148, a full re-tag started
11:41:30Z, done 12:52:11Z — re-tagged **14 \*arr objects** (sonarr/general 3, radarr/general 11) and
created 8 labels, every one a legal language label (`xt-gd` on Sonarr; `xt-bs`, `xt-egy`, `xt-fa`,
`xt-hz`, `xt-km`, `xt-mi`, `xt-my` on Radarr); 0 read-back failures, 0 write errors, not halted. Read
back independently (`b9verify.py`, GET snapshots of every object before and after, Jellyfin read
**by id**): 0 operator tags changed, 0 non-tag fields changed, 0 labels deleted or renamed, twins
intact; on Jellyfin 82 items' managed tags changed — 56 language renames (`sub-DU`→`NL`, `GR`→`EL`,
`NO`→`NB`, `PE`→`FA`, `EG`→`EGY`, …), 5 track-number "languages" dropped (`xt-sub-10`…), 21 of B9's
leftover spellings respelled — and 0 items' non-managed tags. Record, snapshots and scripts:
`~/docker/xenotag/u2-release-20260927/` on the host (see U2).

**B7 — FIXED 2026-09-27 (released in v1.9.0, above).** Took effect at that release, and **that
release's first scan was a full re-tag with live \*arr writes**: `TAG_VOCABULARY` went 2 → 3, so
`_tag_config_hash()` changes for every install (defaults `09d02a2ffe47df66` → `d0c577fe5620e689`).

**What shipped.** Operator decision (c), as written:
- `app/iso639.py` — **all 506 ISO 639-2 codes**, bibliographic and terminologic (20 B/T pairs),
  → ISO 639-1; **203** have a 2-letter code. **Generated, never typed:**
  `scripts/generate_iso639_table.py` reads the Library of Congress list
  (`https://www.loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt`, retrieved 2026-09-27, sha256
  in the module header), validates it (five fields a line, 3-letter codes, 2-letter alpha2, no
  duplicates, known pairs both ways) and refuses to write otherwise; `--check` compares with the
  committed module. Cross-checked once against `pycountry` 26.2.16 in a scratch venv (not a
  dependency): all 440 codes both carry agree; the 66 it lacks are collective/special codes with
  no 639-1. No runtime dependency, nothing downloads at runtime.
- `scanner._lang3_to_lang2()` — 639-1 uppercase where one exists (`khm` → `KM`, `per`/`fas` →
  `FA`); else the 3-letter code uppercase (`egy` → `EGY`, `mul` → `MUL`, an ISO 639-3 `yue` →
  `YUE`); a 2-letter code as itself; `und`/`unknown`/empty → `UND` quietly; **`zxx` and anything
  not 2–3 ASCII letters → `UND` with a WARNING naming the file**. One guard the decision implies:
  a 3-letter code *outside* the table that would spell a scanner tag (`aac`, `dts`, `hlg`) →
  `UND` with a WARNING — the table's own 301 three-letter labels collide with nothing (tested).
- `MediaInfo.languages` now comes from the audio tracks: `_detect_languages()` carried a second
  copy of the old fallback (it fed the index's `languages` column and the media filter), so it
  is gone, and a bad track warns once, not twice.
- External subtitle names: only a letters-only part is a language, so `Movie.10.srt` (a track
  number) is `UND`, no longer `xt-sub-10`. This is the one behaviour beyond the decision's
  letter: a number in a file name is not a malformed language tag, so it does not warn either.
- Every emitted language tag is legal in Radarr's `[a-z0-9-]` (tested over the whole table).

**Acceptance.** `tests/test_language_codes.py` (70 tests): the table's size and shape; every one
of the 42 pre-B7 mappings keeps its output (all 42 agree with the LoC table — **no existing
mapping renames**); every code traced on production, old → new; the malformed/`zxx`/quiet
cases, with the WARNING's file name asserted; **no 3-letter fallback collides with any other
emitted tag**, and the check is shown to fail on the old rule (9 red) and without the guard (5
red); the committed module is the generator's output unedited (a one-code edit is caught).
`tests/test_tag_vocabulary.py` now runs over every language label the table can emit; it
builds the non-language vocabulary separately, because subtracting the language set from the
whole had hidden exact overlaps. That is how B19 was found.

**The renames, measured** (`scripts/measure_language_renames.py`, `--self-test` in CI). Instrument:
copies of production `state.db` and `jellyfin.db` (+`-wal`/`-shm`, 2026-09-27 ~04:40Z).
`MediaStreamInfos.Language` is ffprobe's raw tag (it holds `"eng"` with its quotes, and `bos`,
`gla` where a direct ffprobe of those files did). External subtitles are read from the file names
beside each video. Each index row is labelled by the pre-B7 rule and by the new function,
imported. **Self-check:** the pre-B7 rule reproduces the index's stored labels on **9,380 of
9,390** rows it can see (the probe refuses below 95%). Of the 10,585 rows, 1,194 have no Jellyfin
item at their path (orphans) and 1 has no file, so they were not measured. A scan never reaches
them either. **57 rows change:** 16 in audio (Jellyfin, \*arrs, poster) and 41 in subtitles only
(Jellyfin, poster). That is 131 tag removals and 93 additions.

| raw code | was | now | tracks | | raw code | was | now | tracks |
|---|---|---|--:|---|---|---|---|--:|
| `dut` | `DU` | `NL` | 19 | | `kan` | `KA` | `KN` | 2 |
| `gre` | `GR` | `EL` | 12 | | `mal` | `MA` | `ML` | 2 |
| `enm` | `EN` | `ENM` | 10 | | `mao` | `MA` | `MI` | 1 |
| `nob` | `NO` | `NB` | 10 | | `bur` | `BU` | `MY` | 1 |
| `may` | `MA` | `MS` | 8 | | `bos` | `BO` | `BS` | 1 |
| `ben` | `BE` | `BN` | 5 | | `gla` | `GL` | `GD` | 1 |
| `per` | `PE` | `FA` | 3 | | `her` | `HE` | `HZ` | 1 |
| `ice` | `IC` | `IS` | 3 | | `fil` | `FI` | `FIL` | 1 |
| `khm` | `KH` | `KM` | 2 | | `mac` | `MA` | `MK` | 1 |
| `egy` | `EG` | `EGY` | 2 | | `kaz` | `KA` | `KK` | 1 |
| `baq` | `BA` | `EU` | 2 | | `kir` | `KI` | `KY` | 1 |
| `zxx` | `ZX` | `UND` | 1 | | `mon` | `MO` | `MN` | 1 |
| `"eng"` | `"E` | `UND` | 1 | | `English[eng]`, `English` | `EN` | `UND` | 6 |
| file `.10.`…`.30.` | `10`…`30` | — (UND) | 44 | | `Japanese[jpn]`, `Japanese` | `JA` | `UND` | 3 |

Audio tags by row: `PE`→`FA` 3, `EG`→`EGY` 2, `KH`→`KM`, `MA`→`MI`, `BU`→`MY`, `BO`→`BS`,
`GL`→`GD`, `HE`→`HZ` 1 each, `ZX`→`UND` 1, `"E`→`UND` 1, and 3 `.ogm` series lose `EN JA
dual-audio sub-EN` for `UND` (B20). Subtitle tags: `sub-DU`→`sub-NL` 17, `sub-GR`→`sub-EL` 12,
`sub-NO`→`sub-NB` 10, `sub-MA`→`sub-MS`/`ML`/`MK` 9, `sub-BE`→`sub-BN` 5, `sub-IC`→`sub-IS` 3,
`sub-BA`→`sub-EU` 2; `sub-ENM` added on 10 rows beside their `sub-EN`; `sub-10`…`sub-30` removed
from 5 rows (44 tag applications). **Two the filing thought right by luck were wrong:** audio `GL` was
`gla` (Scottish Gaelic, `GD`), not Galician, and `BO` was `bos` (Bosnian, `BS`), not Tibetan. And
`HE` on *Her Body* was `her` (Herero), not Hebrew, and one episode's `fil` subtitle (Filipino) was
read as Finnish `FI`. **The three untraced codes:** `MA` ← `mao` (Māori → `MI`) in audio, and in subtitles
`may`/`mal`/`mac` (→ `MS`/`ML`/`MK`); `BU` ← `bur` (Burmese → `MY`); `EG` ← `egy` (Egyptian
(Ancient), which has no 639-1 → `EGY`).

**Worth knowing before the release:** `enm` is *Middle* English; the 10 files are surely
mislabelled English, but the table is literal, so they gain `xt-sub-ENM`. Norwegian now splits
by the code the muxer wrote (`nor` → `NO`, `nob` → `NB`). The WARNING lines name every
`"eng"`/`zxx`/`.ogm` file on each scan that probes it. Filed: **B19** (Sindhi/Divehi/South
Ndebele spell `SD`/`DV`/`NR`; 0 streams) and **B20** (the `.ogm` spellings).

**B7 — FILED 2026-09-25, found while fixing B5. Fixed 2026-09-27 (above).**

`scanner._lang3_to_lang2()` maps 42 ISO 639-2 codes and, for anything else, returns
`lang3[:2].upper()`. Evidence from the production index (copied 2026-09-25): *The Uncomfortable
Truth (2018)* (`jellyfin:cea75ca2…`) has an audio track whose language is recorded as `"E`, and
`xt-"E` is in its `tags_applied` — so it is on the Jellyfin item. The B5 dry run's label list also
carries eleven codes that are not values of `LANG_MAP` and can only have come from the fallback:
`ZX KH MA PE BU EG BO ZU TA TE GL`. Some coincide with the right ISO 639-1 code by luck (`tam`→`TA`,
`tel`→`TE`, `glg`→`GL`, `bod`→`BO`, `zul`→`ZU`); the others name no language or the wrong one
(`zxx` is "no linguistic content"; Khmer is `km`, Persian `fa`). The source codes behind `MA`,
`BU` and `EG` were not traced. NEEDS DECISION: extend the map and send the rest to `UND`, or keep
unknown codes as their uppercase 3-letter form — either renames tags already written.

*Sweep 2026-09-26 — **NEEDS DECISION**, confirmed.* **Question:** what does an audio language
code outside today's 42-entry map become? Options: **(a)** extend the map with the codes seen and
send everything else to `UND`; **(b)** keep any unmapped code as its uppercase 3-letter form
(`KHM`, `PER`); **(c)** a complete static ISO 639-2 (bibliographic *and* terminologic) → 639-1
table, with the 3-letter form only for languages that have no 2-letter code, and `zxx`
("no linguistic content") and anything malformed (`"e`, not 2–3 ASCII letters) → `UND` with a
WARNING naming the item. **Recommendation: (c).** It is the only one where every emitted code
names the right language: (a) turns real languages into `UND`, which hides metadata — the
2026-09-23 direction — and (b) keeps `PER` where every other Persian track says `FA`. Only the
wrong tags rename (`KH`→`KM`, `PE`→`FA`, `ZX`→`UND`, plus whatever `MA`/`BU`/`EG` trace to), and
the change must enter `_tag_config_hash()` so a full re-tag applies it. No new dependency: the
table is ~190 lines of constants. Acceptance: a test that no 3-letter fallback collides with a
codec tag in the emitted vocabulary.

**OPERATOR DECISION 2026-09-26:** (c) — a complete ISO 639-2 (B and T) → 639-1 table, the 3-letter form only where no
2-letter code exists, and `zxx` and malformed codes → `UND` with a WARNING naming the item.

**B6 — FIXED 2026-09-27; LIVE in v1.9.0 (2026-09-27).** Production's `config.yml` sets no badge colour, so
nothing it renders changed. Option (a) as
decided, in `ImageConfig._normalise_colour()` (`app/config.py`) over `normalize_color()`:

 - every value Pillow's `ImageColor.getrgb()` reads as **RGB** is stored as lowercase `#rrggbb`
   (`#fff`, `#FFF`, `red`, `rgb(255,0,0)`, `hsl(…)`);
 - **bare `fff` is unparseable** — Pillow 12.3.0 rejects it — so it loads as the default; pinned
   in the tests. **Bare six digits (`203a30`) are kept**, although Pillow rejects them too: the old
   `_parse_color()` stripped the `#` and drew them correctly, so refusing them would have changed a
   working poster;
 - a value Pillow reads **with alpha** (`#ffff`, `#rrggbbaa`, `rgba(…)`) is treated as unparseable,
   not stripped of its alpha: opacity is `badge_opacity`'s job;
 - unparseable (including a YAML non-string such as `000000`, which is the int 0 and used to fail
   the whole load) → the field's default and a WARNING naming `image.<field>` and the value.
   Loading never writes `config.yml`; the normalised value reaches the file only on a later save;
 - `save_config()` (raw YAML, `PUT /config`) and `save_config_from_dict()` (Settings,
   `PUT /api/settings`) validate with the context key `REFUSE_BAD_COLOURS`, so there the same value
   is a 400 with a message naming the field, and the file is untouched;
 - `/api/badge-contrast` builds an `ImageConfig`, so the chip goes through the same normaliser and
   measures what renders;
 - `overlay._parse_color()` now **raises** `ValueError` for anything but `#rrggbb` instead of
   returning black: after validation nothing else should reach it, and a loud failure beats a
   poster of black badges.

`tests/test_colour_validation.py` (41 tests) — the spellings above, caplog on the warning, both
save paths refused through the HTTP routes with the file byte-identical, and the table below
re-rendered: `#fc0` fill under each text spelling, modal pixel of the flat interior (inset 5 px)
`(255, 204, 0)`, the text colour present and **no black pixel** inside the pill, and the tile
byte-identical to the canonical `#ffcc00`/`#ffffff` render. Mutation-checked: dropping the
normaliser fails 38, restoring the black fallback in `_parse_color()` fails 1.

*Filed 2026-09-25, found while fixing B2:*

`ImageConfig`'s five colour fields are plain `str`, so `config.yml` (hand-edited or through the
raw YAML editor) accepts anything, and `overlay._parse_color()` returns `(0, 0, 0)` for every
value that is not exactly `#` plus six hex digits. Measured:

| value | parses to |
|---|---|
| `#ffffff` | `(255, 255, 255)` |
| `#fff`, `#FFF`, `fff`, `red`, `#12345`, `#1234567`, `#gggggg` | `(0, 0, 0)` — **black** |

So `badge_text_color: "#fff"` — a perfectly ordinary CSS spelling of the default — renders
**black labels** on the dark default fills, and a rendered `#fc0` fill under `#fff` text samples
as pure black on black, **1.00:1**. No error, no log line. The Settings pickers always emit
`#rrggbb`, so only the hand-edit paths reach it; the B2 warning reports such a badge truthfully
(it measures the render) but nothing says *why* it is black.

NEEDS DECISION rather than READY because there are two defensible fixes and they differ in what
they do to a config that loads today: **expand** `#rgb` and reject the rest at validation (a
config with `red` would then fail to load — or be migrated), or **validate only** and refuse the
save. Either is a small change; which one is the operator's call.

*Sweep 2026-09-26 — **NEEDS DECISION**, confirmed.* **Question:** what happens to a colour that is
not `#rrggbb`? Options: **(a)** normalise every colour Pillow's `ImageColor.getrgb()` parses
(`#fff`, `red`, `rgb(…)`) to `#rrggbb` at validation; anything unparseable loads as that field's
**default with a WARNING** naming the field and value, and a Settings or raw-YAML save of it is
refused with a message; **(b)** expand `#rgb` only and fail validation on anything else, so such
a config stops the app loading; **(c)** validate on save only, so hand-edits keep rendering black.
**Recommendation: (a).** Every spelling that looks right then renders right, nothing renders
black silently, and it follows the precedent B10 set: an unknown `rating_position` in
`config.yml` falls back with a warning rather than stopping the app. (b) turns a cosmetic typo
into an outage; (c) leaves the defect in the only path that produces it.

**OPERATOR DECISION 2026-09-26:** (a) — normalise anything Pillow's `ImageColor.getrgb()` parses to `#rrggbb`; an
unparseable value loads as the field default with a WARNING, and saving it is refused.

**B5 — LIVE 2026-09-26 (release v1.7.0). All five instances written and read back; `arr_sync.mode: live` since 11:28Z.**

Operator decision 2026-09-26: B5 was a bug fix, so it goes live, one instance first.

**Release.** v1.7.0 (published 2026-09-26 03:30Z, `docker-publish` green, image revision
`f2f83db`) already carried B5, and production had run it since 08:40Z. `main` differed from it
only in this file, so no release was cut for the go-live.

**Dry run in production** (11:09Z, `python -m app.arr_sync --dry-run --db /config/state.db`, 38
GETs, 0 blocked): **35,402 tags / 9,356 objects / 142 labels**, against last night's 35,492 /
9,378 / 142 — −0.25%, all library change: Jellyfin series 2,477 → 2,453 and sonarr/general
objects 1,095 → 1,074 since 09-25; radarr/general +1 film and +1 newly probed. Cert fallback
would fill 0 of 1,361 — left **off**.

**How one instance went first.** `arr_sync.mode` is global, so the rollout used a one-instance
driver: a copy of `run_arr_dry_run()` that builds **one** \*arr client and runs the shipped
`ArrTagSync` with `mode=live` — matching, planning, the bulk-editor write, the read-back and the
halt all unchanged. Jellyfin stayed behind `ReadOnlyTransport`; the \*arr client got an
allowlist transport (GET, `POST /tag`, `PUT /{series,movie}/editor` — anything else raises
before sending). Per instance: a read-only pass of the driver had to reproduce the official dry
run field for field (it did, all five); a 1-object canary on the first Sonarr and the first
Radarr; then the rest, bracketed by GET-only snapshots of every object.

| instance | objects tagged | tags | labels created | refused (B9) | read-back failures / errors / halts |
|---|---:|---:|---:|---:|---|
| sonarr/general | 1,052 | 4,244 | 33 | 0 | 0 / 0 / 0 |
| sonarr/4k | 7 | 34 | 8 | 0 | 0 / 0 / 0 |
| sonarr/anime | 1,355 | 7,339 | 35 | 0 | 0 / 0 / 0 |
| radarr/general | 6,864 | 23,466 | 47 | 4,514 | 0 / 0 / 0 |
| radarr/4k | 69 | 283 | 19 | 77 | 0 / 0 / 0 |
| **total** | **9,347** | **35,366** | **142** | **4,591** | **0** |

**Read back, independently of the in-write check** (`verify.py`, self-test: a clean write passes
and six planted faults — user tag removed, other field changed, managed tag missing, label
deleted, crossed tag, write to an unplanned object — each fail). The final snapshot (11:27Z)
against the one taken before anything was written (11:12Z), **every object on every instance**,
not a sample:

 - **9,347 / 9,347** tagged objects carry exactly the planned tags; the 271 others are
   untouched (sonarr/general 22, anime 1, radarr/general 242, radarr/4k 6).
 - **No existing tag removed:** the 7,812 objects that carry an operator tag carry the same
   ones. No label deleted or renamed; the 142 new ones are all `xt-`.
 - **No other field changed** on any of the 9,618 objects (the volatile fields the read-back
   ignores excepted).
 - **Twins took only their own copy's tags.** Every tagged copy was planned from the Jellyfin
   item in its own folder. The 6 series in both Sonarrs: all 6 HD copies `xt-1080p`, all 6 4K
   copies `xt-4k` — e.g. *The Expanse* `[1080p, av1, en, opus]` on general, `[4k, dts-hd, en,
   h.265, hdr10]` on 4k. The 69 films in both Radarrs: 64 split HD / `xt-4k`; in 2 the 4K copy
   is a cropped 2160p file tagged `xt-1080p` from its own probe (**B13**); in 3 one or both
   copies have no item and were not written.
 - **Cross-check against Jellyfin:** every object's labels must also be on its own Jellyfin
   item (lowercased). True for 9,341; the 6 exceptions are items whose Jellyfin tags are
   gone — filed as **B12**, not a write fault (their \*arr tags come from probes of the files
   on disk; *Frontier War*'s May row has the same mtime as the file today).

**Nine written, then removed (B11).** That cross-check caught 5 sonarr/general series whose
Jellyfin item has no `xt-` tag; the cause was 9 series in all whose folders hold no video file,
which every scan fails to probe and skips — so their rows (June–August) described deleted files,
and a live scan would never have written them. They were reverted through the same editor
(`applyTags: remove`, read back) to their baseline, and the driver skipped any item the latest
scan could not reach on the other four instances (0 there). Hence 9,347 = 9,356 − 9, and
35,366 = 35,402 − 36.

**Switched on:** `config.yml` gained `arr_sync: {mode: live, certification_fallback: false}`
(3 lines, the rest byte-identical), container restarted healthy. The tag-config hash goes
`20142cb0e93c4394` → `aabcd4f06c79b714`, so **the 03:00 scan on 2026-09-27 is a full re-tag** —
expect ~1 h (the last two full scans took 55 and 76 min) and near-zero \*arr writes, since
every reachable object is already current. That scan is the first run of the live path from
a scan; read its report (`written`, read-back, **HALTED**). *(It ran as B9's re-tag instead —
6,043 \*arr writes, 0 read-back failures, not halted: see "B9 — LIVE 2026-09-27".)*

**Where things are:** the `state.db` backup is
`~/docker/xenotag/config/state.db{,-wal,-shm}.bak-20260926-pre-b5-live`; scripts, every report
and every snapshot (the 11:12Z one is the record of each object's tags *before* B5) are in
`~/docker/xenotag/b5-golive-20260926/`. **Back out:** delete the three `arr_sync` lines and
restart (stops writes, removes nothing); to strip the tags, the recipe in step 6 below.

**Still open:** B9 (Radarr refuses `xt-h.264`/`xt-h.265`/`xt-dd+` — 4,591 applications skipped,
so ~3 in 5 radarr/general films have no codec tag there; FIXED and LIVE 2026-09-27), B7 (odd language labels such as
`xt-zx`, `xt-ma` now exist as \*arr labels; FIXED 2026-09-27, LIVE in v1.9.0), B8 (a webhook would process the wrong item; now
live, but 0 webhooks in 30 days and B8 cannot cross-tag), B11 (FIXED 2026-09-26), B12, B13.

**B5 — FIXED 2026-09-25. Sonarr/Radarr writes shipped OFF (dry run); going live was the operator's step (done 2026-09-26, above).**

Matching now uses what Jellyfin supplies — `Tvdb` / `Tmdb` / `Imdb` against the `tvdbId` /
`tmdbId` / `imdbId` of the catalogue `preload()` already fetched — in `app/arr_sync.py`. Nothing
was written to any production \*arr: the only production run was the read-only dry run below.

### What production looks like, measured (dry run, 2026-09-26 02:37Z)

A throwaway container from this branch's code on `docker_frontend` + `docker_vpn`, production's
`config.yml` mounted read-only, a copy of `state.db` (+ `-wal`/`-shm`) opened `mode=ro`, and
every client — Jellyfin included — behind `ReadOnlyTransport`. Guard self-test **PASS** (a GET
reaches the inner transport; POST/PUT/DELETE/PATCH raise before it, and a POST through a real
transport at a closed port raises the guard's error, not a connection error). The run sent
**39 GETs, 0 blocked**. 9,420 Jellyfin items: 2,477 series, 6,943 films.

| instance | objects | labels (xt-) | id-matched (by) | owned | same id, other folder | ambiguous | claimed twice | WOULD change | tags to add | labels to create | label applications refused |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| sonarr/general | 1,095 | 12 (0) | Tvdb 1,092 | 1,086 | 6 | 1 | 0 | 1,085 | 4,376 | 33 | 0 |
| sonarr/4k | 7 | 0 (0) | Tvdb 13 | 7 | 6 | 0 | 0 | 7 | 34 | 8 | 0 |
| sonarr/anime | 1,356 | 10 (0) | Tvdb 1,361, Tmdb 1 | 1,355 | 7 | 0 | 0 | 1,355 | 7,339 | 35 | 0 |
| radarr/general | 7,105 | 20 (0) | Tmdb 6,929 | 6,863 | 66 | 0 | 0 | 6,862 | 23,460 | 47 | 4,512 (B9) |
| radarr/4k | 75 | 3 (0) | Tmdb 136 | 69 | 67 | 0 | 0 | 69 | 283 | 19 | 77 (B9) |

"By" is the most authoritative key that hit; no match anywhere needed `Imdb`. **Going live would
write 35,492 tags onto 9,378 series/films and create 142 labels**; nothing would be removed (no
managed tag exists anywhere). Items that match no instance — **series 29**: AniDB-only 13, same
id in another folder 7, not in any instance 8, ambiguous 1; **films 11**: not in any instance 6,
no provider id 3, AniDB-only 2. Two owned items have no probe record yet. After the run,
`GET /api/v3/tag` on all five: **zero `xt-`/`mf-` labels**, totals 12 / 0 / 10 / 20 / 3 — the
baseline above, unchanged.

### The traps, and what each turned out to be

 1. **An \*arr id is per instance** — and the trap is bigger than the id. **The same title lives
    in two instances**: 133 films match by id in both radarr/general and radarr/4k, 12 series in
    both sonarr/general and sonarr/4k (the HD and the 4K copy). Id-only matching, even resolved
    per instance, would write the 4K file's tags onto the HD copy and back again every scan. So a
    match must also agree on the **folder**: the object's `path` must be the Jellyfin item's
    folder. Every container here mounts media at the same `/media/...` paths, so they compare
    directly — and with that check **no item has more than one owner**. (A deployment whose
    \*arrs see different paths gets "same id, other folder" for everything; the report says so.)
 2. **`_get_or_create_tag()` POSTs** — the dry run counts "labels to create" from the preloaded
    label list and never calls it. Belt and braces: while `arr_sync.mode` is not `live`, every
    \*arr client is built on `ReadOnlyTransport`, so no code path *can* send a write.
 3. **Whole-object PUT from a cache** — demonstrated on dev: after an operator edit, the old
    `PUT /series/{id}` of the preloaded copy **reverted `monitored` and the quality profile**. The
    old `set_managed_tags()` is deleted. Writes now use the bulk editor (`PUT /series/editor`,
    `PUT /movie/editor`) with `applyTags: add` / `remove` and the managed tag ids only — verified
    on dev: add unions, remove removes exactly the listed ids, a user tag survives every step, and
    **no other field changes** (HTTP 202). Before writing, the object is re-fetched and its id and
    folder re-checked; afterwards it is read back.
 4. **Match quality** — the table. Ids pointing at two series in one instance (production:
    *Cunk on Britain*, whose ids name both it and *Cunk on Earth*) and an object claimed by two
    items (0 today) are refused and reported, never guessed.
 5. **The certification fallback** — **would fill 0 ratings today.** 1,361 of the 9,420 live
    items have no Jellyfin rating (the 1,497 above counted orphan index rows); 1,333 of them have
    an owner, and **none of those owners carries a certification** — the \*arrs take it from the
    same TMDB/TVDB data Jellyfin does. 40 single-object GETs agreed with the list response (0 of
    40 carry one). It still ships **off** (`arr_sync.certification_fallback`), because turning it
    on writes to Jellyfin (the rating field, and the rating tag/badge where `destinations.rating`
    sends them — production sends it to `poster` and `jellyfin`).
 6. **Default off** — `arr_sync.mode: dry_run`. The tag-config hash only changes when a switch is
    turned *on*, so a release forces no rescan (production's config hashes `20142cb0e93c4394`
    before and after this change). The Tag destinations grid marks the Sonarr/Radarr columns
    "dry run — not written" until writes are live.
 7. **No schema change.** The report lives in memory and in `arr-sync-report.json` beside
    `state.db`.

Two more defects in the old dead code, which would have fired had the key ever matched — both
measured on dev and fixed here: labels are **stored lowercase** and re-POSTing `xt-HEVC` when
`xt-hevc` exists is a **409**, so the old case-sensitive cache would have failed every scan after
the first; and Radarr's charset (B9) would have thrown on the first `H.264` film and abandoned
that item's whole write.

**The read-back** ships in the live path: managed tags exactly as intended, non-managed tags
unchanged, no other field changed (ignoring `statistics`, `lastInfoSync`, `lastSearchTime`,
`ratings`, `images`, `popularity`, which the \*arr rewrites itself). A mismatch or a write error
is logged at ERROR and **halts every further \*arr write in that scan**.

**Dev end to end** (Sonarr 4.0.18.2978 + Radarr 6.3.0.10514, production's versions; Jellyfin-dev
identified a copied fixture as *Firefly* / *Serenity* on its own): a dry-run scan changed nothing;
a live scan removed a stale `xt-hevc`, kept the user tag, created the missing labels, and an
independent before/after diff found **no field changed but `tags`**; a second live scan wrote 0
(current 1); and a real concurrent edit injected between write and read-back (`monitored`) was
caught — `READ-BACK MISMATCH … field changed: .monitored`, writes halted, the next item skipped.
The dev instances keep the seeded series/film.

### Going live — the operator's steps (done 2026-09-26: see "B5 — LIVE" above)

 1. Release and deploy as usual. Nothing changes: writes are off and no rescan is forced.
 2. Settings → **Sonarr / Radarr writes** → **Run dry run now**, and read the report — or
    `docker exec xenotag python -m app.arr_sync --dry-run --db /config/state.db` (read-only).
 3. Decide **B9** first if the codec tags matter on Radarr; going live without it just skips them.
 4. Set **Tag writes → Live** and save. That changes the tag-config hash, so the **next scan is a
    full re-tag** (every item re-probed and re-written on Jellyfin, as for any tag change) — the
    03:00 scan, or **Full scan** now. Expect ~35,500 tags on ~9,400 objects and 142 new labels.
 5. Read the report afterwards: `written`, `read-back / errors`, and any **HALTED** line. After a
    halt, fix the cause and run a **Full scan** — an incremental scan does not revisit the items.
 6. To back out: switching back to dry run **stops writes but removes nothing**. To strip what was
    written, untick Sonarr/Radarr in every Tag destinations row *while live*, run a full scan (the
    managed set becomes empty, so the managed tags are removed), then switch to dry run.

**Not done here:** the webhook path now resolves owners too, but it can only check the one item
it has, so the "claimed twice" refusal needs a scan; B8 means it would process the wrong item
anyway. Radarr's refused labels (B9) and the odd language labels (B7) are filed, not fixed.

*(original filing, 2026-09-24, follows)*

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

**B10 — FIXED 2026-09-25, filed and fixed the same day.** Reported by the operator: choosing
top-left for the tags laid them *on top of* the content rating (e.g. `PG-13`). Cause, verified in
code: `render_badge_groups()` drew the rating at a hardwired `"top-left"` with no offset, while the
tag rows went to `badge_position` and stacked only against each other. The README compounded it by
saying the rating was always **top-right**, so an operator avoiding the collision would have picked
the wrong corner to avoid. *(An earlier session called this "B6"; that ID was already taken by the
non-hex-colour defect, so this is B10.)*

The operator chose option 4 of four: **both positions independently configurable.** The rating gets
`image.rating_position` (default `top-left`, where it always was, so no existing poster moves), with
its own control beside the tag position. The operator's own list included drag-and-drop for the
same-corner case; a deterministic rule was used instead, because pixel offsets do not transfer between
posters of different sizes and a rule covers every combination:

 - **same corner** → stack, rating nearest the corner, tag rows continuing past it;
 - **same edge, opposite sides** → the one tag row in the rating's band is narrowed so it stops short
   of it. This case was not in the original recommendation and was found while implementing: a tag
   row may span the poster's **full** width, so rating top-left with tags top-right still collided;
 - **different edges** → independent, as before.

`tests/test_rating_position.py` (26 tests) checks all 16 corner combinations for overlap using pill
rectangles reported by the real render path, not a re-implementation. Mutation-checked: removing the
stacking fails 10, removing the narrowing fails 5, hardwiring the rating again fails 7. An unknown
`rating_position` in `config.yml` falls back to `top-left` with a warning rather than stopping the app.

Not covered: a tag stack tall enough to reach a rating on the *opposite* edge. That needs a very
short poster, and bounding pills to the poster generally is [P7]'s containment question.
*(Measured 2026-09-26 under P7: it happens only on a canvas wider than 2.25:1 at `tv_plus`
with `normalize_portrait` off. Closed by [P7] containment, 2026-10-06: the tag stack is clamped
to the canvas and stops short of the rating's row.)*

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
shipped palette: **0.89 for AAA, 0.73 for AA**; the old 0.65 default rendered 3.7:1. *(Palette 1, through the instrument; B4 moved them to
0.98 / 0.80, and [B21] found the poster did not match the instrument below 100% until 2026-10-06.)*

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

**B2 — FIXED 2026-09-25.** The Badge settings card now shows, beside each colour picker and
under the opacity slider, the label's contrast **as the badge renders** — the worst case over
black, white and grey posters, against the **configured** text colour — with WCAG AA (4.5:1) and
AAA (7:1) each marked met or not. Informational only, per the decision below: a failing colour
saves and renders as chosen, and a test saves one through the route.

### Re-measured 2026-09-25 — replaces the stale tables

Both runs through the committed probe (`python3 scripts/measure_badge_contrast.py [--config]`),
which as of this item prints from the same code the UI does. White text, `worst` = the figure the
UI shows:

| badge | shipped default | opaque hex | black | white | grey | **worst** |
|---|---|---:|---:|---:|---:|---:|
| video | `#203a30` | 12.3 | 12.3 | 12.3 | 12.3 | **12.29** |
| audio | `#312c4c` | 13.2 | 13.2 | 13.2 | 13.2 | **13.16** |
| sub | `#50532f` | 8.0 | 8.0 | 8.0 | 8.0 | **8.02** |
| rating | `#73485b` | 7.5 | 7.5 | 7.5 | 7.5 | **7.48** |

**The live config renders exactly this table.** A scratch copy of prod's `config.yml` (read
programmatically, deleted after) sets **none** of the four colours, `badge_opacity` or
`badge_text_color` — the reset of 2026-09-23 removed them — and prod runs v1.6.0, whose
`ImageConfig()` defaults were checked inside the container to be palette 2. So prod renders
**7.48:1 worst, all four AAA**, on any poster. (It carries no `badge_palette_version` yet — it has
not been saved since v1.6.0 — and has nothing for the migration to move.)

**What changed since the filing, and why the old numbers are gone:** the old table described
four custom colours at `badge_opacity: 0.65`; the operator reset both (2026-09-23), and B4/P10
then replaced the defaults (PR #65). Re-run as history, the old palette reproduces the filing to
the decimal — 2.69 / 3.32 / 2.58 / 4.48 worst at 0.65, 5.18 / 7.71 / 4.76 / 13.77 opaque — so
moving the probe's internals into `app/` changed no number.

**Opacity floors for the shipped palette**, at the slider's own 1 % steps: **98 % is the lowest
that holds AAA, 80 % the lowest that holds AA** (97 % → 6.90, 79 % → 4.39, 65 % → 3.19, the
worst badge always `rating` on white). These confirm the figures `config.py` and the README
already quote. *(True of the chip; the poster needed 99% / 87% until [B21] shipped, 2026-10-06,
since when both are true of the poster — re-measured by `scripts/measure_pill_composite.py`.)*

### The premise was half wrong: something DID check a colour — the wrong number, and it prevented

The entry said nothing checks a configured colour. The Badge settings card in fact carried a
client-side "contrast box" (`wcagContrast()` / `updateContrastIndicator()`), and it was wrong in
four ways:

 1. **It judged the opaque hex.** A JavaScript copy of the formula — a *third* copy, besides the
    two in `scripts/` — fed the picker values straight in. Opacity never entered it, and it was
    not even refreshed when the slider moved. B1's error, in the one place an operator looks.
 2. **It called 4.5:1 "AAA".** `required = fontSize >= 24 ? 4.5 : 7.0`, with font sizes 56/72/88,
    is always 4.5, displayed as "✓ WCAG AAA". That is the *large-text* AAA threshold; see below
    for why it is not claimed.
 3. **One number for four pickers**, beside the text colour, not beside the colour being chosen.
 4. **It disabled "Save badge settings" on a fail** (`saveBtn.disabled = !pass`) — *prevent*,
    the opposite of the decision below, and leaky with it: the Settings page's Save and the raw
    YAML editor were never gated.

So "nothing checks it" was wrong in letter and right in effect: nothing checked what renders.

### What shipped

 - **`app/contrast.py` is the one implementation** — WCAG luminance and ratio, the pill
   renderer-over-backdrop, the modal interior sampler, and `badge_contrast(cfg)`. The badge probe
   imports it (and re-exports the names the tests use); the separation probe's contrast column
   uses its formula; the JavaScript copy is deleted. Three copies → one. The separation probe
   keeps its own sRGB→Lab/CVD maths, which is a different computation, not a copy.
 - **`overlay._render_pill_tile()`** is the old body of `_pill_tile()`, uncached; `_pill_tile()`
   is now lookup-then-render with the **same signature and key** (B3's structural test untouched
   and green). The measurement calls the uncached one, so a colour the operator is only trying
   neither reads a tile rendered for something else nor leaves one in `_PILL_CACHE` (tested).
   **`badge_alpha()`** is now the single opacity→alpha mapping for both rendering and measuring.
   Nothing about rendering changed — no rendering test was touched.
 - **`GET /api/badge-contrast`, server-side, deliberately.** The figure must be the rendered
   ratio, and only the renderer knows that; a JavaScript model of the compositing would be a
   second implementation free to drift, which is precisely how B1 lived so long. Cost: ~20 ms a
   call (twelve tile renders), debounced 120 ms, with stale responses dropped by sequence number.
   It shares **`_image_config_from_params()`** with `/preview/image`, so the warning judges exactly
   the badge the preview draws — including the 0.1 opacity clamp and the palette-version pin
   (without which checking the old navy would have measured indigo — P10's trap, handled once).
 - **UI.** Beside each picker, a chip: worst ratio, which poster ("on a white poster", or "on any
   poster" when opaque), and `✓/✗ AA 4.5:1 · ✓/✗ AAA 7:1`. Under the opacity slider, the worst
   shown badge at that opacity, plus — below 100 % — a line saying the poster shows through. The
   old box beside the text colour is now the summary of the shown badges and says "Advisory only".
   A hidden badge is still measured (dimmed) but left out of the summary. **Save is never
   disabled.** Screenshotted in a throwaway container at 1366 px and 390 px, passing (defaults)
   and failing (`#a86200` at 65 %: sub **2.58:1** on white, every badge failing AA at that
   opacity, summary "2.58:1 ✗ AA 4.5:1 · ✗ AAA 7:1 — Subtitles, on a white poster"); the failing
   colour saved through the real UI and reloaded as saved.
 - **Normal-text thresholds, not large-text.** WCAG's large-text exemption (AA 3:1, AAA 4.5:1)
   needs ≥18pt. Badges are sized against a 1000 px reference poster; in a library grid a poster
   is ~150–300 px wide, where a 72 px label is ~11–22 px. No size can be promised, so none is
   claimed.
 - **The warning is readable in the P9 theme — after one correction.** Measured on the card
   (`--surface`): green 8.33, yellow 9.60, red 5.79, muted 7.58. **`--red` on `--surface2` is
   4.44:1** — and `--surface2` was the old box's background, so a failing verdict was itself below
   AA. The box moved to `--inset` (red 6.60). `theme.css`'s header says every pairing the UI uses
   was measured with a weakest of 5.07; red-on-Deep-Forest was not in that set. **Not audited:**
   whether anything else draws `--red` on `--surface2`.

### Is "worst of black, white and grey" the worst poster? For a verdict, yes

 - **For light text on a dark fill (the default), white is the worst of *every* flat poster; for
   dark text on a light fill, black is.** Tested: black text on `#e2ddcf` at 0.65 reports black.
 - **A mid-luminance text colour can do worse on some mid-tone poster than on any of the three.**
   `#808080` text on `#203a30` at 0.5: the three give **1.40**, a sweep of all 256 greys finds
   **1.00** at grey 203 — the poster that lands the fill exactly on the text's luminance.
 - **That can never flip a pass into a fail.** To pass AA against the black-, grey- *and*
   white-backed interiors, a text colour *inside* their luminance range would need a gap of
   ≥ 4.5² = 20.25× in (L + 0.05) between two adjacent interiors; over 20,000 random fill/alpha
   pairs (arithmetic compositing, not rendered) the widest gaps are **5.36×** (black→grey) and
   **3.95×** (grey→white). So a passing figure has the text outside the range, where the extremes
   are the black and white posters — both measured. Spot-checked by render: **42 passing cases,
   0 where a grey poster did worse.** Conclusion: a PASS is exact over every flat poster; a FAIL
   may be optimistic about *how badly* it fails.
 - **Not determined:** a textured poster under the pill. The sampler takes the modal pixel, which
   is right for flat backdrops; a real poster's variance under a translucent fill is not modelled.

### Guarded by

`tests/test_badge_contrast_warning.py` (15). Mutation-checked: judging the opaque hex fails 4
(including `test_a_translucent_colour_is_judged_on_what_renders_not_on_its_hex`), hard-coding
white text fails 2, re-disabling Save in the template fails 1. The UI↔probe agreement test runs
the same config through the endpoint and through the probe's printed table and compares the
digits, at 0.65, 1.0 and a non-white text colour. Suite 116 → 131.

### Not done, deliberately

 - **Palette separation is not in the warning.** B4's CIEDE2000 check is a between-badge
   property, not a within-badge one. *Follow-up worth considering (NEEDS DECISION):* the same
   chips could flag a chosen palette whose worst pair falls below dE 5 under simulated CVD —
   `measure_palette_separation.worst_separation()` already computes it. Not built here.
 - **Only the Badge settings controls warn.** The raw YAML editor and a hand-edited `config.yml`
   get no figure; `--config FILE` on the probe covers them. Nothing warns at scan time either —
   by the decision, a log line is not a warning.
 - **B6 filed** (below): a non-`#rrggbb` colour renders black. The warning reports that
   truthfully (it measures what renders), but only the hand-edit path can produce one.
 - **Seven CodeQL alerts are open on `main` and nothing in this file tracks them** (checked
   2026-09-25): #3–5 `py/path-injection` on `preview_image`'s `sample` read, #6–7
   `py/weak-sensitive-data-hashing` and #8 `py/clear-text-logging-sensitive-data` in `auth.py`,
   #1 `py/cookie-injection`. This PR's first push failed the CodeQL check with #3–5 — not new,
   same alert numbers as `main` — because the new helper sat *above* `preview_image` and git's
   diff re-attributed that route's signature (the `sample` source) as changed code. Moving the
   helper below the route cleared it; no alert was dismissed. Whether #3–5 are real is **not
   determined** (`Path(sample).name` looks like a sanitiser CodeQL does not recognise); they
   deserve an item of their own, with evidence, rather than a guess here.

*(decision record follows)*

**DECIDED 2026-09-23: WARN, do not prevent.** The operator: *"B2 should warn, not
prevent."*

So a configured colour that fails contrast is **rendered as asked** and reported — never
refused. The reasoning that follows from it: a refusal would make xenotag override a deliberate
aesthetic choice, and a badge that silently does not appear is a worse failure than a badge that
is hard to read. The operator owns the trade; the tool's job is to make sure they are making it
knowingly.

**What "warn" should mean, and it is not a log line nobody reads:** surface the measured ratio
**in the Settings UI, beside the colour picker, at the moment of choosing** — with the AA/AAA
thresholds named. A warning emitted at scan time is a warning delivered to nobody.

*(original filing, 2026-09-22, follows)*

The four colours in `ImageConfig` are only defaults. The settings UI and `config.yml` accept
any hex and **nothing checks it**. The live deployment at `~/docker/xenotag/config/config.yml`
has replaced all four, and it keeps `badge_opacity: 0.65`, so B1's new default does not reach
it. Measured with the same probe (`--config FILE`), before and after B1:

*(The configured-palette table that stood here is superseded: that config no longer exists.
It is re-run under "Re-measured 2026-09-25" above, as history, and reproduces to the decimal.)*

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

**B3 — filed 2026-09-22 while fixing B1; FIXED 2026-09-24.**

`_PILL_CACHE` was keyed `(text, fill_hex, text_hex, alpha, font_size)`, but `_pill_tile()` also
takes `pad_h` and `pad_v`, and those change the tile. `_compute_layout_params()` derives all
three from the poster width by separate roundings, so they can disagree: sweeping widths
200–4000px at the default `badge_size` found **121 collisions**, the first being **494px and
501px — both `font_size` 36, `pad_v` 2 vs 3**. Same key, different correct tile; whichever
poster was processed first supplied the tile for every later one in that process.

Consequence was small but real: the row layout in `_render_group()` computes `pill_h` from *its*
`pad_v`, so a mis-served tile is a ~2px vertical mismatch between where the row expects the pill
and how tall the pill actually is. Nothing was unreadable; it was wrong, and the fix is the
one-line widening of the key to all seven arguments.

New probe: **`scripts/measure_pill_cache_key.py`** (`--min-width`, `--max-width`,
`--badge-size`, `--text`, `--compare`, `--self-test`). It does not re-state the key tuple and check it by
eye — restating the key is how the bug got written. It **renders**: for every width it asks the
live cache for a tile, renders the same arguments again against a scratch cache for ground
truth, and compares the bytes. It **exits non-zero** on any collision.

Regenerate the table below with `--compare`, which re-runs the sweep against the pre-fix
5-term key as well, so the "before" column stays reproducible without checking out the defect.
3801 renders requested per row, widths 200–4000px:

| badge_size | collisions | cache entries | hit rate |
|---|---|---:|---:|
| desktop | 149 → **0** | 214 → 232 | 94.37% → 93.90% |
| **tv** (default) | **121 → 0** | 275 → 293 | 92.77% → 92.29% |
| tv_plus | 105 → **0** | 335 → 353 | 91.19% → 90.71% |

**What the wider key costs — the trap in this item.** Cache entries grow by **exactly 18** at
every badge size, and the hit rate drops **0.5pp** (0.47–0.48 in all three). Those 18 entries
are *exactly* the 18 tiles the narrow key was serving wrong: 18 `font_size` classes in this
range contain two `pad_v` values — never more — and the widened key splits each of them in two.
The fix buys correctness with nothing it was not already getting by cheating. (The *collision*
counts differ across badge sizes only because a smaller `badge_size` makes each `font_size`
class span more widths, so the same 18 split classes catch more posters.) The sweep is also the worst
case by construction — every width distinct, one render each. A real library's posters come in
a handful of sizes, and the padding is a deterministic function of the width, so at any fixed
width the wider key costs nothing at all.

**Is anything else missing from the key?** No. `_pill_tile()` now keys on all seven of its
arguments, asserted structurally by `test_cache_key_is_every_pill_tile_argument` so a new
argument cannot be added without landing in the key or failing. Everything else the body reads
is a module constant fixed for the life of a process — `_GLOW_MARGIN`, `_GLOW_EXPAND`,
`_GLOW_BLUR`, the corner radii, the glow's own `(255,255,255,210)`. `_FONT_PATHS` and
`_font_cache` are module state the key does not cover, but they are set at import and never
written in production; the settings routes already call `clear_pill_cache()` on every config
save, which is what covers a `badge_size` or palette change mid-process.

**`pad_h` is in the key, and no poster width can prove it belongs there.** The sweep reports
`pad_h` as never varying within a `font_size` class — at *any* badge size, over 1–8000px. The
reason is arithmetic: every `_BADGE_SIZE_PX` value is an exact multiple of 8 (56 = 7×8,
72 = 9×8, 88 = 11×8), so every `round(8 * scale)` rounding step lands on a
`round(base * scale)` step too. `pad_v`'s base is 5, which divides none of them, which is why
it is the one that collides. This is luck in the layout constants, not a property of the cache;
a badge size that is not a multiple of 8 would spend it. So `pad_h` stays in the key, its test
case is planted by hand rather than derived from a width, and the probe's self-test carries a
hand-planted `pad_h` control precisely because a width sweep cannot supply one.

Re-run: `python3 scripts/measure_pill_cache_key.py [--compare] [--self-test]`. The test suite
carries the probe's self-test, the 480–520px band that used to fail, the entry-count arithmetic,
and — the strongest of them — `test_a_poster_renders_the_same_whatever_went_through_the_cache_first`,
which renders a whole 494px and 501px poster through `render_badge_groups()` in both orders and
compares the bytes. That one guards the defect *class* rather than B3's instance of it: P6 would
fail it too, which is the cue to add a palette term to the key. Reverting the key alone fails 12
tests.

**B4 — FIXED 2026-09-24, together with [P10].** The operator chose **palette B** and **defaults
plus a migration**. Worst pair across normal, protan, deutan and tritan vision went from **dE 1.9
to dE 12.3**, and CI now runs the separation probe bare as the acceptance criterion — the same
shape as B3's sweep — so a future default that collapses fails the build. The details of what
shipped are under P10.

*(original filing follows)*

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

## Formerly "In Progress"

*Sweep 2026-09-26: nothing here was in progress.* No branch, local or remote, carries P1 or P2
work; P3 shipped on 2026-05-05. The heading is kept so older references resolve.

| ID | Feature | Readiness | Issue |
|----|---------|-----------|-------|
| P1 | Audio language override (fix UND tracks via ffmpeg metadata) | **CLOSED 2026-09-26** — moved to [Deferred / Out of Scope](#deferred--out-of-scope) | [#21](https://github.com/bpoulliot/xenotag/issues/21) |
| P2 | Media browser: name column, codec columns | **CLOSED 2026-09-26** (shipped; Rating column kept) | [#10](https://github.com/bpoulliot/xenotag/issues/10) |
| P3 | Scan history in web UI | **SHIPPED 2026-05-05** (`fc7563d`) | [#9](https://github.com/bpoulliot/xenotag/issues/9) (closed) |

**P1 — NEEDS DECISION (sweep 2026-09-26) → CLOSED by the operator 2026-09-26 (decision at the end of this note), and the size of the problem changes the question.**
Nothing is built: no route, no UI action, no branch. Measured on a read-only copy of production
`state.db` (2026-09-26): **3,411 of 10,583 index rows carry at least one `UND` audio track, and
3,406 of those have *only* `UND` tracks** — 11,866 audio tracks in all, 3,411 undetermined. By
container: **mp4 3,071**, mkv 283, avi 45, other 12. (Rows include the ~1,160 orphans U2
describes, so the live figure is somewhat lower; the shape is not.) Issue #21 specifies a
per-item form that remuxes the file with `ffmpeg -c copy -metadata:s:a:N language=…` and
atomically replaces it. At ~3,000 items a one-at-a-time form is not a fix, and the remux is a
write to the media file itself — the project's asymmetry, with the \*arrs' `recycleBin` empty.
**Question:** should xenotag ever rewrite media files? Options:

 1. **As specified** — per-item remux, replacing the file. Works for every container; every use
    rewrites a whole file in the library.
 2. **Header-only edit where possible** — `mkvpropedit` sets an MKV track's language in place,
    without a remux (needs `mkvtoolnix` in the image); MKV only, so it reaches 283 of 3,411.
 3. **A per-item override stored by xenotag** — the file is untouched and the *tags* say `EN`;
    needs a new table, so an Alembic revision (I3, SHIPPED 2026-09-26), and it is U9's option 2 in another form.
 4. **Out of scope** — xenotag reports `UND` (it already does, in yellow, in the media browser)
    and the operator fixes files with their own tools, then rescans.

**Recommendation: 4 now, 3 later if wanted.** A tagger that rewrites 3,000 media files is a
different product with a different risk profile, and the tagging side of the question is
already answered by 3. If the operator wants files fixed, the bulk case belongs in the encode
pipeline (`nav1s.sh` already writes per-stream `language=` when it muxes), not in a web form.

**OPERATOR DECISION 2026-09-26:** option 4 — out of scope: moved to **Deferred / Out of Scope** (3,411 `UND` rows;
rewriting media files is a different product). **CLOSED.**

**P2 — NEEDS DECISION (sweep 2026-09-26) → CLOSED (shipped) by the operator 2026-09-26: two of its three asks already shipped.** The media
browser's `Item` column shows the item's folder name (falling back to the file name, then the
id), and its `Video` and `Audio` columns show resolution, codec and HDR, and each track's
language and codec — checked in `index.html`. **What is left is issue #10's third ask, "remove
the rating column"**, filed when "rating" was read as a review score. U7 (2026-09-23) settled
that rating here means the certification (`R`, `TV-MA`) — the same value the rating badge draws.
**Question:** keep the `Rating` column? Options: (a) keep it and close P2 as shipped;
(b) remove it as #10 asked. **Recommendation: (a)** — the column is the certification, which the
operator's own U7 framing treats as meaningful, and it is the only place the browser shows it.

**OPERATOR DECISION 2026-09-26:** (a) — keep the Rating column; the other two asks shipped, so P2 is **CLOSED (shipped)**.

---

## Near-term

### U — User-facing

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| U1 | Tag migration: clean up legacy `mf-*` tags on upgrade from Metafin; `tags.legacy_prefixes` config option | 5 | 2 | **FIXED 2026-09-24** | [#35](https://github.com/bpoulliot/xenotag/issues/35) |
| U2 | Tag lifecycle: remove stale `xt-*` tags when items are deleted from Jellyfin; handle mtime-preserving re-encodes | 5 | 3 | Deleted items: **removal LIVE 2026-10-10** — production runs `deleted_items: {mode: remove, max_fraction: 0.15}`; the first pass (scan 164) deleted **1,198** index rows (10,661 → 9,463) and stripped **0** \*arr tags, every removed id checked gone (see *U2, deleted items* below). It was report-only from v1.9.0 (2026-09-27) and blocked on B26 until v1.11.1 · re-encodes: NEEDS MEASUREMENT | [#36](https://github.com/bpoulliot/xenotag/issues/36) |
| U3 | Webhook / event-driven processing: per-item rescan on Sonarr/Radarr/Jellyfin Download events | 5 | 2 | **SHIPPED 2026-05-05** (`53c9f3f`) — item resolution fixed by [B8] (2026-10-06, released in v1.11.0) | [#22](https://github.com/bpoulliot/xenotag/issues/22) |
| U4 | Subtitle language tagging: write `xt-sub-*` tags to Jellyfin/Sonarr/Radarr (ffprobe extraction already exists) | 4 | 2 | **SHIPPED** (in v1.0.0) | [#11](https://github.com/bpoulliot/xenotag/issues/11) (closed) |
| U7 | ~~**Ratings ingest**~~ — **CLOSED 2026-09-23, premise was wrong**: xenotag already emits certification ratings from `OfficialRating` | 4 | 2 | **CLOSED** | — |
| U8 | **Tag taxonomy pass** — audit the `xt-*` set actually emitted and collapse what is redundant or never queried. | 4 | 3 | **CLOSED 2026-09-23** — measured, then the operator ruled out cutting tags; what remains is [P7] | — |
| U9 | ~~Tag queries~~ **RESCOPED: a manual correction to an `xt-*` tag is silently clobbered on the next scan** | 4 | 3 | Override half **CLOSED** · drift-detection half **SHIPPED 2026-09-27** · **RELEASED v1.11.0** (2026-10-07) | — |
| U11 | **Nothing sends xenotag a webhook.** 0 notifications on all five production \*arrs and no Jellyfin webhook plugin, so U3's event path (and B8's fix) never runs; wiring one adds an event-driven writer to live \*arrs | 2 | 2 | **CLOSED (decided 2026-10-05)** — unwired | — |

**U11 — FILED 2026-09-27, found while measuring B8. Not built here.**

Measured, read-only: `GET /api/v3/notification` is an empty list on sonarr-general, sonarr-4k,
sonarr-anime, radarr and radarr-4k, and production Jellyfin's 16 plugins include no webhook
sender, so no event has ever reached `POST /webhook/{source}` (0 logged). **The question:** should
the \*arrs call xenotag on `Download`/`Rename` at all, given that the nightly scan already reaches
every item within 24 h? What wiring one involves, measured:

 - **An open endpoint.** Production's `webhooks.secret` is empty, so the route takes any POST.
   Set it first (`XENOTAG_WEBHOOK_SECRET` or its `_FILE` twin, I8) and put `?token=` in each
   \*arr's webhook URL.
 - **An address.** All five \*arrs run in the VPN container's network namespace: `http://xenotag:7755`
   does not resolve there (curl exit 6), and xenotag's `docker_vpn` IP (172.23.0.4 today) answers
   `GET /health` 200. That IP is assigned by Docker and can change, so it would need pinning.
 - **A second writer to live \*arrs.** With B5 live, every webhook runs the \*arr tag sync for its
   item, so a webhook is a write path outside the scan's schedule.
 - **Timing.** A webhook is handled immediately, but Jellyfin sees an import only through its
   real-time monitor (60 s `LibraryMonitorDelay`), so an immediate run mostly finds nothing yet
   (B8's "not measured" note). A useful build would retry after the monitor's delay. The lag on
   production's media mounts should be measured before choosing that delay.

**Options:** (1) leave it unwired. The nightly scan is the mechanism, B8 still gets fixed so the
dormant path is correct, and U3's GitHub #22 closes as built but unused. (2) Wire all five after
B8 ships: secret, pinned address, `Download`+`Rename`, plus a resolve retry.
**Recommendation: (1)** unless a day's latency on new imports has actually bothered someone. It
is the operator's call because it decides whether xenotag becomes event-driven at all.

**OPERATOR DECISION 2026-10-05:** (1) — leave webhooks unwired. B8 still gets fixed (the folder
resolve) so the path is right if one is ever wired. Relabelled **CLOSED**.

**Sweep 2026-09-26 — U2, U3, U4.**

 - **U3 — SHIPPED 2026-05-05** (`53c9f3f`, "HTTP connection pooling and webhook event-driven
   processing"): `POST /webhook/{source}` with the shared-secret check, Sonarr/Radarr `Download`
   and `Rename`, Jellyfin `ItemAdded`, and `handle_webhook()` running the single-item pipeline.
   It does not *work*, and that is **B8**, which owns the defect now — don't reopen U3 for it.
   GitHub #22 is still open.
 - **U4 — SHIPPED** before this roadmap existed: `tagger._subtitle_tags()` emits `xt-sub-<LANG>`
   (as `mf-sub-*` in v1.0.0), and `tags.destinations.subtitles` sends them wherever the operator
   ticks — the default is `[poster, jellyfin]`, and Sonarr/Radarr are one tick away in the Tag
   destinations grid. GitHub #11 was closed 2026-05-05. U8's worry that U4 "widens the tail" is
   answered by the P7+U8 redirect: the tail is not cut.
 - **U2 — NEEDS DECISION → decided 2026-09-26 (deleted-items half READY, below).** Two halves, and the premise moved under both of them:
   - *Deleted items.* When #36 was written, xenotag had never written to an \*arr (B5), so the
     "stale tags on the \*arr" half was hypothetical; since 2026-09-26 it is real. What is
     measured: **1,162 of 10,575 index rows (11%) describe a Jellyfin item that no longer
     exists** (U1, 2026-09-24), and B11 found 9 series whose folders hold no file. **Question:**
     when a Jellyfin item is gone, what does a scan do? Options: **(a)** delete its `state.db`
     row only (local, reversible from a backup, and it removes the stale input B11's dry run
     reads); **(b)** (a) plus strip the managed tags from the \*arr object it owned, if that
     object still exists; **(c)** (b) plus a report-only first release that lists what it
     *would* remove. **Recommendation: (c)**, i.e. (b) behind one dry-run release — the \*arr
     strip is the first automatic *removal* xenotag would make on an \*arr, and it gets the same
     staged rollout B5's writes got. (b)'s ownership rule is B5's: an object is xenotag's to
     strip only if its folder matched the deleted item's.

     **OPERATOR DECISION 2026-09-26:** (c) — when a Jellyfin item is gone, delete its index row and strip
     the owned \*arr tags (B5's folder-ownership rule), behind one report-only release first. The
     deleted-items half is **READY**; the mtime-preserving re-encode half below stays NEEDS MEASUREMENT.
   - *mtime-preserving re-encodes.* NEEDS MEASUREMENT before anything is chosen: does any tool
     here replace a file's content and keep its mtime? Tdarr and `nav1s.sh` write new files (the
     latter a new *name*, so a new item), and \*arr renames preserve content. Measure by
     comparing, on a copy of `state.db`, each row's `file_mtime` and stored codec against a
     fresh `stat` + ffprobe of a random 200 files (~1 h, read-only). If the count is zero, this
     half closes; if not, the size check #36 proposes needs a `file_size` column — an Alembic
     revision (I3, SHIPPED 2026-09-26).

**U2, deleted items — BUILT 2026-09-27; LIVE report-only in v1.9.0 (deployed 2026-09-27 11:40Z). Removal LIVE in production since 2026-10-10 (v1.11.1; the operator's switch, made by an overnight item — see the end of this section).**

The operator's option (c): when a Jellyfin item is gone, a scan deletes its index row and strips
the managed tags from the \*arr object it owned — behind one report-only release first. The
re-encode half below is untouched and still NEEDS MEASUREMENT.

**What shipped** — `app/deleted_items.py`, run at the end of every scan (not a cancelled one),
from Settings → **Deleted items** → **Run report now**, and as
`python -m app.deleted_items --report --db <copy>`; the last two are report-only whatever the
config says.

 - **"Gone" needs two independent reads, and any doubt aborts the pass with nothing changed.**
   A complete listing of every Movie/Series on the server — not only the configured libraries,
   because an item outside them still exists: every page must carry the same `TotalRecordCount`,
   no id may come twice, a page may not come back empty before the total, and a `Limit=0`
   recount after the last page must agree. Then every row missing from it is looked up with
   `/Items?Ids=` in batches of 100, each beside 5 live control ids from the listing; one control
   not answering aborts the pass (a lookup that answers nothing would "confirm" everything), and
   a candidate that answers is not gone.
 - **Deleted is not unreachable (B11).** An item Jellyfin still lists — file gone, probe failed —
   is in the listing, so it is never a candidate, whatever `scan_errors` says (tested both ways).
 - **Ownership is B5's folder rule.** The deleted item's folder is the one the scan recorded: the
   folder of the poster it overlaid (every row with a poster has it in the item's own folder —
   10,535 of 10,585 in production), else the file's folder, then its parent; the object at the
   deepest such folder across every instance is the owner, and two objects claiming one folder in
   one instance are refused. **An object whose folder still holds any live Jellyfin item is never
   stripped** — that item owns it now (a superset of B5's id-and-folder ownership), which is the
   re-encode-in-place case U1 found most orphans to be.
 - **The bound: `deleted_items.max_fraction`, default 0.15.** Removal refuses when more than 15%
   of the index would go; the report still lists all of it. Chosen because the first production
   pass faces a backlog nobody ever cleared — 11.2% (below) — while the flow afterwards is small
   (orphans by `last_scanned` month: 29 May, 947 June, 90 July, 94 August, 27 September), and
   losing a whole media mount exceeds it (the largest library is 1,383 of 9,399 items, on top of
   the backlog). It cannot catch one small library vanishing — the listing and the lookup are for
   that. A deliberate mass deletion needs it raised for one scan.
 - **Report mode cannot change anything, structurally.** The index is opened `mode=ro`, every
   \*arr client sits behind `ReadOnlyTransport`, and Jellyfin is read-only in both modes (U2 never
   writes it). Tests prove a strip attempted in report mode raises before the PUT is sent, and a
   row delete through the report session is refused by SQLite.
 - **Remove mode** strips tags only while `arr_sync.mode` is `live` too (otherwise a row that
   still needs a strip is kept). Each strip re-fetches the object, re-checks its folder, removes
   managed/legacy tags only through the bulk editor, and is read back like a B5 write; a mismatch
   or an error halts the pass and **no row is deleted after a halt**. A row goes only after every
   object it owned is stripped. Each strip is appended to `deleted-items-removed.jsonl` beside
   `state.db` (instance, object id, tag ids, labels), so it can be undone by hand.
 - **No schema change and no re-tag:** the report is `deleted-items-report.json` beside `state.db`
   (like B5's), and the mode is not in `_tag_config_hash()`.

**Measured before the release** (2026-09-27 11:20Z, read-only: a throwaway container from the
v1.8.0 image with this branch's `app/`, production's `config.yml` read-only, a copy of `state.db`
with its `-wal`/`-shm`; 42 GETs, 0 blocked):

| step | production |
|---|---|
| listing | 9,399 items = `TotalRecordCount` 9,399 = recount; 19 pages |
| rows missing from it | 1,187 of 10,585; 12 lookups by id: **1,187 confirmed gone**, 0 answered, 60/60 controls answered |
| would delete | 1,187 rows (11.2%, under the 15% bound) |
| folder holds a live item → row only | 314 rows (297 radarr/general objects kept) |
| no \*arr object in the folder → row only | 873 rows |
| \*arr tags to strip | **0**, on all five instances |

Why 0: B5 went live on 2026-09-26 and writes only to objects a live item owns, and nothing it has
tagged has left Jellyfin since. **Cross-checked from the other end**, independently of the pass: a
GET snapshot of all five \*arrs (11:26Z) holds 9,350 objects with a managed label, and all 9,350
sit in a folder holding a live Jellyfin item — 0 do not. So enabling removal today would delete
1,187 index rows and strip nothing; the strip half starts to matter as tagged items are deleted.

**In production — the report of record** (v1.9.0, released and deployed 2026-09-27; production's
`config.yml` has no `deleted_items` key, so it runs the shipped `report`). The first scan, scan 148 (a
full re-tag forced by B7, 11:41:30Z → 12:52:11Z), ran the pass at its end: **the same numbers** —
9,399 listed = `TotalRecordCount` = recount; 1,187 of 10,585 rows confirmed gone by id (60/60
controls); 314 rows kept for a live item (297 radarr/general objects), 873 with no object; **0 tags to
strip** on all five instances; 42 GETs, 0 blocked, no \*arr write. None of the 873 has an object at
any ancestor of its file (checked at every depth, so the folder rule misses nothing there); 716 of
them name a folder that exists under another root — the title moved, and its object follows its
live item. The report with titles, the snapshots and the re-tag read-back are on the host in
`~/docker/xenotag/u2-release-20260927/` (not in this repo).

**Dev end to end** (Jellyfin 12.1.0, Sonarr 4.0.18.2978, Radarr 6.3.0.10514; scratch config and
index): Serenity's folder moved out of the library and Jellyfin refreshed (item gone, the Radarr
movie kept, as in production). **report** — 1 row, 4 `xt-` tags on 1 movie, 7 GETs, and an
independent before/after snapshot equal on every field; **remove at 15%** — REFUSED (1 of 6 rows is
16.7%), nothing changed; **remove at 50%** — the 4 tags stripped and read back, `user-keep` kept, no
other field or label definition changed, the row deleted, the removal log written. Folder restored:
Jellyfin re-added it under a new id, the next scan re-tagged the movie (B5 `written 1`) and the
pass found nothing.

**Tests:** `tests/test_deleted_items.py`. Seven mutants — no live-folder keep, no lookup by id, no
recount, a writable report session, \*arr clients always writable, no bound, deleting after a halt
— each turn at least one test red.

**To enable (the operator):** read the production report (Settings → Deleted items, or
`deleted-items-report.json` beside `state.db`), then set `deleted_items: {mode: remove}` in the YAML
editor or `config.yml`. The next scan acts; no re-tag is forced. Back out: `mode: report`.

**OPERATOR NOTE 2026-10-05:** the operator will flip `deleted_items: {mode: remove}` once the
report above is re-run — the report of record is scan 148 (2026-09-27), now roughly 8 days old.
Readiness unchanged; this just records the re-run that is owed first.

**2026-10-06 the operator authorised the switch through an overnight item; on 2026-10-07 that item
STOPPED at its own gate, and nothing changed.** It ran after the v1.11.0 release and read scan
158's report (`generated_at` 05:07:23Z, `mode: report`, `status: ok`, not halted): 10,645 index
rows, **1,197 confirmed deleted (11.24 %, under the 0.15 bound)**, but **87 rows marked STRIP —
82 radarr/general objects, 330 tags** — which are B26's live films. The gate was "strip count
non-zero → do not switch". Production `config.yml` is untouched (`mode: report`), no backup was
taken and no scan was run. The authorisation stands: the switch is owed again once the release that
carries B26 is live and a full scan's report shows 0 rows to strip.

**2026-10-10: both conditions are met, and the switch has NOT been made.** B26 is live in v1.11.1.
Its full scan (scan 163) wrote a report-mode deleted-items report at 17:07:30Z: `status: ok`, not
halted, listing 9,467 (= total = recount), **1,198 confirmed deleted, 0 answered by id**, fraction
**0.1124** (bound 0.15), and **strip_objects 0 / strip_tags 0 on every instance**. Production still
runs `deleted_items: {mode: report}`. The switch is the operator-authorised overnight item that
re-checks this report (queued as item 55).

**2026-10-10: removal is LIVE — production runs `deleted_items: {mode: remove, max_fraction: 0.15}`.**
The overnight item made the switch only after three gates held, and nothing needed rolling back.

- *Gates.* Two reports were checked by `gatecheck.py`, whose self-test breaks each check once and
  which FAILs the pre-B26 nightly report as a control:
  - scan 163's report (17:07:30Z);
  - a fresh report-only pass started from the Settings route (17:25:38Z).

  Both gave the same numbers:
  - listing 9,467 = `TotalRecordCount` = recount;
  - **1,198 confirmed deleted, 0 answered by id**, 60/60 controls;
  - fraction **0.1124** against the 0.15 bound;
  - **strip 0 objects / 0 tags on all five instances**.

  An independent GET-only check named the same 1,198 rows, id for id: its own listing and its own
  `Ids=` lookups, self-tested both ways (200 live ids must answer, 200 made-up ids must not).
- *Backup and switch.*
  - Backup taken after a clean stop (no WAL): `state.db.bak-20261010-pre-u2-remove` and
    `deleted-items-report.json.bak-20261010-pre-u2-remove`.
  - `config.yml` changed by exactly one line: `mode: report` → `mode: remove`. `max_fraction` stays
    0.15; every other key was compared equal.
  - Restarted 17:29:31Z, healthy, 0 restarts.
- *The first removal pass.* It ran at the end of one incremental scan, scan 164 (17:30:41–17:31:22Z,
  9,467 listed, 3 tagged). Its pass (`mode: remove`, `arr_writes: true`, 17:31:22–17:31:28Z):
  - **`rows_deleted` 1,198** = `confirmed_deleted`, 0 rows kept;
  - 322 rows had a live item in the folder (305 radarr/general objects left alone), and 876 had no
    \*arr object;
  - **0 objects / 0 tags stripped** on all five instances;
  - 0 write errors, 0 read-back failures, not halted, not refused.
- *Verified.*
  - The index went from 10,661 to **9,463** rows (−1,198), and the scan added 0. The removed set
    equals the independent prediction exactly.
  - All 1,198 removed ids: 0 in a fresh complete listing, 0 answer `/Items?Ids=`. Three single-id
    spot checks return `TotalRecordCount 0`.
  - GET snapshots of all five \*arrs before and after show **one** change: one radarr/general
    object `xt-h264` → `xt-av1`. That is the scan's own tag sync (its report: `written 1`), not
    the pass.
- *The removal log records strips only.* `deleted-items-removed.jsonl` was not created, because
  nothing was stripped. A deleted index row can only be recovered from a `state.db` backup; the
  pre-switch backup holds all 1,198.
- *From now on* every scan that is not cancelled, the nightly incremental included, deletes the rows
  of items Jellyfin no longer has. It strips managed tags from an \*arr object only when no live item
  is in that object's folder. Back out: `mode: report`.
- Record (titles, so not in this repo): `~/docker/xenotag/u2-remove-20261010/README.md`, with the
  gate, prediction and diff tools.

**Not covered:** an \*arr object carrying managed tags whose folder holds no live item and that no
index row points at (a row removed by hand) is never found — 0 exist today (the cross-check above).

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

*(Superseded 2026-09-23 by the P7+U8 redirect below: the operator ruled that no tag is cut for
being rare, so U8 is CLOSED and the per-destination idea that follows was not taken up. Kept
for provenance.)* ~~**What remains for U8 to decide (why it is READY, not DONE):**~~ whether a tag on <1% of items is
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

*Sweep 2026-09-26 — **NEEDS DECISION**, and the measurement this note asked for now exists.* The
B5 go-live compared, for all **9,356** items it planned, Jellyfin's current `xt-` tags with
`state.db`'s `tags_applied`: **9,346 match exactly**, and the 10 that do not have *lost* their
tags (B11, B12) rather than carrying a corrected one. Because scans are mtime-driven, a hand edit
would survive until its file changed, so it would have shown up there. **No one has corrected an
`xt-` tag by hand.** Meanwhile every *wrong* tag found this month was a derivation bug, fixed or
filed upstream: B7 (language codes), B13 (cropped 2160p called 1080p). **Question:** which does
U9 become? Options: **(1)** close it — wrong tags are derivation bugs, and the README says in one
line that `xt-` tags are owned by xenotag and hand edits are replaced; **(2)** an override store
(needs a table — an Alembic revision, I3 SHIPPED 2026-09-26; overlaps P1's option 3); **(3)** (1) plus **drift detection**: before
writing, compare the item's current `xt-` tags with `tags_applied` and log a WARNING when they
differ — no schema, and the same check names B12's five films the next time a scan reaches
them (only then: it runs where the write runs, so an unchanged file is not re-checked).
**Recommendation: (3)** — it removes the "silently" from the defect title without building an
override nobody has needed, and it is a cheap first detector for B12's class of loss.

**OPERATOR DECISION 2026-09-26:** option (3) — the override store is closed, the README says `xt-` tags are owned by
xenotag, and drift detection logs a WARNING when an item's current `xt-` tags differ from
`tags_applied` before a write. So U9's **override half is CLOSED** and its **drift-detection half is
READY**, specced by option (3) above; no build has been queued for it.

**U9 drift-detection half — SHIPPED 2026-09-27; released in v1.11.0 (2026-10-07).** `_process_one_item()`
calls `_warn_tag_drift()` just before `set_managed_tags()`, on both the scan and the webhook path.
It compares the item's current `xt-` tags with the row's `tags_applied`, both restricted to
`tags.managed_prefix`. When they differ it logs one WARNING line naming the item (name and id),
the `xt-` tags Jellyfin has that xenotag did not write, and the ones it lacks. It also increments
`xenotag_tag_drift_total`, a counter in I5's registry that accumulates for the process's lifetime
and resets on restart. The write goes ahead unchanged, and any error in the check is logged at
DEBUG and ignored. Non-`xt-` tags (including legacy `mf-` and the \*arrs' non-prefixed labels)
are never drift. No row, or a row whose `tags_applied` is NULL, counts as a first write.
- **No extra request.** The tags come from `jf.get_tags(item)`, the same copy
  `set_managed_tags()` builds its write from (in a scan, since B18, the batched `Ids=` read). The scan's item comes from the recursive listing
  and the Jellyfin webhook's from `/Items?Ids=`, and both request `Tags` in `Fields`. The
  \*arr webhooks use `find_item_by_provider_id()`, which requests `ITEM_FIELDS` and so gets
  `Tags` too.
- **Caveat: B18 — FIXED 2026-10-06 (#114).** On production the recursive listing can serve stale
  `Tags`. Before B18's fix, a scan-path warning could therefore describe the listing rather than
  Jellyfin. The scan now reads each batch's tags by `Ids=` right before the write, and the
  warning and the write share that read.
- **Vocabulary changes do not flood the log.** The comparison is Jellyfin against the *row*,
  never against the tags about to be written. An item nobody touched still carries exactly the
  row's old spelling, so the full re-tag that a `tag_config_hash` change forces warns only where
  Jellyfin really differs. B17's 22 films are one example: their rows say `xt-H264` and
  Jellyfin still says `xt-H.264`.
- **Expected real warnings.** Items carrying the \*arrs' lowercase NFO spellings (`xt-aac`
  beside `xt-AAC`; 28 were counted before scan 148) are reported as drift, because they are
  `xt-` tags xenotag did not write, and the write replaces them.
- Tests: `tests/test_tag_drift.py` covers drift detected, all tags lost (B12), no drift,
  non-`xt-` differences ignored, first write, a post-vocabulary-change re-tag, identical writes
  and row with and without drift, and a failing check not stopping the write.

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
benefit; the mtime problem is the residue for files that change without an event. *(Sweep
2026-09-26: U3 had in fact shipped on 2026-05-05; the trigger half is B8 now, since the shipped
webhook resolves the wrong item.)*

### I — Infrastructure

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| I1 | CSRF protection: ~~form token validation on login and settings forms~~ **decided 2026-09-26: an `Origin`/`Referer` check on every state-changing request** | 4 | 1 | **SHIPPED 2026-09-27; LIVE (v1.10.0)** — proxy-shape smoke passed; browser save = **operator action** (decided 2026-10-09, round 5) | [#14](https://github.com/bpoulliot/xenotag/issues/14) |
| I2 | Backup/restore API: download/upload state.db; prevents full rescan after container upgrades | 4 | 2 | **CLOSED 2026-09-26** — moved to [Deferred / Out of Scope](#deferred--out-of-scope) | [#18](https://github.com/bpoulliot/xenotag/issues/18) |
| I3 | Alembic DB migrations: structured schema versioning; required before any further schema changes | 5 | 3 | **SHIPPED 2026-09-26** | [#15](https://github.com/bpoulliot/xenotag/issues/15) |
| I4 | HTTP connection pooling for Jellyfin/Sonarr/Radarr clients | 3 | 1 | **SHIPPED 2026-05-05** (`53c9f3f`) | [#19](https://github.com/bpoulliot/xenotag/issues/19) |
| I5 | Prometheus metrics endpoint | 3 | 2 | **SHIPPED 2026-09-27; LIVE (v1.10.0)** — scraped, three alert rules on the host | [#17](https://github.com/bpoulliot/xenotag/issues/17) |
| I6 | ntfy push notifications: configurable server URL, token, and topic in settings UI; notify on scan complete, scan error, and batch tag events | 3 | 2 | **CLOSED 2026-09-26** — superseded by I5 | — |
| I8 | **Secrets can only live in `config.yml`, which the app rewrites** — no env override, so the host's SOPS pipeline cannot reach them | 4 | 2 | **SHIPPED 2026-09-24** | — |
| I9 | **Sonarr/Radarr API keys cannot be externally managed** — I8's override table is addressed by dotted path, and the `*arr` keys live in a list | 3 | 3 | **SHIPPED 2026-09-27** · **RELEASED v1.11.0** (2026-10-07); prod wiring: **interactive session with the operator, after 1.12.0** (decided 2026-10-09, round 5) | — |
| I10 | **`ORJSONResponse` is deprecated in the FastAPI xenotag pins** — `main.py` sets it as the app-wide `default_response_class`, and every start logs a `FastAPIDeprecationWarning` | 2 | 1 | **SHIPPED 2026-09-26** | — |
| I11 | **The test client runs on a deprecated transport** — `starlette.testclient` over `httpx` logs `StarletteDeprecationWarning: … install httpx2 instead` | 2 | 1 | **SHIPPED 2026-10-06** (#118) · **RELEASED v1.11.0** (2026-10-07) | — |
| I12 | **`datetime.utcnow()` is deprecated** — `app/state.py` uses it at **7** sites, `last_scanned` among them (Python 3.12 `DeprecationWarning`) | 1 | 1 | **FIXED 2026-09-26** | — |
| I13 | **Eight CodeQL alerts are open on `main` and nothing tracks them** — three `py/path-injection`, two `py/weak-sensitive-data-hashing`, one each of clear-text logging, cookie injection and stack-trace exposure. **Triaged 2026-09-27:** five are false positives (probes committed); #6/#7 → B15, #8 → B16. What remains is dismissing the five on GitHub with the reasons recorded below | 3 | 1 | **DONE 2026-10-07** — #1, #3, #4, #5, #9 dismissed on GitHub as *false positive* with the comments below; #6/#7 fixed by B15, #8 by B16 (verified by `gh api` 2026-10-09) | — |
| I14 | **jellyfin-dev is not production's Jellyfin.** `docker-compose.dev.yml` pins `lscr.io/linuxserver/jellyfin:latest`, which is **12.1.0**; production runs 10.11.10, and the dev database was migrated to 12.1 on 2026-09-25 (no way back) | 2 | 2 | **READY** (2026-10-09) — production has run Jellyfin **12.2.0** (`jellyfin/jellyfin:12.2`) since 2026-10-06, so the 2026-10-05 decision can be built: move jellyfin-dev to 12.2 | — |

**I14 — FILED 2026-09-27, found while measuring B8. Not fixed here.**

`jellyfin-dev` reported `"Version":"12.1.0"` on start. `~/.mf-dev/configs/jellyfin/log/log_20260925.log`
shows 12.1.0 applying 21 database migrations at 2026-09-25 20:12 (the B5 session's start). Production's
`/System/Info/Public` says 10.11.10. Every dev measurement since then that assumes "dev = production's
Jellyfin" is off by a major version. B8 was affected, and its answers were re-taken on production by
GET. B12's measurement plan above ("on **jellyfin-dev** … write `xt-` tags … refresh") is exposed the same
way: a tag-loss behaviour found on 12.1 may not be 10.11.10's. Jellyfin does not downgrade a migrated
database, so pinning the image alone would not start on the existing config. **Options:** (1) pin
`jellyfin-dev` to production's image version and re-seed a fresh `~/.mf-dev/configs/jellyfin`, keeping
the 12.1 one aside for upgrade testing. (2) Keep 12.1 and write in every dev measurement that it is
not production's version. **Recommendation: (1)**, since dev exists to reproduce production. It is the
operator's call because re-seeding replaces the dev library that earlier sessions seeded (Firefly and
Serenity would have to be re-identified). The sibling trap is already known: `sonarr-dev` pulls
`:develop` and has to be pinned by an override.

**OPERATOR DECISION 2026-10-05:** not (1) as filed — instead of pinning `jellyfin-dev` back to
production's current 10.11.10, move it to Jellyfin 12.2 **together with** production's own
upgrade, when that happens. Production's upgrade is expected "maybe tomorrow"; nothing in this
repo changes until it does. Relabelled **DECIDED**, waiting on the production upgrade, not
NEEDS DECISION.

**RELABELLED READY 2026-10-09: the blocker is gone.** Production moved to Jellyfin 12.2.0 on 2026-10-06
(`~/docker` TODO I62); its image is the **official** `jellyfin/jellyfin:12.2`, and that image is already
pulled locally. Build: pin `jellyfin-dev` in `docker-compose.dev.yml` to 12.2 instead of
`lscr.io/linuxserver/jellyfin:latest` (12.1.0). The dev database is on 12.1, so 12.2 migrates it forward;
keep a copy of `~/.mf-dev/configs/jellyfin` first, since there is no way back. **Trap:** dev uses the
linuxserver image, whose `/config` layout differs from the official image's (`/config` + `/cache`, no
PUID/PGID) — either pin a linuxserver tag that carries 12.2.0 (check that it exists before pinning) or
switch to the official image and re-map its mounts. Then confirm `"Version":"12.2.0"` and that the
seeded Firefly/Serenity still resolve.

**Sweep 2026-09-26 — I1–I6.**

 - **I4 — SHIPPED 2026-05-05** (`53c9f3f`). `JellyfinClient` and the \*arr base client each hold
   one `httpx.Client` for their lifetime and close it (`_close_clients()` at the end of a scan),
   which is issue #19's scope exactly. GitHub #19 is still open.
 - **I3 — SHIPPED 2026-09-26.** Every schema change from here on is an Alembic revision in
   `app/migrations/versions/` (how: `app/migrations/README.md`); `create_all()` and
   `_migrate_schema()` are gone from `init_db()`, which now calls `app.migrate.upgrade_to_head()`.
   **Ran against production at v1.8.0's start (2026-09-27 05:49Z):** `state.db schema: stamped
   (revision 0001)`; v1.9.0's start logged `current (revision 0001)`, as every later start should. A
   `SchemaMismatchError` means the file is not the baseline and nothing was changed.
   - **What "exact" compares** (before stamping an unversioned file): per table, every column's
     name, declared type, NOT NULL, default and primary-key position; every index's columns,
     uniqueness and partialness (explicit ones by name, SQLite's automatic ones by columns); and
     the set of tables, views and triggers. Never SQLite's stored `CREATE` text. The reference is
     the baseline revision run on a scratch file and read back through the same reader.
   - **Concurrency:** the check-and-stamp runs in one `BEGIN IMMEDIATE` transaction, so a second
     process waits for the write lock and re-reads the version under it (8 processes on one
     barrier: one `created`/`stamped`, seven `current`; `tests/test_migrations.py`). Switching a
     new file into WAL cannot happen inside a transaction and fails fast with "locked", so it
     retries for up to 60 s.
   - **Measured** with `scripts/verify_state_db_upgrade.py` (self-test in CI) on two copies of
     production `state.db` + `-wal`/`-shm`: today's (10,644 rows: `media_state` 10,583,
     `scan_runs` 50, `scan_errors` 10, `app_meta` 1; empty WAL) and the 2026-09-24 backup
     (10,636 rows, 4 MB WAL). Both **stamped**, **0** of the `.dump` `INSERT` lines differing,
     schema outside `alembic_version` unchanged, second start `current`; the same copy with
     `media_state.content_rating` dropped was **refused**, naming the column, and left unstamped.
     An image built from the branch started healthy on an empty `/config` (`created`) and on a
     prod copy (`stamped`, then `current` after a restart); on the drifted copy it exited 3
     with the message. `python -m app.migrate check` (a CI step) exits 0 on the models and 1 with
     a column planted in `MediaState`.
   - **Added:** `alembic==1.20.0` (License-Expression `MIT`, read from the package metadata) and,
     through it, `Mako` 1.4.3 (`MIT`, same source), unpinned like the other transitive deps.
     `pip-audit -r requirements.txt`: no known vulnerabilities.

   The spec it shipped against, kept as written: Facts
   measured on a read-only copy of production `state.db` (2026-09-26): four tables
   (`media_state`, `app_meta`, `scan_runs`, `scan_errors`), columns and the one explicit index
   (`ix_scan_errors_item_id`) **identical to the SQLAlchemy models**, `PRAGMA user_version` 0,
   no version table. Today's schema management is `create_all()` plus `_migrate_schema()`, an
   unversioned `ALTER TABLE … ADD COLUMN` for five columns — the debt this item exists to stop
   growing. Spec:
   1. **Alembic** (named by #15 and by the standing rules), with `render_as_batch=True` so later
      SQLite `ALTER`s work.
   2. **Baseline revision = today's schema, exactly**, including `_migrate_schema()`'s five
      columns. Its `upgrade()` must be safe on a database `create_all()` already built.
   3. **Run it from `init_db()`**, not a Docker entrypoint: `init_db()` is the one path shared by
      the app, the tests and the scratch-uvicorn recipe. An **unversioned** database (no
      `alembic_version`) whose tables exist is checked against the baseline and **stamped**;
      a mismatch refuses to start with a message naming the difference — never a guess. A fresh
      database is `upgrade head`. `create_all()` and `_migrate_schema()` then go.
   4. **Rollback:** an older image against a migrated database must still work for additive
      changes (SQLAlchemy ignores the version table and unknown columns); say so in the README,
      and say that a non-additive migration needs a backup first.
   5. **Acceptance:** a copy of production `state.db` (+ `-wal`/`-shm`) upgrades to head with every
      table's rows byte-identical (`.dump` diff); a fresh database's schema equals the models'
      (`alembic check` passes, run in CI); a deliberately drifted database is refused. No live
      step — prod picks it up at the next release, as with any change.
 - **I1 — NEEDS DECISION → READY, decided 2026-09-26 (below).** The issue's own threat model is out of date. The session cookie is
   `SameSite=lax` (`routes.py`), which already withholds it from cross-site POSTs — including
   top-level form posts, which #14 says it "does not cover" — and production sits behind
   Authentik forward-auth at SWAG. The residual risk is **same-site**: every other
   `*.bitmapserv.org` app is the same *site*, so a compromised sibling could POST to xenotag with
   the cookie attached. **Question:** is that worth closing? Options: **(a)** reject any
   `POST`/`PUT`/`DELETE` whose `Origin` (or, absent that, `Referer`) is not the request's own
   origin — ~30 lines of middleware, `/webhook/*` exempt (it authenticates by token), and no
   frontend change; **(b)** the synchronizer/double-submit token #14 proposes — every mutating
   `fetch` in `index.html` changes; **(c)** close: `SameSite=lax` + Authentik is enough for a
   single-admin deployment. **Recommendation: (a)** — it closes the same-site hole at a
   fraction of (b)'s surface. The implementer must verify behind SWAG that the proxied `Host`
   matches the browser's `Origin`, in a throwaway container, before claiming it.

   **OPERATOR DECISION 2026-09-26:** (a) — reject `POST`/`PUT`/`DELETE` whose `Origin` (else `Referer`) is
   not the request's own origin, `/webhook/*` exempt, verified behind SWAG. **READY.**

   **I1 — SHIPPED 2026-09-27; released in v1.10.0.** `app/web/csrf.py`, a pure ASGI middleware
   added in `app/main.py` inside `_SecurityHeaders` (so its 403s carry the same headers). It checks
   `POST`/`PUT`/`PATCH`/`DELETE`, and `/webhook/*` is exempt. `Origin` is compared, or `Referer` when
   there is no `Origin`, as a (scheme, host, port) triple against the request's own origin. A
   request with neither header, or with blank ones, is allowed as a non-browser client. `null`, a
   wrong scheme, a wrong port and a suffix-extended host are all rejected. A rejection logs
   `Rejected cross-origin <METHOD> <path>: Origin <x> is not this request's origin <computed>`.
   - **"Own origin" behind SWAG.** SWAG's `proxy.conf` sends `Host $host`,
     `X-Forwarded-Host $host:$server_port` and `X-Forwarded-Proto $scheme`, over a plain-http hop
     to `xenotag:7755`. uvicorn trusts forwarded headers only from 127.0.0.1: it has the default
     `forwarded_allow_ips`, and neither the Dockerfile `CMD` nor prod compose sets it. So
     `request.url` reads `http://` behind SWAG, and a naive comparison would reject every save.
     The middleware therefore reads the scheme from `X-Forwarded-Proto` and the host from
     `X-Forwarded-Host`, then `Host`. The port comes from the host, then `X-Forwarded-Port`, then
     the scheme's default. For a proxy chain it takes the first comma entry. Trusting these headers
     is safe without a proxy too: they are not CORS-safelisted, so a cross-origin page cannot make
     a browser send them without a preflight, and xenotag answers none. The Dockerfile's uvicorn
     flags were deliberately **not** changed: `--forwarded-allow-ips '*'` would also let any
     client spoof `X-Forwarded-For`, which the login rate limiter keys on.
   - **Proxy verification** (the operator's condition). A throwaway container built from the
     branch ran behind a throwaway nginx: the `lscr.io/linuxserver/swag` image's own nginx, with the
     xenotag vhost's `location` block and `proxy.conf`/`resolver.conf` copied verbatim. Only the
     Authentik includes and the real cert were left out. They shared a scratch `--internal`
     network, removed afterwards. Results: a login through the proxy with `Origin: https://<host>`
     → 302 and a cookie; the Settings save (`GET` then `PUT /api/settings`) → **200 `saved`**; the
     same `PUT` with `Origin` `https://evil…` / `http://<host>` / `https://<host>:8443` / `null` →
     **403** each; a same-origin `Referer` with no `Origin` → 200 and a foreign one → 403; neither
     header → 200. `POST /webhook/sonarr` with a foreign `Origin` → 200 (exempt), and
     `POST /scan/cancel` → 403 foreign, 409 same-origin (the request reached the route). uvicorn's
     access log named the nginx container as the peer, confirming it applied no forwarded headers.
     The computed origin was `https://<host>:443`. The harness and output are in
     `~/docker/xenotag/i1-proxy-verify-20260927/`.
   - **Non-browser callers checked.** `grep` over `~/docker` found none that `POST`s to xenotag
     except the documented in-container recipe (`POST http://127.0.0.1:7755/scan/full` with a
     minted cookie; U2 release README). It sends no `Origin`, so it is unaffected. The poll
     scripts and the healthcheck only `GET`.
   - **Not covered, by the decision:** `/webhook/*` is exempt, and production's `webhooks.secret`
     is empty (B8), so that route is still reachable cross-site. It only queues a single-item
     re-tag.
   - **Tests** `tests/test_csrf_origin.py`, 115 of them. A guard pins the 16 mutating routes,
     re-listed from `routes.py`, so a new route has to be looked at. Each of the 15 checked routes
     is tested four ways: foreign `Origin` → 403, same-origin → reaches the route, `Referer`
     fallback both ways, neither header → passes. More tests cover a signed-in admin's
     cross-origin `/scan/full` (403, and no scan starts) against the same-origin one (starts), the
     SWAG header set (public https origin passes; wrong scheme, host or port 403; without the
     forwarded headers it would 403), the webhook exemption, GET unchecked, 403 carrying the CSP,
     and both parsers directly. Seven mutants (middleware removed, reject-all, forwarded headers
     ignored, host-only compare, neither-header rejected, no `Referer` fallback, webhook not
     exempt) each turn the file red: 43/34/8/6/16/15/1 failures. Suite **512 → 627**, baseline
     re-measured from a `git archive` of `origin/main`.
   - **Post-deploy smoke (the releasing session or the operator):** through
     `https://xenotag.bitmapserv.org`, save Settings once. It must say saved, and
     `docker logs xenotag` must carry no `Rejected cross-origin` line for it.
   - **Released in v1.10.0, 2026-09-27 (the I5 session).** The browser save above needs the
     operator's Authentik login, so it is **still owed**. What the session could do without it:
     from inside the `swag` container (the real upstream peer), `PUT /api/settings` to production
     with SWAG's header set (`Host`, `X-Forwarded-Host: xenotag.bitmapserv.org:443`,
     `X-Forwarded-Proto: https`) and **no session**: `Origin: https://xenotag.bitmapserv.org` →
     **401** (passed the check and reached the route, which refused the missing session — nothing
     was written); `https://evil.example.org` → **403**; `http://xenotag.bitmapserv.org` → **403**.
     The app's computed origin was `https://xenotag.bitmapserv.org:443`. The two
     `Rejected cross-origin` lines at 08:10:44 MDT are those two negative controls, not a save.
   - **v1.11.0 smoke, 2026-10-07 (the release session).** A real save, short of the browser: from
     inside `swag`, with SWAG's `proxy.conf` header set, Origin `https://xenotag.bitmapserv.org`
     and a session the app minted for itself, `PUT /config` with the file's own text → **200
     saved**, `config.yml` byte-identical, **no `Rejected cross-origin` line**. Control: Origin
     `https://evil.example.org` → 403 (the one `Rejected cross-origin` line, 21:27:26 MDT). The
     browser save through Authentik is **still owed** by the operator.
 - **I2 — NEEDS DECISION → CLOSED 2026-09-26 (below); the premise does not hold for this deployment.** "Prevents a full
   rescan after container upgrades" assumes the upgrade loses `state.db`. It does not: production
   bind-mounts `~/docker/xenotag/config` at `/config`, `state.db` lives there, and restic backs up
   `~/docker` nightly. I8 already answered the `config.yml` half (back it up only through
   `_persistable()`'s strip). **Question:** keep I2 for other deployments, or close? Options:
   (a) close it as not needed; (b) keep it as download-only (`GET /api/backup` via SQLite's
   `.backup()`, which is safe against a live WAL — see the docker TODO's I33); (c) keep the full
   download + restore. **Recommendation: (a)**, moved to Deferred with this reason. Restore is the
   risky half (swapping the DB under a live engine) and nothing here needs it.

   **OPERATOR DECISION 2026-09-26:** (a) — close: `state.db` is bind-mounted and restic-backed; moved to
   **Deferred / Out of Scope** with that reason. **CLOSED.**
 - **I5 and I6 — NEEDS DECISION → decided 2026-09-26 (I5 READY, I6 CLOSED; below), one question for both:** how should xenotag tell the operator
   something happened (a scan finished, failed, or **HALTED** its \*arr writes)? The host already
   runs Prometheus → Alertmanager → ntfy (`~/docker/monitoring/ALERTING.md`). Options:
   **(a)** I5 only — a small `/metrics` (last scan success/failure time, items scanned/tagged,
   errors, \*arr writes and **halts**) scraped over the Docker network, with the alert rule added
   in `~/docker/monitoring`; close I6 as superseded; **(b)** I6 only — xenotag posts to ntfy
   itself, which needs a publish token in its config (an I8-style secret) and duplicates the host
   pipeline; **(c)** both. **Recommendation: (a)** — one alerting path, no new secret, and a halt
   is exactly the kind of state a scrape sees and a push can miss. Constraints for the spec if
   (a): metric names `xenotag_*` (#17 still says `metafin_*`); the app runs **one** uvicorn
   process, so the plain registry, and **never `PROMETHEUS_MULTIPROC_DIR`** (the
   `accesslens_metrics` leak of 2026-09-18 is what that costs); `/metrics` unauthenticated on the
   container port is acceptable because the public hostname is behind Authentik.

   **OPERATOR DECISION 2026-09-26:** (a) — build I5 (`/metrics`, `xenotag_*` names, the plain registry, never
   `PROMETHEUS_MULTIPROC_DIR`) plus an Alertmanager rule in `~/docker/monitoring`, and close I6 as
   superseded. **I5 READY, I6 CLOSED.**

**I5 — SHIPPED 2026-09-27.** `GET /metrics` (unauthenticated, not in the OpenAPI schema) serves
`app/metrics.py`'s own `CollectorRegistry` — the plain in-process registry the decision named.
`prometheus_client` reads `PROMETHEUS_MULTIPROC_DIR` when it is *imported*, so `app.metrics`
removes both spellings of it from the environment first, with a WARNING; a subprocess test proves
the library writes multiprocess files into an inherited directory on its own and writes none
through `app.metrics`. The metric list, names and labels are in the README ("Prometheus metrics")
and pinned by `tests/test_metrics.py`. Choices the spec left open:

* **A failed scan** is one that could not list Jellyfin (the existing FATAL path) or raised out of
  `_run_scan()`. Per-item errors do not fail a scan; they are `xenotag_scan_errors_total` by the
  four `error_type` shapes — `process_error: <text>` is collapsed to `process_error`, since a label
  is never free text. A **cancelled** scan is its own outcome and moves neither timestamp.
* **Restarts.** The registry is in memory, so at startup the last completed `scan_runs` row seeds
  the success timestamp and last-scan counts (without it every deploy reads as "no scan for 20,000
  days"), and a stored *live* \*arr report with `halted` set seeds the halt. `scan_runs` cannot tell
  a cancelled scan from a success, so the seed counts it as one; a failure is stored nowhere and a
  restart forgets it — the 26-hour rule still catches a scan that keeps failing.
* **The halt** is a gauge that goes to 1 at the halt (mid-scan, so a scrape sees it before the
  scan ends) and back to 0 only when a later **live, uncancelled** scan finishes its \*arr sync
  without one. A dry run writes nothing and a webhook touches one item, so neither clears it.
* The label is `arr_instance`, never `instance` (Prometheus owns that one). Known label sets start
  at 0 so `increase()` sees the first increment; `*_created` series are off.
* A 200 scrape is dropped from uvicorn's access log; anything else is still logged.

Dependency added: **`prometheus_client` 0.26.0**, pinned in `requirements.txt`; licence read from
the package metadata: `License-Expression: Apache-2.0 AND BSD-2-Clause` (its NOTICE: the bundled
`decorator` 4.0.10 is 2-clause BSD). pip-audit clean. The host side — the scrape job and three
alert rules (no successful scan in 26 h, last scan failed, \*arr sync halted) — lives in the
operator's monitoring config, not this repo. Found while building it: **B22**.

**I5 — LIVE 2026-09-27 (v1.10.0).** Released with `bump=minor`; v1.10.0 carried I5, I1, P7's
ordering half and the B21 filing — no tag-vocabulary change (`tagger.py`, `iso639.py` and
`_tag_config_hash()` identical to v1.9.0), so no re-tag. Deployed 14:09Z; `state.db` backed up
after a clean stop as `state.db.bak-20260927-pre-i5` (WAL checkpointed, no `-wal`/`-shm`);
`:1.9.0` is still local for a back-out. At startup the seed read scan 148 back exactly
(completed 12:52:11Z, 9,399 scanned / 9,389 tagged / 9,341 images, 4,241 s) and no halt. The
host's Prometheus scrapes `xenotag:7755` as job `xenotag` over `docker_frontend` (a network both
were already on); target UP. Rules `XenotagNoSuccessfulScan` (> 26 h), `XenotagLastScanFailed`,
`XenotagArrSyncHalted` (critical) were proven with `promtool test rules` — a must-fire and a
must-not-fire case each, and six mutated expressions each fail the tests — and each selector
returns the one live series. Smoke checks: I1 as recorded under its item; P7 — production has no
`prefer_languages` (`[]`, the byte-identical default), `/api/languages` and `/preview/image` answer
200. A 200 scrape is absent from `docker logs xenotag` as designed.

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

**I9 — SHIPPED 2026-09-27; released in v1.11.0 (2026-10-07).** Option (d), as decided: `ArrInstance` has an
optional `api_key_file`. When it is set, the file supplies the instance's key on every load and every
save, wins over an `api_key` beside it, and that `api_key` is never written back — not by the Settings
page, the raw YAML editor, `save_auth()` or the first-run bootstrap. The path saves normally. An
unreadable or empty file is fatal at startup and refuses a save (nothing written); there is no
"ignored with a warning" case, because unlike I8's bare variable a set `api_key_file` is always
deliberate. Loading never writes, so the stale key already in `config.yml` is purged by the next
save. Startup logs each file-backed instance by name and path, never by value. The Settings row
shows its key read-only with an "Externally managed" line naming the path; changing the path is a
raw-YAML-editor job (no new UI). 30 tests in `tests/test_arr_key_files.py`; with the strip in
`_persistable()` removed, the ten save-path tests fail, and the raw-editor re-dump, safe-dict
blanking, `config_as_yaml` strip, file read, empty-file check, test-route key and the page's
`cleanInstance()` each fail their own test when mutated. Checked end to end in a throwaway
container (network none, fake keys): the first-run bootstrap and a real browser Settings save both
left `config.yml` without the stale or the file key and with the path.

One decision the note did not settle: the Settings **Test** / root-folder buttons for a file-backed
row send the key the *configured* instance read, matched by its `api_key_file` — never a file named
only by the request — so a test request cannot read an arbitrary container path into an outbound
header. An unsaved file-backed row answers "save first". (A save can still point `api_key_file` at
any path the container can read; that is inherent to (d) and needs an authenticated admin.)

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

*Sweep 2026-09-26 — **NEEDS DECISION**, confirmed, with a fourth option the note did not list.*
**(d) Indirection in the instance itself:** `ArrInstance` gains an optional `api_key_file`
(a path, e.g. `/run/secrets/sonarr_4k`). When set, the key is read from that file at load and
`api_key` is never written back. The *path* is not a secret, so it saves normally; reordering or
renaming an instance cannot detach it, because the binding lives in the same row; and it is
exactly the `_FILE` convention `materialize-secrets.sh` already renders for the rest of the stack.
**Recommendation: (d).** It has none of by-index's silent-reorder failure or by-name's rename and
mangling rules, and unlike "don't" it lets the five \*arr keys — now carrying live write access
since B5 — rotate through the host's SOPS pipeline. The per-row read-only treatment in Settings
is still needed; I8's fatal-on-unreadable-`_FILE` rule carries over unchanged.

**OPERATOR DECISION 2026-09-26:** (d) — a per-instance optional `api_key_file`; `api_key` is never written back when it
is set, and I8's fatal-on-unreadable rule applies.

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

**I10 — SHIPPED 2026-09-26.** `default_response_class=ORJSONResponse` and its import are gone from
`app/main.py`, so every route renders through Starlette's `JSONResponse`, and `orjson` is gone from
`requirements.txt` (`pip show orjson` in a venv built from the old `requirements.txt`: `Required-by:`
empty — nothing needed it transitively). Measured:

* **The warning.** `tests/test_json_responses.py::test_a_request_records_no_fastapi_deprecation_warning`
  makes one `GET /health` through `TestClient` and asserts no `FastAPIDeprecationWarning` was recorded.
  It **failed on `main` (`e49e4ec`)** and passes after. The same app started under uvicorn on a scratch
  config (`CONFIG_PATH`/`STATE_DB` in `/tmp`): `GET /health` + an authenticated
  `GET /api/badge-contrast` logged **1** `FastAPIDeprecationWarning` on `main`, **0** after.
* **Non-finite floats.** `/api/badge-contrast` driven with `opacity` 0, -1, 1.5, 1e308, `nan`, `inf`,
  `-inf`; identical fill and text; malformed colours (`#fff`, `red`, empty, `zzzzzz`, `#gggggg`,
  `#12345`); every badge hidden — **14/14 return 200 with only finite numbers**, opacity always in
  [0.1, 1.0]. No defect found. A NaN injected into the route's output makes the request raise
  `ValueError: Out of range float values are not JSON compliant`, so the test does fail when it should.
* Full suite 220 passed with `orjson` uninstalled from the venv.

**Worth knowing:** `opacity=nan` is safe only by argument order. FastAPI parses `nan`/`inf` from a
query string as a float, and `_image_config_from_params()` clamps with `max(0.1, min(1.0, opacity))`;
`min(1.0, nan)` is `1.0`, but `min(nan, 1.0)` would be `nan`. The `nan` case in the test pins it.

**I11 — filed 2026-09-26, seen in I10's test output.** `from fastapi.testclient import TestClient`
logs `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2
instead` (Starlette 1.6.0). It is the *test* path only: `httpx` itself is a runtime dependency
(`app/clients/arr.py`, `app/clients/readonly.py`, `app/arr_sync.py`) and must stay. NEEDS MEASUREMENT:
whether `httpx2` is a drop-in for the test client at the pinned Starlette, and whether it belongs in
`requirements.txt` or a test-only install (CI installs `requirements.txt pytest`).

*Sweep 2026-09-26 — **NEEDS MEASUREMENT**, confirmed; queueable.* **What:** in a scratch venv
built from `requirements.txt` (never the shared tree): install `httpx2`, run the full suite with
`-W error::DeprecationWarning`, and record (1) whether `TestClient` picks it up with no import
change, (2) whether the warning is gone, (3) the pass count against the baseline (220 after I10),
and (4) whether `httpx2` pulls anything that conflicts with the runtime `httpx`. Also check its
PyPI metadata (maintainer, licence, release history) — a package whose name is one character off
a popular one deserves a provenance look before it enters CI. **Roughly 30 min.** The
test-only-install question answers itself from (4).

*Measured 2026-09-26 — now **READY**.* Scratch venv (Python 3.12.3, CI's version) built from
`requirements.txt` + `pytest` at `af91b0c`; `pytest tests/ -q`.

* **Baseline, no `httpx2`: 251 passed, 0 skipped, 1 warning** (the sweep's "220" predates later
  tests). The warning is raised at `fastapi/testclient.py:1`
  (`from starlette.testclient import TestClient`): *"Using `httpx` with `starlette.testclient` is
  deprecated; install `httpx2` instead."* Starlette 1.6.0's `testclient.py` does
  `try: import httpx2 as httpx` and falls back to `httpx` with that warning.
* **`StarletteDeprecationWarning` is a `UserWarning`** (MRO: `StarletteDeprecationWarning →
  UserWarning → Warning`; its docstring says so on purpose, "visible by default") — the same trap
  as I10's `FastAPIDeprecationWarning`. So `-W error::DeprecationWarning` **cannot** catch it:
  without `httpx2` that run is 251 passed *with the warning still printed*, while
  `-W error::UserWarning` fails collection (`tests/test_json_responses.py`). Any CI guard for this
  must name `UserWarning` (or `starlette.exceptions.StarletteDeprecationWarning`).
* **`pip install httpx2` → 2.13.1**, pulling `httpcore2` 2.13.1 and `truststore` 0.10.4 (3 packages,
  nothing else changed in `pip freeze`). **Picked up with no import change:** `TestClient.__mro__` is
  `starlette.testclient.TestClient → httpx2.Client`; `starlette.testclient.httpx` is module `httpx2`;
  the client's `_transport` is Starlette's `_TestClientTransport`, a subclass of
  `httpx2.BaseTransport`; `isinstance(client, httpx.Client)` is now False.
* **Warning gone:** 251 passed with no flag, with `-W error::DeprecationWarning`, and with `-W error`
  (every warning an error) — 0 warnings in all three. Same count as the baseline.
* **No conflict with the runtime `httpx`:** `pip check` clean; `httpx` 0.28.1 and `httpx2` 2.13.1
  import side by side from separate packages (`httpcore` vs `httpcore2`); `app.clients.arr`,
  `app.clients.readonly` and `app.arr_sync` still resolve `httpx` to `httpx`, and
  `tests/test_arr_sync.py`'s `httpx.MockTransport` fakes are untouched. Starlette's own `full` extra
  requires both (`httpx2>=2.0.0` and `httpx<0.29.0,>=0.27.0`), so coexisting is the intended setup.
  The runtime never imports `httpx2`.
* **Provenance — sound.** PyPI `httpx2`: author Tom Christie (the `httpx` author), maintainer
  "Pydantic Services Inc.", sole PyPI owner `Kludex`, who authored and merged Starlette's own
  "Support httpx2 in the test client" (encode/starlette#3291, 2026-05-25) and publishes Starlette
  releases (1.7.0). Source `github.com/pydantic/httpx2` (not a fork; created 2026-05-11, the day of
  the first upload). Licence `BSD-3-Clause` (read from package metadata; `httpcore2` BSD-3-Clause,
  `truststore` MIT). History: 18 releases, 0.0.0 (2026-05-11) → 2.13.1 (2026-09-23), none yanked.
  Starlette's release notes name it three times (#3291 test-client support, #3304 type checking,
  #3323 `full` extra) and its README links `pypi.org/project/httpx2`. No PEP 740 attestation is
  published for 2.13.1 — not a red flag, just absent.
* **`pip-audit` 2.10.1**, 70 packages: 0 vulnerabilities in `httpx2`, `httpcore2`, `truststore`,
  `httpx`, `httpcore`; the only findings are in the venv's own bundled `pip` 24.0, not a project
  dependency.

**Spec.** Test-only: the runtime never imports `httpx2`, and putting it in `requirements.txt` would
ship three unused packages in the image (the `Dockerfile` installs `requirements.txt`). Add a root
`requirements-dev.txt` holding `httpx2==2.13.1` (Dependabot's pip ecosystem at `/` picks it up), and
change `ci.yml`: the test job to `pip install -r requirements.txt -r requirements-dev.txt pytest`, and
the `pip-audit` step to `pip-audit -r requirements.txt -r requirements-dev.txt` so the new package is
audited. Leave `pytest` where it is (no version change in this item). Acceptance: the CI test log's
warnings summary has no `StarletteDeprecationWarning`; optionally add
`-W error::starlette.exceptions.StarletteDeprecationWarning` to the pytest step — **not**
`error::DeprecationWarning`, which cannot see it.

**I11 — SHIPPED 2026-10-06 ([#118](https://github.com/bpoulliot/xenotag/pull/118), merged, not
released).** Added root `requirements-dev.txt` pinning `httpx2==2.13.1` (test-only; the
`Dockerfile` installs only `requirements.txt`, so the runtime image is unchanged — `git diff`
touched no `app/` file). `ci.yml`'s `tests` job now installs
`-r requirements.txt -r requirements-dev.txt pytest`; `lint-and-security`'s `pip-audit` step now
audits both files. The guard landed in `pyproject.toml`'s `[tool.pytest.ini_options]`
`filterwarnings`, naming `starlette.exceptions.StarletteDeprecationWarning` directly (not
`DeprecationWarning`, which the measurement found blind to it). Re-measured in a scratch venv
built like CI (Python 3.12.3): baseline grew to **777 passed, 1 warning** (the earlier 251 is
stale); `httpx2==2.13.1` is still PyPI's latest, same provenance (BSD-3-Clause, Pydantic
Services Inc. / Kludex, 0 `pip-audit` findings) as the 2026-09-26 measurement. Both-directions
guard check: with `httpx2` temporarily uninstalled, the `filterwarnings` entry fails collection
on 5 test modules (`starlette.exceptions.StarletteDeprecationWarning`); reinstalling it restores
777 passed, 0 warnings. `ruff check app/` / `black --check app/` clean. PR #118 green on all 5
checks (CodeQL, dependency-review, docker-build, lint-and-security, tests) on head SHA
`acfccc6`; merged via `PUT .../pulls/118/merge` (`sha=acfccc6`), `main` confirmed moved to
`ff651ba`.

**I12 — filed 2026-09-26, seen in I10's test output; FIXED 2026-09-26 (below the sweep note).** `app/state.py:137` sets
`row.last_scanned = datetime.utcnow()`, deprecated since Python 3.12. NEEDS MEASUREMENT before the
obvious swap to `datetime.now(UTC)`: that returns an AWARE datetime, and whether the column and every
comparison against `last_scanned` tolerate aware values (SQLite stores naive) has not been checked.

*Sweep 2026-09-26 — **READY**; the measurement is unnecessary because the fix can avoid the
question.* Two corrections to the filing first: `utcnow` appears at **7** sites, all in
`app/state.py`, not one — three `Column(DateTime, default=datetime.utcnow)` defaults
(`ScanRun.started_at`, `ScanError.first_seen`, `ScanError.last_seen`) and four calls
(`last_scanned`, `start_scan_run`, `finish_scan_run`, `upsert_scan_error`). **Spec:** add one
helper, `_utcnow() -> datetime` returning `datetime.now(UTC).replace(tzinfo=None)`, and use it
at all seven (the defaults take the function, not a call). It yields the **same naive UTC
value** `utcnow()` did, so nothing stored or compared changes and the aware-vs-naive question
never arises. Acceptance: `grep -rn utcnow app/` is empty; a test asserts the helper returns a
naive value within a second of `datetime.now(UTC)`; the test run logs no `utcnow`
`DeprecationWarning`.

*Fixed 2026-09-26.* `app/state.py` gained `_utcnow()` (naive UTC via `datetime.now(UTC)`), used at
all 7 sites; the three column defaults take the function. `tests/test_state_utcnow.py` checks the
helper is naive and within 1 s of `datetime.now(UTC)`, and that every default fires per row (two
rows 20 ms apart differ) — with a negative control proving a frozen `default=_utcnow()` fails that
check. Suite 225 → 228 passed; the warnings summary went from 5 (4 `utcnow`: 3 at `state.py:137`, 1
via a SQLAlchemy column default, attributed to `sqlalchemy.sql.schema` — so a
`-W error::DeprecationWarning:app.state` filter would have missed it) to 1 (the `httpx2` one, I11).
The acceptance grep is `datetime\.utcnow`: the helper's own name makes a bare `utcnow` grep non-empty.

**I13 — filed 2026-09-26 by the readiness sweep as NEEDS MEASUREMENT; measured 2026-09-27, now READY (below).** B2 (2026-09-25) noted seven
open CodeQL alerts on `main` that "deserve an item of their own, with evidence" and none was
filed. Re-read on 2026-09-26 via `gh api repos/bpoulliot/xenotag/code-scanning/alerts`: **eight**
are open, one new since B2 —

| alert | rule | where |
|---|---|---|
| #1 | `py/cookie-injection` | `app/web/routes.py:125` |
| #3, #4, #5 | `py/path-injection` | `app/web/routes.py:758–759` (`preview_image`'s `sample`) |
| #6, #7 | `py/weak-sensitive-data-hashing` | `app/auth.py:52, 56` |
| #8 | `py/clear-text-logging-sensitive-data` | `app/auth.py:133` |
| **#9** | `py/stack-trace-exposure` | `app/web/routes.py:865` — **not in B2's list** |

**What to measure:** for each alert, read the flagged source-to-sink path and decide *real* or
*false positive*, with the reason written down (B2's untested guess: `Path(sample).name`
sanitises #3–5 in a way CodeQL does not model). For a real one, a one-line failure scenario;
for a false positive, the exact dismissal reason to use. **On:** the code and the alert
details, no running system. **Roughly 1–2 h.** Deliverable: the table above with a verdict
column, and each real alert filed as its own B item. Dismissing alerts is an outward action on
GitHub and is **not** part of the measurement.

**MEASURED 2026-09-27 — relabelled READY: dismiss five, fix two via B15, decide one via B16.**

*Instruments.* `gh api repos/bpoulliot/xenotag/code-scanning/alerts?state=open` returned the same
eight as the table above (#1, #3–#9), every `most_recent_instance` at `ddb8eb5`, every line number
unchanged; #2 is `fixed` (2026-06-01) and none is dismissed. The REST API returns no code-flow
paths, so each source → sink was traced by reading `origin/main` at `ddb8eb5`. What CodeQL treats
as a sanitiser was read from `github/codeql` `main` on 2026-09-27
(`python/ql/lib/semmle/python/security/dataflow/{PathInjection,CookieInjection,StackTraceExposure}Customizations.qll`)
— not necessarily the exact pack version the action ran. Every "false positive" below is backed by
`tests/test_codeql_triage.py` (21 tests, real routes through `TestClient`). Each test carries a
control that fails the other way, and the file was run against three mutants in a scratch copy —
`sample` joined raw, the raw username as the cookie value, a formatted traceback stored as the
dry-run error: **5, 5 and 1 tests went red respectively**, so the probe is not one that can only
agree with itself. #6–#8 were reproduced with scratch probes (not committed: they pin defects).

| alert | rule | verdict | reason |
|---|---|---|---|
| #1 | `py/cookie-injection`, `routes.py:125` | **false positive** | The cookie value is `create_session()`'s token: `base64url("<user>:<expiry>:<hmac>")`, set only when the submitted username **equals** the configured one. Five hostile usernames (`;` + `Domain=`, CR/LF + `Set-Cookie:`, a quote + `HttpOnly=false`, a comma, NUL) each gave exactly one `Set-Cookie`, a value in `[A-Za-z0-9_-]+=*` (the stdlib quotes it when it holds `=` padding), only the expected attributes, and a token that decodes back to the username. A non-matching username gets a 401 and no cookie. CodeQL's cookie query has no sanitiser at all and propagates taint through base64 encoding. |
| #3, #4, #5 | `py/path-injection`, `routes.py:758–759` | **false positive** | `sample` is reduced to `Path(sample).name`, so every path touched is `_PREVIEW_CACHE/<one component>`. Four inputs whose naive join provably resolves to a file outside the cache (`../x`, an absolute path, `../../<dir>/x`, `legit.jpg/../../x`) read nothing; so did `..%2F`, `%2E%2E%2F`, double-encoded `%252F`, backslashes (one filename on POSIX), NUL (pathlib answers `False`, never raises), `.`, `/` and `..`. `..` is the only name that steps up — to the cache's parent, a directory, which fails `is_file()`. A spy on `Path.exists`/`is_file` shows nothing outside the cache is even stat'd, and a file in the cache is still read (also via a `../elsewhere/` prefix, which is dropped). CodeQL's barriers are a constant comparison, a normalise-then-prefix-check pair, or a models-as-data barrier; `PurePath.name` is none of them. B2's guess was right. |
| #6, #7 | `py/weak-sensitive-data-hashing`, `auth.py:52, 56` | **real, latent → B15** | What is hashed is **the admin password itself**, with bare `hashlib.sha256` — not an HMAC key (that is `_sign()`, HMAC-SHA256 with `secret_key`, not flagged) and not a cache key. The branch runs only when `import bcrypt` fails. Probe with `bcrypt` made unimportable: two hashes of one password are identical (no salt), equal plain SHA-256, and a `$2b$` hash no longer verifies. Unreachable in the shipped image (`bcrypt==5.0.0` pinned; prod's container imports 5.0.0; prod and dev store `$2b$`). |
| #8 | `py/clear-text-logging-sensitive-data`, `auth.py:133` | **real, by design → B16** | On a first run with no `XENOTAG_PASSWORD`, `bootstrap()` logs the generated password at WARNING; the logged value verifies against the stored hash. Controls: nothing is logged when `XENOTAG_PASSWORD` is set or a hash already exists. The log is stdout only (no file handler, no route serves logs). Prod today: 0 `FIRST RUN` lines in its `docker logs`, a `$2b$` hash, no `XENOTAG_*` env. |
| #9 | `py/stack-trace-exposure`, `routes.py:865–871` | **false positive** | The response's `error` is `str(exc)` of a failed dry run — the message, never a traceback. Probe: the **real** `run_arr_dry_run()` against a local Jellyfin that answers 401, with a sentinel API key; the report carries exactly `str(exc)` and contains no `Traceback`, no `File "`, no `.py`, and not the key (clients send keys as headers, so an `httpx` error's URL holds none). The route is 401 without a session, and the Settings page shows the message on purpose ("Last dry run failed: …"). CodeQL counts the caught exception object itself as stack-trace information. Residual, not a finding: the message is unbounded, so a future exception that embeds a secret would reach the admin's own browser. |

**The remaining work (READY):** after this lands on `main`, dismiss #1, #3, #4, #5 and #9 on
GitHub, each as **"false positive"** with the comment below verbatim (each under GitHub's 280
characters). #6/#7 close as *fixed* now B15 has merged (#115); #8 likewise with B16 (#125). Dismissing is an
outward action — the operator's, or a session whose item says so in so many words.

- **#1:** `Value is create_session()'s base64url HMAC token, set only when the username equals the configured one; no ; , " or CR/LF can reach the header. CodeQL propagates taint through base64. Pinned by tests/test_codeql_triage.py (I13).`
- **#3, #4, #5:** `sample is reduced to Path(sample).name, so the path is always _PREVIEW_CACHE/<one component>; '..' reaches only the parent dir, which fails is_file(). CodeQL has no barrier for PurePath.name. Pinned by tests/test_codeql_triage.py (I13).`
- **#9:** `Returns str(exc) of a failed dry run (the message, never a traceback) to the signed-in admin only; client keys travel in headers, not URLs. CodeQL treats the exception object as stack-trace info. Pinned by tests/test_codeql_triage.py (I13).`

**DONE — verified 2026-10-09** (`gh api repos/bpoulliot/xenotag/code-scanning/alerts`): #1, #3, #4, #5
and #9 are `dismissed`, reason *false positive*, 2026-10-07 00:12:45–50Z by the operator, with the
comments above; #6 and #7 are `fixed` 2026-10-06 15:18Z (B15), #8 `fixed` 2026-10-06 17:11Z (B16).
Still open, and not I13's: #10 (`py/stack-trace-exposure`, open after #135) and #11 (B25).

B2's trap still applies after dismissal: a PR whose diff re-attributes `preview_image`'s or
`login`'s signature can re-surface these on the PR check.

*(I10's original filing follows; its heading had been pasted twice.)*

**I10 — filed 2026-09-25, from the v1.7.0 deploy log. Nothing is broken; this is removal-proofing.**

On every start, prod logs:

    fastapi/routing.py:120: FastAPIDeprecationWarning: ORJSONResponse is deprecated, FastAPI now
    serializes data directly to JSON bytes via Pydantic when a return type or response model is
    set, which is faster and doesn't need a custom response class.

Verified 2026-09-25 against `main` and the running image (FastAPI **0.136.3**, pinned in
`requirements.txt`): the **only** use is `app/main.py:98`, `default_response_class=ORJSONResponse`,
imported at `main.py:9`. **Nothing else imports `orjson`** in `app/`, `scripts/` or `tests/`, so
`orjson==3.11.9` exists in `requirements.txt` solely for this line. It works today; it will stop
working when FastAPI removes the class, and a Dependabot bump of FastAPI is how that would arrive —
as a green-looking PR that fails at import.

**The fix, minimal and READY:** delete `default_response_class=ORJSONResponse` and its import, so
the app falls back to FastAPI's standard `JSONResponse`, and drop `orjson` from `requirements.txt`.
Performance is not a reason to keep it: payloads here are small, and the media browser is
paginated.

**The one trap, measured so the implementer need not rediscover it: non-finite floats.** orjson
writes `NaN`/`Infinity` as `null`; the standard `JSONResponse` **raises** on them (`allow_nan=False`),
so an endpoint that ever returned one would go from a quiet `null` to a 500. As of 2026-09-25 nothing
can: a search of `app/` finds no `inf`/`nan` construction, and the only float in
`app/web/schemas.py` is scan progress (`done`). But `app/contrast.py` (B2) computes ratios, and a
ratio is where a division goes non-finite. **Pin it with a test** — e.g. every JSON route's output
survives `json.dumps(..., allow_nan=False)` for the edge inputs it accepts — rather than trusting
today's grep. Datetimes are not a trap: none cross the response schemas.

**Optional, separate, not part of this item:** FastAPI's new fast path needs a return type or
`response_model`, and only 5 of the 35 routes in `routes.py` declare one. Adding them is typing
polish with a small speed-up; it is not required to clear the warning, so do not bundle it.

### P — Polish

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| P6 | **Background-aware palette (main + backup)** — sample the poster region under each badge and pick the palette that contrasts with it. | 4 | 4 | **READY (approved 2026-10-09)** — build the Settings UI on [#134](https://github.com/bpoulliot/xenotag/pull/134) (branch `feat/p6-rebase`, `HOLD:`) before it merges; L 0.12 and the per-drawn-row reading signed off. #95 is closed | — |
| P7 | ~~Overlay density / simplification — fewer, clearer badges by default.~~ **Redirected 2026-09-23: pills always inside the poster margins, plus a preferred order** — nothing hidden. **Containment measured 2026-09-26:** pills never cross the margin at defaults, but only because the layout hides metadata (1.3% of items). | 4 | 3 | Containment: **SHIPPED 2026-10-06** ([#126](https://github.com/bpoulliot/xenotag/pull/126)) · **RELEASED v1.11.0** (2026-10-07) — (d), 2-row budget, counted `+N`, stack clamped · ordering: **SHIPPED 2026-09-27; LIVE (v1.10.0)** | — |
| P8 | **Brand assets: icon, wordmark, favicon set** — replace the Metafin-era dragonfish mark everywhere it renders. | 3 | 2 | **SHIPPED 2026-09-23** | — |
| P9 | **UI theme retoken to the brand palette** — Charcoal/Deep Forest/Sage/Warm Gray/Bone, with the accent lightened to clear AA. | 3 | 3 | **SHIPPED 2026-09-24** | — |
| P10 | **Badge palette under a near-monochrome brand** — four badge categories, one brand green. | 2 | 2 | **SHIPPED 2026-09-24** | — |
| P11 | **Brand vectors must reproduce the concept art exactly** — the supplied SVGs draw a different shape, and the PNG fallback is clipped. | 3 | 4 | **CLOSED 2026-09-25 — keep the PNGs** | — |

**OPERATOR DECISION 2026-10-09 (second round) — P6 APPROVED as recommended. READY.** The work is the
rebase held in [#134](https://github.com/bpoulliot/xenotag/pull/134) (branch `feat/p6-rebase`); #95 is
closed, so do not redo the rebase.

- **Threshold L 0.12 — signed off** (the AAA boundary a single threshold can serve; numbers below).
- **Decision (1a) under P7's layout — the per-drawn-row reading is confirmed.** Each drawn row decides by
  the poster under **its own strip**, so a wrap row decides independently of the row above it; a `+N`
  pill follows the backup of the group it counts; groups that share a main colour share a backup.
- **Settings UI — to be built ON #134 before it merges:** (a) one checkbox, "adapt badge colours to the
  poster", **off by default**, with the note that it only matters below 100 % opacity; (b) four backup
  colour pickers (`backup_{video,audio,sub,rating}_badge_color`) carrying the same contrast warning the
  main pickers carry; (c) the Preview passes the checkbox and the four backup colours through (the routes
  already accept `adapt` and `backup_*_color`). #134 then loses its `HOLD:` and merges on green.

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

*Sweep 2026-09-26 — **NEEDS DECISION**, confirmed; the two open questions, with a recommendation.*
**(1) Selection:** (a) main + backup palette, chosen per badge row by the luminance of the poster
region under it against one threshold; (b) continuous — derive each fill from the region.
**Recommend (a):** two palettes can each be *measured* in advance — B2's contrast chips and
B4's separation probe both work on a fixed palette, and neither works on a colour computed per
poster — and it keeps the render deterministic and explainable. **(2) Operator control:**
(a) one checkbox, "adapt badge colours to the poster" (off by default), with the backup palette's
four pickers carrying the same B2 contrast chips as the main ones; (b) fully automatic, no
control. **Recommend (a)** — it matches the constrained-controls rule, and default-off means no
existing poster changes until the operator asks. Scope note for the spec: it only matters below
100% opacity (B1: an opaque pill hides the poster), so the checkbox should say so, and the
backup palette must also clear B4's dE 5 under simulated CVD. B3's cross-width test is the one
that fails if the cache key is not widened with the palette choice.

**OPERATOR DECISION 2026-09-26:** (1a) main + backup palette chosen per badge row by the luminance of the region
under it, and (2a) one checkbox, off by default, whose backup pickers carry B2's contrast chips;
the backup palette must clear B4's dE 5.

**P6 — UNBLOCKED 2026-10-06: [B21] shipped (#124), option (b).** The poster now composites a
translucent pill the way the instrument does (chip = poster to the level, 65–100%), so the numbers
below — all taken through the instrument's model — describe what renders. PR #95's probe
self-test, red on its branch, passes once B21 is merged in (local scratch merge). What is left is
what the last bullet lists, after merging `main` into the held branch.

**P6 — STOPPED 2026-09-27 on [B21]; the work is held, unmerged, in PR #95 (`HOLD:`).** Measuring
the threshold showed the poster path does not composite a translucent pill the way B1's instrument
does (B21), and P6 lives entirely below 100%. So its threshold, its backup colours and the chips its
pickers must carry would each encode a guess about how B21 is resolved. What the held branch has,
and what is left:

 - **Built, independent of B21:** `image.adapt_badge_colors` (off) + `backup_{video,audio,sub,
   rating}_badge_color` (B6-normalised); `overlay.region_luminance()` (exact linear-light mean from
   the histogram) over the laid-out row's own strip of the **bare** poster; `adapted_fill()` picks
   the backup where the region is on the *label's* side of the threshold (light regions for a
   light label, dark ones for a dark label); no adaptation at 100% (what the checkbox text says);
   `app/wcag.py` is the one luminance implementation. **The cache trap turned out not to need a
   new key term:** the choice is made before the tile is requested and arrives as `fill_hex`, which
   B3 already keys on — the tile stays a pure function of its seven arguments. Default off → 512/512,
   P7's byte-identical pins included.
 - **Probe:** `scripts/measure_adaptive_palette.py` (threshold sweep, backup contrast over every
   region it serves, CVD separation of every pair that can share a poster — backup/backup **and**
   backup/main across categories, since rows choose independently — a main-vs-adapted table, a
   seeded `--search`, `--self-test`). Its self-test's end-to-end check compares
   `render_badge_groups()` with the instrument and **fails until B21 is resolved**; it is in CI on
   that branch, so the PR is red on purpose.
 - **Provisional numbers (the instrument's model, white text, design opacity 65% — the pre-B1
   default):** the main palette holds AAA over flat greys up to 94 / 98 / 109 at 50 / 65 / 80%
   (L 0.112 / 0.122 / 0.153), while its AA boundary swings from grey 153 to "everywhere" over the
   same range — so an AAA boundary is the one a single threshold can serve: **L 0.12**. Backup
   (search: AA on white at 65%, hue within 30° of the category, chroma ≤ the main palette's 22,
   maximise the worst co-occurring dE) **`#0c332d` / `#120c2a` / `#332d0c` / `#1f0001`**: AA on
   white down to **65%** and AAA down to **79%** (main alone: 80% / 98%), worst co-occurring pair
   **dE 11.5** (main's own worst 12.3). Adapted worst over every flat grey: 3.19 → 4.51 at 65%,
   4.52 → 7.26 at 80%. Coloured regions (150 random RGB each side, seed 0): main ≥ 7.08, backup ≥ 4.88.
   Every one of these is re-derived by the probe once B21 lands; under (b) they should not move.
 - **Not built:** the Settings UI (checkbox + four backup pickers with chips) and the preview wiring
   in the template; the routes already accept `adapt` and `backup_*_color`.

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

*Sweep 2026-09-26 — P7 relabelled **NEEDS MEASUREMENT**: the containment half is queueable now.
The ordering half carries one open question (below); it can be answered in the same sitting as
the other decisions and neither half waits on the other.*

**Containment — what to measure.** Whether every pill lands inside the poster at every
`badge_size` × poster aspect × badge/rating position, or only usually. **Inputs from production**
(read-only `state.db` copy, 2026-09-26): the heaviest item carries **58** tags, **52** distinct
subtitle languages and **21** audio languages; the 99th percentile is **12** subtitle languages,
the 99.9th **35**. **Method:** render through `render_badge_groups()` — never a
re-implementation — with synthetic tag sets at p50 / p99 / p99.9 / max, over posters of 2:3,
27:40, 16:9 (backdrops) and a short 1:1, at widths 300 / 600 / 1000 / 2000, all three
`badge_size`s, and every position combination B10's test uses; take the pill rectangles the real
render reports (as `tests/test_rating_position.py` does) and count any that cross the poster
margin, or overlap the rating. **Self-test:** a planted oversized tag set must fail and a
single short pill must pass. **Roughly 2 h, render-only** — `generate_preview_bytes()` or
`render_badge_groups()` on synthetic images, no library file touched. Also covers B10's
"not covered" case (a tag stack reaching a rating on the opposite edge).

**Ordering — the question.** What does a "prefer" setting order, and with what control?
Options: **(a)** one `prefer_languages` list (e.g. `en, ja, de`) applied to both audio and
subtitle rows, everything else after it in today's order; **(b)** separate audio and subtitle
lists; **(c)** a precedence list per category, video and rating included. **Recommendation:
(a)** — it is the operator's own example, languages are where the long tail lives (the p99.9
item has 35 subtitle languages), and it is one new control rather than four. Per the
constrained-controls rule it should be a pick-list of the language codes actually present in the
index, not a free-text box. It changes pill order only — never which pills exist.

**OPERATOR DECISION 2026-09-26:** (a) — one `prefer_languages` pick-list (codes present in the index) for audio and
subtitle rows; it orders pills only, never which pills exist. The ordering half is **READY**.

**P7 ordering — SHIPPED 2026-09-27; released in v1.10.0.** `image.prefer_languages`, one list
(default empty) for the audio and subtitle rows. Applied where the pills are built,
`pipeline._make_badge_groups()`, via `overlay.order_pills_by_language()`: within each pill the
listed languages lead in list order, then the pills are ranked by the best-listed language they
carry; everything unlisted keeps today's order (a stable sort). `render_badge_groups()` is
untouched. **Order only** — a 40-seed property test checks every pill keeps exactly its languages.
 - **Byte-identical at the default.** In CI: renders with `prefer_languages=[]` equal renders from
   a verbatim copy of the pre-P7 builder over 5 tag sets × 3 layouts, and a reorder is shown to
   change the bytes (so "equal" can fail). Out of CI, once: 360 renders (60 random tag sets × 3
   sizes × 2 corners, 360 distinct digests) from a `git archive` of `origin/main` and from the
   branch hashed identical.
 - **Tags untouched; no re-tag.** It is poster-only and not in `_tag_config_hash()` (a test pins
   that). There is **no** separate overlay-config hash to add it to: like every other badge
   setting (colours, size, corners), a change reaches existing posters on the next **full** scan,
   because an incremental scan skips files whose mtime has not moved. The Settings hint says so.
 - **Backups.** `apply_overlay()` copies `.orig` only if absent and always renders from it; a test
   re-renders a synthetic poster in a new order and checks `.orig` is still the clean original and
   the new render was drawn on it, not over the old badges.
 - **Pick-list.** `GET /api/languages` (read-only) returns the codes present in the index with the
   number of items carrying each, most first; UND/empty excluded, codes as the badges spell them.
   It walks every row's track JSON, so it is cached until the latest scan run or the row count
   changes. The Settings control (Badge settings card) is a select of those codes plus ordered
   chips (◀ moves earlier, × removes), tooltip "reorders only — no pill is ever hidden". A saved
   code no longer in the index stays listed, marked as such, rather than silently dropped. The
   preview honours the setting. Verified in a throwaway container on invented data at 1366 and
   390 px: add → raise → preview URL → save → reload round-trips, no JS errors, no 4xx/5xx.

**P7 containment — SHIPPED 2026-10-06 ([#126](https://github.com/bpoulliot/xenotag/pull/126)); released in v1.11.0 (2026-10-07).** Decision (d), as decided.
`render_badge_groups()` no longer draws one row per group with a `…`:

 - **Wrap, up to 2 rows per group.** The unit is the badge (`overlay._atoms()`): a label's
   codec or format, never split (`TrueHD Atmos` and `DD+ Atmos` are one badge), or one of its
   language codes. A label that does not fit the space left on a row moves to the next row
   if it fits there whole. Otherwise it is split between badges, and its remaining languages
   continue as a pill on the next row. It is never split after its head alone once the row
   has other pills. Rows read top to bottom at every corner; B10's narrowed band row is the
   edge row.
 - **Counted `+N`.** What does not fit in the group's rows is counted in a `+N` pill of the
   group's colour, closing its last row. Badges trimmed off that row's tail to make room are
   added to N, so N is exactly the number of badges not drawn. The `…` pill and the in-label
   `…` cut are gone. Order is kept throughout, so `prefer_languages` decides what survives.
 - **Vertical clamp — the rule for what goes first.** The tag stack gets the rows between the
   margins. The rating's row is subtracted, except when the rating shares a band with a
   narrowed tag row (same edge, opposite side), so a tag never covers the rating. Each group
   gets one row, then wrap rows as space allows. Priority is group order — video, audio,
   subtitles — and the lowest-priority group gives first: **subtitles lose their wrap row
   first, then audio, then video.** On a canvas with fewer rows than groups, the
   lowest-priority group then loses its whole row and is counted as `+N` in its own colour
   on the last drawn group's row. If that row fills up, the count moves to the group before
   it. The one case left untraced is a canvas with no tag row at all: with a rating, wider
   than 4.41:1 at `tv_plus`, 5.08:1 at `tv` or 6.06:1 at `desktop` (width-independent, since
   row height scales with width). The widest shape in the grid is 2.39:1.

**Measured** (`scripts/measure_pill_containment.py`, 6,144 renders, the 2026-09-26 grid). The
probe now reads `+N` pills, counts hidden badges by the definition above (written independently
of the renderer's), and fails on three new things: a hidden badge no `+N` counts (or a `+N` that
over-counts), a `…`, or badges drawn out of label order. Its self-test fails both ways. The
overflow plant is now painted pill by pill, since the renderer can no longer overflow. Six
detector mutants each fail the self-test, and it passes against both renderers.

| | origin/main `2c57c39` | this change |
|---|---|---|
| margin / off-canvas / rating overlap | **192** (all 2.39:1, padding off, `tv_plus`; same as 2026-09-26) | **0** |
| any violation, the new probe | 4,656 (192 + 4,608 renders hiding without a count) | **0** |

Worst hidden badges over the 16 corner pairs, 2:3 at 1000 px, before → after (of 7 / 18 / 44 / 85):
p50 0 → 0 at every size; p99 9 → 0 / 11 → 4 / 12 → 7 (desktop / tv / tv_plus); p99.9
32 → 23 / 34 → 27 / 36 → 31; max 70 → 54 / 75 → 62 / 77 → 69. At its best corner pair p99 now
hides nothing at desktop or tv. The pairs that still hide are B10's narrowed band row, plus
`tv_plus`, where `--budget` already said p99's subtitles need 3 rows. p50 hides in 48 renders,
all 2.39:1 with padding off at `tv_plus`. Those are exactly the renders that ran off the canvas
before; they now drop the subtitle row and count it.

**Default posters do not move.** In a 132-render spread (the 4 README configs at 280×420 and
1000×1500 plus their preview JPEGs, and 120 random tag sets × sizes × corners × 300/600/1000 px
on 2:3), **all 65 renders where the old code hid nothing are byte-identical** (sha256 of the RGBA
bytes, max difference 0). All 63 that moved are ones where the old code hid something. In CI,
`tests/test_pill_containment.py` repeats the comparison over 80 random cases against a verbatim
copy of the pre-P7 renderer, and also checks that a hiding case does move. The scratch harness
`/tmp/xt-p7-dump.py` was not committed; the test is its re-runnable form.

 - **`assets/readme/` moved, legitimately.** `overlay-tv`, `overlay-tv-plus` and
   `overlay-stacked` were regenerated: the README's own example (`TrueHD Atmos EN` +
   `DTS-HD JA`) hid `DTS-HD JA` behind a `…`, and at `tv_plus` dropped it with no `…` at all
   (the old "exactly one badge fills the row" branch). It is now a second audio row.
   `overlay-desktop` did not move. B21's stored 100% references for those three configs
   (`tests/test_pill_composite.py`) were re-taken on the same grounds; the desktop rows are
   still `5476f24`'s.
 - **Census** (`--db`, a read-only copy of prod `state.db` + `-wal`/`-shm` taken 2026-10-06,
   10,643 rows, 1000×1500, default corners), old → new, same probe:

   | `badge_size` | items hiding anything | of which uncounted | violations | median / max hidden per item |
   |---|---|---|---|---|
   | `tv` | 128 (1.20%) → **101 (0.95%)** | 128 → **0** | 128 → **0** | 21 / 49 → 16 / 44 |
   | `desktop` (prod's) | 111 (1.04%) → **98 (0.92%)** | 111 → **0** | 111 → **0** | 20 / 47 → 12 / 39 |

   Labels dropped whole: 24 → 4 at `tv`, 10 → 4 at `desktop`. (2026-09-26's 136 / 116 were
   a 10,583-row index; the library has moved since.) Every item that still hides something is
   subtitles (one also audio), and all of it is counted. The census found one more thing. The
   first new-code run reported **1** desktop violation: a stale row whose subtitle label still
   holds legacy numeric "languages" (`10`, `11`, `12`, from file names). The probe re-parsed
   the continuation pill `AR 10 UK …` as the one head `AR 10`. The render was right (`+13` =
   the 13 badges not drawn), but the renderer's `+N` trim re-parsed pill text the same way and
   could have miscounted had a trim cut such a pill down to `AR 10`. Both now work from the
   badges themselves: pills carry theirs, and the probe matches each pill as a run of the
   label's badges. A test covers the shape with invented codes. The re-run reads 0.
 - `--db` failed on that copy with `no such column: media_state.field_order` (U5's column,
   released in v1.11.0). It now loads only the columns it reads.

**Consequence for the operator.** It is poster-visible only for the items that wrapped or
clipped, and it takes effect at the **next full scan**: there is no overlay hash, and an
incremental scan skips unchanged files. No tag, schema or config change. `.orig` handling is
untouched; `apply_overlay()` still renders from the backup.

**P7 containment — MEASURED 2026-09-26, relabelled NEEDS DECISION.** Probe
`scripts/measure_pill_containment.py` (`--self-test` in CI). It renders through the real
`render_badge_groups()`, with groups built by `pipeline._make_badge_groups()` from invented
`MediaInfo`s, on blank synthetic canvases (no library file, no poster), and reads each render
three ways: the rectangles from the `placed=` hook (as B10's test does), the text of every pill
(a read-side wrap of `_pill_tile`), and the pixels painted in exactly each group's fill colour. The
pixels must agree with the rectangles or the run is void: **0 disagreements in 6,144 renders.**
The self-test fails both ways: a lone `SD` pill passes; a planted 12-row stack on a 1000×562
canvas is reported crossing the margin, running off the canvas and covering the opposite-edge
rating, by both the rectangles and the pixels. A rectangle moved 300 px from its pill and a pill
missing from the hook are both caught.

*Inputs.* Tag sets shaped on a read-only copy of prod `state.db` (10,583 rows): distinct subtitle
languages per item p50 1 / p99 12 / p99.9 34 / max 52, audio p99.9 3 / max 21. Longest token is
`DVB_SUBTITLE`, longest rating `Rated Not Rated`. Built as **p50** (`1080p H.264`, `AAC EN`,
`SRT EN`, R), **p99** (`SRT` + 12 languages), **p99.9** (`HDR10`, `DD+` + 3, `PGS` + 35) and
**max** (`4K H.265 HDR10`, `TrueHD` + 20, `AAC` + 1, `PGS` + 52, `SRT` + 3, `DVB_SUBTITLE`,
`Rated Not Rated`). Grid: 2:3, 27:40, 16:9, 1:1 **and 2.39:1** (added: it is past the breakpoint
below) × widths 300/600/1000/2000 × all three `badge_size`s × all 16 badge/rating corner pairs.
`normalize_portrait` runs both off and on for the aspects it pads, and the canvas used is the one
`apply_overlay()` would hand the renderer (`_pad_to_portrait()`'s own output). 6,144 renders,
529 s.

*Violations (a pill crossing the margin, or a tag overlapping the rating or another row):*

| aspect | `normalize_portrait` | desktop | tv | tv_plus |
|---|---|---|---|---|
| 2:3, 27:40 | (never padded) | 0 / 512 | 0 / 512 | 0 / 512 |
| 16:9, 1:1 | off | 0 / 512 | 0 / 512 | 0 / 512 |
| 16:9, 1:1 | on (default) | 0 / 512 | 0 / 512 | 0 / 512 |
| 2.39:1 | on (default) | 0 / 256 | 0 / 256 | 0 / 256 |
| **2.39:1** | **off** | 0 / 256 | 0 / 256 | **192 / 256** |

(Each cell covers the 4 tag sets × 4 widths × 16 corner pairs for its aspects.) All 192 violating
renders are 2.39:1 with padding off at `tv_plus`, at **every** width and **every tag set, p50
included**: it is the fixed row count that overflows, not the tags. In the 4 same-corner pairs (64
renders) the stack climbs off the canvas, putting 2–3 pills past the edge; the worst case is 3,
for example p99.9 at 2000 px with tags and rating both top-right. In the 8 different-edge pairs
(128 renders) the tag stack covers the rating on the opposite edge. That is B10's
"not covered" case, and it is real here. The 4 same-edge, opposite-side pairs pass.

*What guarantees containment, and where it stops.*
 - **Horizontal: guaranteed, by hiding.** `_render_group()` draws each group as **one row**
   bounded by `max_row_w`. A label wider than the row is cut by `_truncate_label()` (`PGS EN JA…`),
   pills past the row are replaced by a `…` pill, and a single token wider than the row (the
   `_truncate_label()` fallback) is refused by the packer and becomes `…`. None of these can
   overflow, and all of them hide information.
 - **Vertical: nothing in the code bounds it.** It holds because the pipeline draws at most 3 tag
   rows plus 1 rating row, and row height scales with poster width. So containment depends on
   aspect alone. `--breakpoints` (1000 px wide, heaviest set, worst corner pair) gives the widest
   canvas that still fits as **3.12:1 at desktop, 2.60:1 at tv, 2.25:1 at tv_plus**.
   `normalize_portrait` (on by default, and on in prod) pads anything wider than 0.717:1 to 2:3,
   so at defaults every aspect is contained. The preview route always renders 280×420. With
   padding off, a target image wider than the breakpoint overflows. Prod runs `desktop` with
   padding on, so prod is not exposed.

*Hidden metadata: the part the P7+U8 direction rejects.* Tokens hidden out of tokens in the set,
worst corner pair. It does not depend on width or aspect: at 300 px and 2000 px the counts differ
by at most 1.

| tag set | desktop | tv | tv_plus | labels dropped whole |
|---|---|---|---|---|
| p50 | 0 / 8 | 0 / 8 | 0 / 8 | 0 |
| p99 | 9 / 19 | 11 / 19 | 12 / 19 | 0 |
| p99.9 | 32 / 45 | 34 / 45 | 36 / 45 | 1 at tv_plus |
| max | 70–71 / 87 | 75 / 87 | 77 / 87 | 3 (4 at tv_plus) |

Every p99 render hides something. The worst corner pairs are same-edge, opposite-side: B10's
narrowed row. The best pairs hide 4 / 6 / 8 of 19 at p99. **Production census** (`--db`, every row
of the copy rendered at 1000×1500): **0 containment violations.** At `tv`, **136 of 10,583 items
(1.29%) hide something**: 135 in subtitles and 2 in audio, 28 with a whole label dropped, a median
of 21 and a maximum of 47 tokens hidden per item. At prod's own layout (`desktop`, tags bottom-left, rating top-left, read by key name from prod `config.yml`): **116 items (1.10%)**, all in subtitles (1 also in audio), 12 with a whole label dropped, a median of 20 and a maximum of 45 tokens hidden. Each census took ~10 min.

*What showing everything would cost* (`--budget`, 1000×1500, default corners). This is arithmetic
on the renderer's own font metrics and layout parameters, **not a render**, because no such layout
exists. The columns are the rows today, the rows needed if each group wrapped onto further rows at
token granularity, the rows the whole poster can hold, and the font size one row per group would
need, against 56 / 72 / 88 px today:

| tag set | size | rows today | rows if wrapped | rows the poster holds | font for one row |
|---|---|---|---|---|---|
| p99 | desktop / tv / tv_plus | 4 | 5 / 5 / 6 | 19 / 16 / 13 | 41.7 / 41.8 / 41.9 px |
| p99.9 | desktop / tv / tv_plus | 4 | 7 / 8 / 10 | 19 / 16 / 13 | 14.9 px |
| max | desktop / tv / tv_plus | 4 | 12 / 15 / 18 | 19 / 16 / 13 | 8.7 px |

**Why NEEDS DECISION and not READY.** The margin is never crossed at defaults, so there is no
plain overflow to fix, except the padding-off 2.39:1 case. Honouring the direction's "nothing
hidden" is a trade that the code cannot make on its own. **Wrap** shows everything but covers the
poster: p99.9 at tv_plus takes 10 of the 13 rows the poster holds, and the max item at tv_plus
does not fit even edge to edge (18 > 13). **Shrink** keeps one row but needs 8.7–14.9 px text for
the heavy sets, which cannot be read. **Clamp** (today) is bounded but hides, and its `…` does not
say how much it hid. Every option then needs an **explicit vertical bound**: once rows vary, the
fixed-row-count guarantee above is gone.

**Options.** **(a)** Wrap, uncapped. **(b)** Shrink to fit, with no floor. **(c)** Keep one row
per group, but replace `…` with a **counted pill** (`+33`) so nothing disappears without a trace.
**(d)** Wrap each group up to a **row budget** (e.g. 2 rows), then a counted `+N` pill. **All of
them** clamp the stack to the canvas height, which fixes the 2.39:1 case: when the rows do not fit,
fewer rows go to wrapping; the rating is never covered.

**Recommendation: (d), with a 2-row budget.** It shows every pill for most of the 1.29% (p99 needs
1–2 extra rows), stays bounded on a heavy item, and turns silent hiding into a stated count. It
also puts the `prefer_languages` ordering (shipped 2026-09-27) to work, because the preferred languages are then
the ones that survive. The build's acceptance test is this probe's grid: zero violations, including
2.39:1 with padding off. Any count left hidden must appear in a `+N` pill; today's `…` pill must
not.

**OPERATOR DECISION 2026-10-05:** (d), 2-row budget — wrap each group up to 2 rows, then a counted
`+N` pill; clamp the stack to the canvas height; today's `…` pill goes. Relabelled **READY**.

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

**P8 — SHIPPED 2026-09-23**, on the PNG path. The source artwork lives in `assets/brand/`
(`mark.png`, `wordmark.png`) and every raster under `app/static/` is now generated from it by
**`scripts/generate_brand_assets.py`** (`--check` verifies without writing, and CI runs it, so a
hand-edited static file that drifts from its source fails the build).

Shipped: `logo-mark.png` 512 and `logo-wordmark.png` 960×160, both transparent; `favicon.png`
256, `favicon.ico` 16/32/48, `apple-touch-icon.png` 180 and `icon-192`/`icon-512`, all on a
Charcoal tile. Added `site.webmanifest`, `theme-color`, and an `application/manifest+json` MIME
registration in `main.py` because `.webmanifest` is not in Python's default table. All the traps
below are closed: `VERSION` bumped to 1.5.1 for the `?v=` cache-bust, the logo carries alpha, and
the circular crop is gone from both templates.

**The naming and the header layout follow van1sh's brand setup**, which the operator pointed at:
`-mark` is the glyph alone, `-wordmark` the full lockup, and a `.brand` block pairs the art with
a `<small>` tagline. **One thing does not transfer, and it is the interesting one.** van1sh sets
its wordmark as live text with a gradient (`background-clip:text`, the word literally fading
out — a pun on the name) beside a separate mark. **Xenotag's wordmark embeds the mark as its
`O`**, so showing both prints the glyph twice — which is exactly what this header used to do.

So the header shows **exactly one of the two**, chosen by width: a `<picture>` serves the
wordmark normally and the bare mark at ≤600px, and only the chosen file is fetched. The `<h1>`
survives as `sr-only` so the document keeps its heading while the art carries the name visually.
Login does the same, wordmark-only and centred. Setting the wordmark as *text* is not available
here without a font — if [P11] ever produces one, that changes.

*(original filing follows)*


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

**P9 — SHIPPED 2026-09-24.** The palette tokens now live in **`app/static/theme.css`**, linked from
both templates. `login.html` used to carry its own copy of the token block, which had already
drifted, so there is now exactly one place the UI palette is defined. The mapping is the one
proposed below, plus four tokens the sweep turned out to need: `--inset` (`#121614`, the wells
behind inputs, the log and the YAML editor, which were `#040c1c`/`#030a16` literals), `--hover`,
`--on-accent`, and `--danger-bg`/`--danger-hi`.

**Every pairing the UI actually draws was measured, not just the ones proposed below.** The weakest
is the accent on Deep Forest at 5.07:1 (AA). Text and muted text clear AAA everywhere except muted
on Deep Forest (5.81, AA). Status green, yellow and red were deliberately left un-tinted and still
clear AA on Charcoal.

What the sweep found beyond a colour swap:

 - **The accent is a light colour, so nothing may draw white on it.** `.size-btn.active` did
   (white on the new sage would be ~2.4:1), and so would the primary buttons once they became solid
   sage. Everything drawn on the accent now takes `--on-accent` (Charcoal, 7.18:1). The toggle knob
   had the same problem in reverse: Bone on the sage "on" track is 1.78:1, so the knob now goes dark
   when a toggle is on.
 - **Text inputs were split by page.** Settings used a near-black well and the Preview page used
   `--badge-bg`. Under the old navy theme the two looked alike; under the brand it is black against
   green. Every text-entry control is now an `--inset` well. Buttons and chips keep `--badge-bg`.
 - **A third, stale badge palette was hardcoded in the template** — `#1a7a6e #6b3a9e #a86200
   #2d2d2d`, the live deployment's old custom colours, used as preview and form fallbacks. B4's fix
   only searched for the old *defaults* and missed these. They now read `ImageConfig` like the rest.
 - The per-category labels on the colour pickers were tinted in the old palette's hues. Palette 2's
   hues are fill colours too dark to read as text on Charcoal, and the swatch beside each label
   already shows the colour, so the labels are now plain text.

`index.html` went from 46 distinct hex literals to 11, each deliberate: badge text and contrast maths
(badge data), the dual/multi chip tints (status), the 4K/720p chips (categorical), and the
`theme-color` meta, which cannot read a CSS variable. `theme-color` and the manifest are Charcoal.

**Verified in a real browser, not just by token audit:** the app was run against a scratch config
and screenshotted with headless Chromium (login, dashboard, preview, settings, and 390px), with no
JS errors and no 4xx/5xx responses. A token audit confirms every `var(--x)` the templates use is
defined, and none are defined but unused.

**Not fixed here, and not caused by it:** at 390px the header's nav runs off the right edge. The
pre-P8 header was wider, so this predates P9. It belongs to [P4] (mobile breakpoints).
**Fixed by P4b ([#128](https://github.com/bpoulliot/xenotag/pull/128), 2026-10-06):** the DOM audit
no longer flags it at 360 or 390 — P9's last open defect is closed.

*(original filing follows)*

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

**SHIPPED 2026-09-24 — palette B, defaults plus migration.** The operator: *"I like option b,
defaults plus migration."*

| category | old | new | opaque vs white |
|---|---|---|---:|
| video | `#134e4a` | `#203a30` Deep Forest | 12.3:1 |
| audio | `#1e3a8a` | `#312c4c` | 13.2:1 |
| sub | `#7c2d12` | `#50532f` | 8.0:1 |
| rating | `#4c1d95` | `#73485b` | 7.5:1 |

**The migration is one-shot, and that is the whole design.** A validator keyed only on "value
equals the old default" re-fires on every load, so an operator who *later* picks the old navy on
purpose would have it swapped back on each restart, silently and forever. So `ImageConfig` gains
`badge_palette_version` (default `2`). A config with no version is palette 1: each colour still
**exactly equal to its own field's** old default moves to the new one, anything else is left
alone, and the version is set to 2. It persists on the next save, and from then on nothing is
touched. The Settings UI spreads the loaded `image` dict back into both of its PUT bodies, so the
marker survives UI saves without any change to the frontend.

Three consequences worth knowing:

 - **Posters do not repaint on their own.** Nothing hashes image settings the way
   `_tag_config_hash()` hashes tag settings, so existing overlays keep their old colours until the
   next **full** scan re-renders them. This was already true of any colour change made in Settings;
   the migration inherits it rather than causing it. A fix would be an image-config hash that
   forces a re-render, which is a separate item if wanted.
 - **The opacity floors moved, and there is now little room below 1.0.** Separation was bought
   partly with lightness, which put `rating` at 7.48:1 — just over AAA. Re-measured: **0.98 is the
   AAA floor and 0.80 the AA floor** (they were 0.89 and 0.73). Palette A had the same ~7.5 worst
   case, so this is the price of colour-blind separation, not of B specifically. `config.py` and the
   README quote the new floors. *(Instrument figures; on the poster they hold only since [B21], 2026-10-06.)*
 - **The preview route had to opt out.** It builds `ImageConfig` from query parameters, which to
   the migration looks exactly like an unversioned legacy config, so previewing the old navy would
   have rendered indigo. It now passes the current version explicitly. A test pins this, and
   removing the one line fails it.

Also closed from the trap list below: the README's nonexistent `#1e3a5f` defaults are replaced
with the real ones, `config.example.yml` ships palette 2 with its version, and the template and
preview route now read their defaults from `ImageConfig` instead of repeating hex literals — which
is how the README drifted in the first place. `tests/test_badge_contrast.py` still uses `#134e4a`,
correctly: there it is an arbitrary fill for cache tests, not a claim about the defaults.

Guarded by `tests/test_badge_palette.py` (14 tests), mutation-checked: dropping the preview
route's version fails 1, and a naive every-load migration fails 3.

*(decision record follows)*

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
   badge and no warning. *(B2 FIXED 2026-09-25: Sage as a fill now shows 3.92:1 ✗ AA beside the
   picker.)*
 - **[P6] is unaffected but should be told.** A background-aware palette needs a *pair* of
   palettes; if P10 lands monochrome, P6 has one fewer degree of freedom to work with.

**P11 — CLOSED 2026-09-25. The operator: *"Option 2, just use the pngs."*** `assets/brand/mark.png`
and `wordmark.png` stay the source of truth for every shipped asset; nothing changes in the app.

**Why a vector cannot clear the bar, recorded so nobody retries the same route.** The concept art is
a *rendered* image, not a drawing: each shape sits inside a soft, uneven ~14px glow, and the
design's actual edge is a thin bevel line drawn inside that glow. A tracer
(`alpha-128 contour + Schneider Bezier fit + least-squares gradients`) and an independent verifier
(cairosvg render on the source's pixel grid) were built on 2026-09-25. The trace **passed every
numeric threshold** — IoU 0.997, mean boundary 0.12px, dE00 mean 1.03 (mark) / 0.62 (wordmark) —
and was **visibly not exact**: wavy edges, a blobby N inner corner, a notched arc end, no bevel rim.
It sat within 1.00px of the alpha contour everywhere, corners included, which is exactly why the
shape metrics could not see the problem: **they measured fidelity to the glow, and the glow's 50%
contour is irregular.** Any future verifier for "exact" must measure the visible colour/bevel edge.

The only routes to an exact vector are a designer's hand trace or vectors exported from whatever
produced the concept. **What option 2 gives up:** the single SVG favicon that stays sharp at every
size; the fixed 16/32/48 ICO set stays. The tracer and verifier live on the local branch
`feat/brand-vectors-p11`, unpushed; `assets/brand/vectors-wip/` is kept for reference only.

*(original filing follows)*

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

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| U5 | Extended ffprobe tags: video profile, bitrate tier, interlacing, frame rate | 4 | 3 | **SHIPPED 2026-09-27** — interlacing only (`xt-interlaced`), as decided · **RELEASED v1.11.0** (2026-10-07) — its first scan was the full re-tag | [#24](https://github.com/bpoulliot/xenotag/issues/24) |
| U6 | Extended metadata tags from Jellyfin/\*arr: genres, original language, runtime bands, series status, ratings, custom formats | 4 | 5 | **CLOSED 2026-09-26** | [#25](https://github.com/bpoulliot/xenotag/issues/25) |

### P — Polish

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| P4 | Mobile-responsive UI: full breakpoint coverage | 3 | 2 | **SPLIT:** P4a **SHIPPED 2026-10-06** ([#120](https://github.com/bpoulliot/xenotag/pull/120)) · **RELEASED v1.11.0** (2026-10-07) · P4b header **SHIPPED 2026-10-06** ([#128](https://github.com/bpoulliot/xenotag/pull/128)) · **RELEASED v1.11.0** (2026-10-07), media browser + `.health-grid` truncation: **audit after P6 + B12(b) merge, then the operator picks a layout** (decided 2026-10-09, round 5) — measured 2026-09-26 | [#26](https://github.com/bpoulliot/xenotag/issues/26) |
| P5 | README sample screenshots and overlay examples | 2 | 1 | **SHIPPED 2026-09-26** | [#23](https://github.com/bpoulliot/xenotag/issues/23) |

### I — Infrastructure

| ID | Feature | Value | Complexity | Readiness | Issue |
|----|---------|:-----:|:----------:|-----------|-------|
| I7 | pillow-simd acceleration (measured 2026-09-26: overlay ≈ 8–11% of full-scan wall, pillow-simd could save ≤ ~3%; recommend close) | 2 | 3 | **CLOSED (decided 2026-10-05)** | [#20](https://github.com/bpoulliot/xenotag/issues/20) |

> **Renumbered 2026-09-22:** this was a second `I6`, colliding with the ntfy item in Near-term.
> Referenced as `I6` in anything predating this date, it means whichever of the two fits context.

**Sweep 2026-09-26 — far-term items, labelled for the first time.** They stay far-term; the
labels say what would make each one startable.

 - **U5 — NEEDS DECISION → BLOCKED on B9, decided 2026-09-26 (below).** Four new tag families, each a vocabulary choice: which of profile /
   bitrate tier / interlacing / frame rate, and in what spelling. Three constraints the decision
   inherits: every new tag must be legal in Radarr's `[a-z0-9-]` (B9 — `xt-23.976` is not),
   the tag-config hash makes each addition a full re-tag, and the P7+U8 direction means a new
   family is never hidden later for being rare, so add only what the operator would query.
   **Question:** which families, if any? **Recommendation:** interlacing only (`xt-interlaced`,
   present or absent) — it is the one that changes what the operator does with a file; the
   others are browsable in Jellyfin's own media info already. Decide after B9, whose spelling
   rule it must follow.

   **OPERATOR DECISION 2026-09-26:** interlacing only (`xt-interlaced`, present/absent), after B9.
   ~~**BLOCKED on B9.**~~ **READY** since B9 was FIXED 2026-09-27; `xt-interlaced` is already legal
   under B9's spelling rule (`tagger.tag_label()`), which any new family must go through.

   **U5 — SHIPPED 2026-09-27; released in v1.11.0 (2026-10-07). Interlacing only; the other three families
   were not built.**
   - **Rule.** `scanner._detect_field_order()` reads ffprobe's `field_order` from the first video
     stream (the one the codec comes from). `tt`/`bb`/`tb`/`bt` add `xt-interlaced`; `progressive`
     adds nothing; a stream with no `field_order` is stored as `unknown` and adds nothing — no
     guess either way. No video stream stores NULL. The label is already legal in Radarr (B9;
     it is in `tests/test_tag_vocabulary.py`'s vocabulary now). It rides `tags.destinations.video`,
     like resolution/codec/HDR, so no new config key. **Tags only:** no badge — the video pill row
     is resolution/codec/HDR and stays that way (`test_no_badge_is_drawn_for_it`).
   - **Index.** New nullable column `media_state.field_order`, **Alembic revision 0002** — the
     first revision after the baseline; additive (a plain `ALTER TABLE … ADD COLUMN` under batch
     mode). The \*arr index dry run builds tags from the row with it, so the dry run and the scan
     agree (`test_index_dry_run_and_scan_agree_on_xt_interlaced`). A row written before 0002 reads
     NULL and plans no `xt-interlaced`, without error.
   - **Re-tag.** `TAG_VOCABULARY` 3 → 4, so the first scan after the upgrade is a full re-tag.
     Checked that it **re-probes**: a full scan skips the mtime check, so every reachable item is
     probed and its row gets `field_order` (`test_the_first_scan_after_the_upgrade_re_probes_an_unchanged_file`;
     the scan after that is incremental again and reuses the row). Items that fail to probe keep
     NULL. Default-config hash `d0c577fe5620e689` → `ed8a1890a06dc045`; production's own hash
     (`811c4c22ae05ed4a`, arr live) changes too, which is the point.
   - **Migration, measured on a copy of production `state.db`** (2026-09-27 08:37 MDT, at
     revision 0001): `scripts/verify_state_db_upgrade.py --db` → `upgraded` 0001 → 0002, 10,646
     rows (`media_state` 10,585, `scan_runs` 50, `scan_errors` 10, `app_meta` 1), **0** differing
     in any pre-existing column, `field_order` NULL on all of them, second start `current`. The
     **released** image (`:latest`, v1.10.0) run against the upgraded copy logs "revision 0002,
     which this xenotag image does not know … Starting without migrating", starts, and its writes
     leave `field_order` NULL — so rolling back the image needs no database restore.
     The verifier itself had to change: it compared `.dump` lines byte for byte (an added column
     changes every line) and built its "pre-I3" fixture from today's models. It now builds the
     fixture from the baseline revision, accepts a source at an older revision, compares every
     pre-existing column by `quote()` (what `.dump` writes), and fails if an added column holds a
     value; the self-test plants one and sees it.
   - **How often `unknown` occurs — common.** `scripts/measure_field_order.py`, run in a throwaway
     container of the released image (ffprobe **7.1.5**, the one production scans with), media
     mounted read-only, no network; every answer cross-checked against a separate plain `ffprobe`
     call (0 disagreements), self-test on synthetic progressive/top/bottom-field files. Sample:
     paths from the production index copy, seed 20260927 — 400 at random (47 paths no longer
     exist on disk — stale index rows, deleted or replaced files; not counted) and 200 from the
     SD / 480p / MPEG-2 / VC-1 / MPEG-4 stratum (9 gone).

     | stratum | probed | progressive | unknown | interlaced |
     |---|---:|---:|---:|---:|
     | random | 353 | 226 | **126 (35.7 %)** | 1 (`tt`, SD H.264) |
     | legacy SD stratum | 191 | 149 | 38 (19.9 %) | 4 (MPEG-2 `tt` ×2, `bb` ×1; H.264 `tt` ×1) |

     By codec (random): AV1 76 of 132 unknown, H.265 47 of 72, MPEG-4 3 of 3, H.264 0 of 146.
     Dev fixtures (`~/.mf-dev/media`, 6 files, host ffprobe 6.1.1): 6 progressive.
     **What `unknown` is:** 15 of the unknowns (H.265 6, MPEG-4 4, AV1 3, VP9 2) re-probed with
     `-probesize 200M -analyzeduration 200M` still declare no `field_order`, and none of their
     first 50 decoded frames has `interlaced_frame=1` — so it is the stream not saying, not the
     probe being too small. AV1 cannot code interlaced video at all. Consequence, recorded not
     acted on: `xt-interlaced` is a **lower bound** — an interlaced file whose stream declares no
     field order (plausible for old MPEG-4/AVI) is missed. The frame-level flag is what a later
     rule could use; nothing reads it now, and there is no item for it, because with no
     `xt-progressive` an `unknown` and a `progressive` file tag identically.
   - **Not measured:** how many production items get the tag. The sample says roughly 0.3 % of
     the library at random; the real count arrives with the release's full re-tag and can be read
     back by `Ids=` (never the recursive listing — B18).
 - **U6 — NEEDS DECISION → CLOSED 2026-09-26 (below).** Complexity 5 because it is seven unrelated features; "ratings" left
   it with U7 (already shipped as the certification). Most of the rest is data Jellyfin already
   holds and indexes (genres, original language, series status), so re-emitting it as `xt-`
   tags duplicates a query surface — the same reasoning that dropped U9's query half.
   **Question:** keep U6? **Recommendation:** close it, and re-file any single source the
   operator actually wants (runtime bands and \*arr custom formats are the two Jellyfin cannot
   answer) as its own small item.

   **OPERATOR DECISION 2026-09-26:** close; re-file any single source the operator wants as its own item.
   **CLOSED.**
 - **P4 — NEEDS MEASUREMENT.** One defect is known (P9, 2026-09-24: at 390 px the header nav runs
   off the right edge); "full breakpoint coverage" is otherwise unmeasured. **What:** headless
   Chromium screenshots of login, dashboard, media browser, preview and settings at 360 / 390 /
   768 / 1024 / 1366 px from a throwaway container on a scratch config (P9's recipe), listing
   every overflow, clipped control and unreadable table. **Roughly 1 h**, no code. The fix list
   it produces is then specced — and per the UI rule, any layout change that is not a plain
   overflow fix goes to the operator first.

   **Measured 2026-09-26 — the label is now SPLIT: P4a READY (one plain overflow fix) and P4b
   NEEDS DECISION (the header and the phone-width tables change layout).** Instrument: headless
   Chromium (Playwright 1.58.0) at 360 / 390 / 768 / 1024 / 1366 × 800 against a throwaway
   container built from `origin/main` `331dba5`, on a Docker `--internal` network (no path to
   any real service; placeholder `*.example` URLs; one Sonarr + one Radarr instance so Settings
   shows instance cards), scratch `/config`, `SECURE_COOKIES=false`, and a **synthetic**
   `state.db` (60 media rows with long invented paths and 1–8 `xt-` tags, 6 scan runs, 4 scan
   errors). Per page × width, from the DOM: `scrollWidth` vs `innerWidth`; every visible element
   whose box leaves the viewport and is not inside a horizontal scroll/clip box (root offenders
   only); every `overflow-x:auto|scroll` box that actually scrolls; every control outside its
   viewport or clip box; every control whose centre `elementFromPoint` hits something else.
   **Self-test, both directions:** a synthetic 390 px page with a 600 px div, an off-screen
   button and a 900 px table in a scroll box is flagged on all three counts, and a 100 px page
   comes back empty; on the app, the known P9 header defect is flagged at 390 px, and all four
   pages at 1366 px have no overflow and no clipped control. No JS errors at any width.

   | Page | 360 | 390 | 768 | 1024 | 1366 |
   |---|---|---|---|---|---|
   | Login | clean | clean | clean | clean | clean |
   | Dashboard (incl. media browser) | **508 px wide** (header + all 5 cards) | **508 px wide** (header + all 5 cards) | clean | clean | clean |
   | Preview | 397 px (header only) | 397 px (header only) | clean | clean | clean |
   | Settings | 397 px (header only) | 397 px (header only) | clean | clean | clean |

   What overflows, and why:

   1. **Header (every signed-in page, 360 and 390).** The nav fits (66–347 px); **Sign out**
      sits at 357–397, off the edge by 7 px at 390 and 37 px at 360, and wraps to two lines.
      The fixed parts do not fit one row at phone width: ~24 padding + logo + the nav's 32 px
      `margin-left` + 281 px of nav + Sign out ≈ 460 px. This is the P9 defect; it is the
      *logout* button that leaves the viewport, not the nav buttons.
   2. **Dashboard grid (360 and 390).** Every card is forced to **484 px** and the page to
      508 px, because `.grid` items keep `min-width:auto`: the one-column grid takes the widest
      card's min-content (the media browser's table, 484 px; scan history 463). The tables'
      `overflow-x:auto` wrappers therefore **never scroll at any width** — measured scrollWidth ==
      clientWidth everywhere. Clipped by it at 360/390: **Clear all** (scan errors, 425–491 px) and
      the **Language** filter (247–445 px), reachable only by scrolling the whole page sideways.
   3. **Media browser readability (360 and 390, not an overflow).** Squeezed to min-content, the
      tag chips break at every hyphen (`xt-` / `lang-` / `ja`): **median row 188 px, tallest
      393 px, 50 rows = 10,616 px** of table, vs 60 / 1,909 px at 1366. Scan-error paths break
      per character (`word-break:break-all`, deliberate).

   **Falsified:** 3–5 Settings controls per width first read as covered, by the sticky save bar.
   Re-tested with each of the 53 controls scrolled to the viewport centre: **0 covered at every
   width.** It is where the bar sits at a given scroll offset, not an unreachable control.

   **P4a — READY: the plain overflow fix.** `.grid > * { min-width: 0 }` (or
   `grid-template-columns: minmax(0,1fr) …`). Measured by injecting it into the throwaway page:
   dashboard `scrollWidth` 508 → 360 / 390, zero overflowing elements, zero clipped controls,
   and the three tables now scroll inside their cards (media 450 px in a 278 / 308 px box, scan
   history 429, errors 361); 768+ unchanged. It fixes items 2 and the clipping, **not** item 1.
   Acceptance: the same DOM audit, clean on dashboard at 360 and 390, unchanged at 768–1366.

   **P4a — SHIPPED 2026-10-06 ([#120](https://github.com/bpoulliot/xenotag/pull/120); merged, not
   released).** `.grid>*{min-width:0}`, chosen over `minmax(0,1fr)` because one rule covers both
   track lists (two columns, and one at ≤ 800 px). Re-measured with a rebuilt instrument (Playwright
   1.58.0 / Chromium 1208, `--internal` network, synthetic `state.db`; scripts and screenshots in
   `~/docker/xenotag/p4a-audit-20261006/`, outside the repo — its self-test needs Playwright, which
   CI does not install). The self-test passed both ways, and P9 is still flagged at 390. Dashboard
   at 360 / 390: before, 534 px wide, 5 cards offending, **Clear all** and **Language** clipped;
   after, **360 / 390 with the header hidden**, 0 offenders, 0 clipped, and all three tables scroll
   inside their cards (429 / 382–401 / 476 px in 278 / 308 px boxes). 768 / 1024 / 1366: full-page
   screenshots are **pixel-identical** to `main`. **Correction to the acceptance above:** with the
   header shown, the dashboard is 397 px at 360 and 390, the same as Preview and Settings. That is
   P9's Sign out, which P4a does not fix, so "`scrollWidth` 508 → 360 / 390" can only hold with
   the header excluded. Seen in the 390 screenshot, not measured: the Service health tiles
   (`.health-grid`, `repeat(3,1fr)`) truncate names (`Son…`) and "Unreachable" runs a few px past
   the third tile. That belongs to the deferred phone-width UI pass, not to P4a.

   **P4b — NEEDS DECISION: the header, and the media browser at phone width.** Both change
   what the page looks like, so per the UI rule they are the operator's.
   - *Header.* (a) `header{flex-wrap:wrap}` alone — no overflow, but measured **3 rows
     (~127 px)** at 390, because the brand has zero width and the nav's 32 px margin pushes it
     down too. (b) wrap plus `.nav{margin-left:8px}` below 600 px — **2 rows (91 px) at 390**,
     still 3 rows (127 px) at 360; Sign out gets its own row. (c) move Sign out into the nav row
     as an icon button below 600 px — one row, but a new control shape. (d) collapse nav + Sign
     out into a menu — biggest change, three items do not need it. **Recommendation: (b)** — a
     wrap, no new control, and one extra row on the widths that need it; revisit (c) if the
     360 px third row bothers the operator in use.
   - *Media browser at ≤ 600 px.* After P4a it scrolls sideways inside its card, but rows stay
     very tall. (a) leave it — sideways scroll is the honest table. (b) `white-space:nowrap` on
     tag chips — rows shrink, the table gets wider and scrolls further. (c) cards instead of a
     table below 600 px. (d) hide Source / Audio / Scanned below 600 px. **Recommendation: (a)
     now**, and decide (b)–(d) in the post-feature UI pass — a tagger's operator browses this
     table at a desk, and the phone case is checking a scan, which the Overview card covers.

   **OPERATOR DECISION 2026-10-05:** header **(b)** — wrap plus `.nav{margin-left:8px}` below
   600 px. Media browser **(a)** — leave it; decide (b)–(d) in the post-feature UI pass. Header
   half relabelled **READY**; the media-browser half stays open, deferred, not NEEDS DECISION.

   **P4b (header) — SHIPPED 2026-10-06 ([#128](https://github.com/bpoulliot/xenotag/pull/128);
   released in v1.11.0, 2026-10-07).** `header{flex-wrap:wrap}` plus `@media(max-width:600px){.nav{margin-left:8px}}`.
   The media rule must come **after** the base `.nav` rule: same specificity, so source order wins;
   placed in the existing 600 px block above it, it lost to `margin-left:32px` and gave 3 rows at
   390 — the audit caught it. Re-measured with P4a's instrument plus a header-row probe (scripts,
   results and before/after screenshots at 360 / 390 / 768 in `~/docker/xenotag/p4b-audit-20261006/`,
   outside the repo; `--internal` network, synthetic `state.db`). Self-test passed both ways; on
   `main` (`a8b15ec`) the P9 defect is flagged at 390, with this change it is not. **390: 2 rows,
   93 px**, Sign out alone on row 2; **360: 3 rows, 131 px** (accepted); every page at 360 / 390 is
   exactly viewport-wide with 0 offenders, 0 clipped, 0 covered controls (was 397 px). 768 / 1024 /
   1366: full-page screenshots of all three pages **pixel-identical** to `main`. Side effect, seen
   not intended: on `main` at ≤ 600 px the flex row shrank `.brand` to 0 px wide (the mark still
   painted, overflowing it); with wrap it keeps its 28 px. **P9's header defect is closed by this.**
   Still open, as before: the media browser at phone width (deferred) and the `.health-grid` tile
   truncation noted under P4a.

   Not determined: real library data (longest titles, most tags) — the synthetic rows were made
   long on purpose but are not a sample; Preview with a reachable Jellyfin (its sample posters
   come from Jellyfin — here the synthetic backgrounds were used); touch-target sizes; heights
   other than 800 px; browsers other than Chromium. Screenshots were kept out of the repo.
 - **P5 — SHIPPED 2026-09-26** (`scripts/generate_readme_images.py`; CI runs its `--check`). README gains (1) overlay examples rendered with `generate_preview_bytes()`
   over the **synthetic** backgrounds in `app/preview_samples.py` — never real posters, which are
   copyrighted art and this repo is public — at the shipped defaults, one per `badge_size`, and
   one with the rating and tags in the same corner (B10's stacking); (2) UI screenshots from a
   throwaway container on a scratch config with no real library data. Images go under
   `assets/readme/`, generated by a committed script so they can be regenerated when the
   palette or layout changes. No live step.
 - **I7 — NEEDS MEASUREMENT.** The row already says "marginal gain; ffprobe is the bottleneck"
   without a number behind it. **What:** the share of a full scan's wall time spent in
   `apply_overlay()`, from a profiled full scan on the **dev** stack (or timing the overlay of a
   few hundred copied posters against the probe time of the same items). **Roughly 1 h.** If
   the overlay is under ~5% of scan time, close I7; pillow-simd is also a build-from-source
   fork, which is a supply-chain cost the gain would have to pay for.
   **Measured 2026-09-26 → NEEDS DECISION** (below).

**I7 measured 2026-09-26 — the overlay is NOT under 5%, but pillow-simd still is not worth it.**
Probe: `scripts/measure_overlay_share.py` (`--self-test` covers the timer, the percentile against a
naive reference, overlay-changes-the-copy / never-the-source, a badge-less item is neither timed
nor written, and the media-root guard, each in both directions; it is **not** a CI step). Method
(b), copied posters, over the dev stack: the dev library is a handful of items at synthetic
poster sizes, while prod's posters are the distribution that matters. Run in a throwaway
container of the prod image (`ghcr.io/bpoulliot/xenotag:latest`, v1.7.0: its ffprobe 7.1.5 and
Pillow 12.3.0, with prod's `--cpus 4 --memory 2g`, `--network none`, and every media root
mounted `:ro`) with origin/main's `app/` on the path, prod's `config.yml` read-only (desktop
badges, `normalize_portrait`), and a copy of prod's `state.db`. Each poster's `.orig` was
copied to `/tmp` and the copy overlaid with the real `apply_overlay()`. `probe_file()` was
imported and run read-only on the item's own media file.

- **Sample:** 300 rows, seeded (`--seed 7`), out of the 10,533 rows with an `image_path`. 268
  were measured. 21 had no poster on disk and 11 failed to probe because the file was gone:
  stale rows. Posters: 72 at 2000×3000, 66 at 1000×1500, 57 at 680×1000, the rest smaller or
  odd sizes. `perf_counter` timing, one pass per item.
- **Overlay** (open `.orig`, RGBA, render, RGB, JPEG save): median **20.9 ms**, p95 **105.5 ms**,
  mean 40.3 ms. **Probe** (serial, first read): median **97.1 ms**, p95 **177.9 ms**, mean
  118.3 ms, max 1.07 s. **Overlay share of overlay + probe:** median **20.4%** per item, p95
  56.6%, and 25.4% of the summed totals.
- **Where the overlay time goes.** Timings are the median of 15 runs on a synthetic noisy JPEG,
  same image and limits. At 2000×3000: decode 37.0 ms, → RGBA 8.7, render 41.0, → RGB 6.6,
  encode 19.1, total 112 ms. The real 2000×3000 posters took 116–131 ms. At 1000×1500 the total
  is 20.5 ms, and at 680×1000 it is 9.2 ms. JPEG decode and encode are already libjpeg-turbo (3.1.4.1, in the Pillow
  wheel), and pillow-simd's SIMD work is not in the codec. What pillow-simd could speed up is the two conversions plus the render
  (its alpha composite): about 50% of the overlay at 2000×3000 and about 36% at 1000×1500.
- **The share of what a scan actually spends.** A scan probes on a pool of `scan.max_workers`
  (4 in prod) and runs `_process_one_item()`, overlay included, **serially** on the consuming
  thread. The probe side therefore costs about 118 / 4 ≈ **30 ms/item** of throughput, and
  13.5 ms/item on a warm second pass through a real 4-worker pool. Prod's last two full scans
  took 55.4 min for 9,407 items (**353 ms/item**, 2026-09-24) and 75.8 min for 9,404 items
  (**484 ms/item**, 2026-09-23). That fits the note that full scans take about 1 h. So the
  overlay's 40 ms mean is **≈ 8–11% of full-scan wall time**, and **the roadmap row's "ffprobe
  is the bottleneck" is false**. The critical path is the serial consumer. About 310–440
  ms/item of it is neither the probe nor the overlay: the Jellyfin tag write, `refresh_item()`,
  the \*arr sync and the upsert. Its split was not measured.
- **Gain bound.** Even a 2× speedup on every step pillow-simd could touch saves ≤ 25% of the
  overlay: ≈ 10 ms/item mean, **≤ ~3% of a full scan** (≈ 1.5–2 min of 55–76). Incremental
  scans overlay 4–16 items (≈ 1 min each), so they gain nothing measurable.
- **Machine load at the time:** load average 43 / 38 / 36 on 24 threads, I/O pressure
  `some avg10` ≈ 33%, and Tdarr at ~800% CPU. Serial probe times are load-sensitive (see the
  Jellyfin ffprobe-load notes). The overlay is CPU-bound inside a 4-CPU limit.
- **Could not determine:** (1) pillow-simd's actual speedup, because it was deliberately not
  installed anywhere, so the gain above is a bound, not a measurement. (2) How much of each
  first probe read was already in the page cache: dropping caches needs root. (3) The overlay's
  I/O against the real media disks: the copies were on `/tmp`, and a scan reads `.orig` from and
  writes the poster to `/mnt/media`, so the true overlay time is ≥ the figure above. (4) The
  split of the ~310–440 ms of non-overlay consumer time.

**Decision for the operator:** adopt pillow-simd, or close I7. **Recommendation: close.** The
overlay is above the sweep's 5% threshold, but at most ~3% of a scan's wall time is reachable
by SIMD, and nightly incremental scans gain nothing. The cost: a build-from-source fork replaces
the `Pillow` pin, the build stage gains a compiler and image-library headers, and security
releases wait on the fork tracking upstream. It would also risk P5's byte-identical
`generate_readme_images.py --check` in CI, which was measured identical across *upstream*
Pillow builds only. If full-scan time ever matters, the lever is the serial consumer:
moving the overlay off it, or finding out what the other ~310–440 ms/item is. That is not filed,
because no decision waits on it today.

**OPERATOR DECISION 2026-10-05:** close — pillow-simd is not worth it, as recommended. Relabelled
**CLOSED**. Closing GitHub #20 to match is the operator's own step, not done here.

**GitHub issues with no roadmap item** (found by the sweep, not assigned IDs here): **#12**
*Subtitle cleanup* — deletes subtitle files/streams not on a keep-list, a destructive write to
the library; **#13** *Jellyfin plugin* — a sidebar iframe, a separate C# repo. Both predate the
rename. **Question for the operator:** track them (and under which ID), or close them on
GitHub? **Recommendation:** close both — #12 cuts against this project's "xenotag does not
destroy library content" rule and belongs with the encode pipeline if anywhere, and #13 is a
second product. Also stale on GitHub: **#19 (I4) and #22 (U3) are still open** though both
shipped on 2026-05-05.

**OPERATOR DECISION 2026-09-26:** close **#12** and **#13** on GitHub (no roadmap item: out of scope / a second
product), and close **#19** (I4) and **#22** (U3), both shipped 2026-05-05.

---

## Deferred / Out of Scope

| Feature | Reason |
|---------|--------|
| Outbound API rate limiting (Jellyfin/\*arr) | LAN services, no documented limits; natural scan serialization is sufficient |
| Whisper transcription integration | Out of scope; heavy model dependency; use Bazarr instead |
| Bare metal install guide | [#16](https://github.com/bpoulliot/xenotag/issues/16) — low demand, Docker is the primary path |
| P1 — Audio language override (fix `UND` tracks by rewriting media files) | [#21](https://github.com/bpoulliot/xenotag/issues/21) — operator decision 2026-09-26: 3,411 `UND` rows, and rewriting media files is a different product. Evidence in the P1 note under "Formerly In Progress" |
| I2 — Backup/restore API for `state.db` | [#18](https://github.com/bpoulliot/xenotag/issues/18) — operator decision 2026-09-26: premise does not hold here; `state.db` is bind-mounted at `/config` and restic backs up `~/docker` nightly. Evidence in the I1–I6 sweep note |
