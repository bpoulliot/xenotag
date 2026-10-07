# B12 — why Jellyfin loses xenotag's `xt-` tags (measured 2026-10-07)

Roadmap item B12, NEEDS MEASUREMENT. Question: which writer removes or replaces `xt-` tags after
xenotag writes them, and does it act on the five films and on any others?

## Verdict

The filing assumed something rewrote the five films' tags *after* the 2026-09-24 scan wrote them.
That premise is wrong. Three different things change xenotag's tags on Jellyfin. Only the first two
lose any.

1. **A library-wide metadata refresh on 2026-09-04, 04:00–08:00Z, wiped xenotag's tags from about
   8,000 items, the five among them.** Every Movie/Series item last saved before 09-04 still had
   `xt-` tags on 09-08 (1,304 of 1,304). Of the items saved between 09-04 and 09-08, 7,950 of 8,038
   had none. 5,454 were saved on 09-04 alone, across every drive. Four of the five films were saved
   at 07:05–07:08Z that morning; *Steel Magnolias* was saved again on 09-08. These were refreshes of
   existing items, not re-creations: `DateCreated` is unchanged, and `DateLastRefreshed` equals the
   save time. Each item kept only its NFO tags and provider keywords. In the lab, on both versions,
   **only `replaceAllMetadata=true` produces that result**. The full scans of 09-23 and 09-24 rewrote
   the library, but on 09-26 the five still had exactly their 09-08 tags. Those two writes did not
   stick, which is B17's class of failure. The 09-27 re-tag restored them, and they still carry
   their tags now: on production 12.2.0, read by `Ids=` at 02:10Z on 10-07.
2. **Re-creating an item drops every tag the NFO does not carry.** When Jellyfin removes an item
   and adds it back, the new item has the same path-derived Id. It is built from the NFO plus
   provider data only. The 12.2.0 upgrade did this to one tracked item (*Jimmy Carr: Stand Up*, a
   two-file folder, `DateCreated` 2026-10-06 18:16:49Z). It lost `xt-sub-EN` and `xt-Not-Rated`,
   which no \*arr carries. `state.db` still says the item is tagged, and its file is unchanged, so no
   incremental scan will go back to it.
3. **The \*arrs' NFOs respell xenotag's tags in lowercase. Nothing is lost.** Every \*arr runs
   the Kodi/Emby metadata consumer. Each daily (Radarr) or 12-hourly (Sonarr) refresh rewrites the
   NFOs whose content changed. Jellyfin's real-time monitor then refreshes the library folder and
   merges the NFO into the item: NFO tags first, then the rest, keeping one copy of each tag
   regardless of case. The \*arr's lowercase `xt-aac` therefore replaces xenotag's `xt-AAC`. Tags no
   \*arr carries (`xt-sub-EN`, the rating) and the provider keywords are kept. On 10.11.10 this had
   happened to **7,732 of the 9,445 tracked items (82%)** by 2026-10-06 18:12Z, and to 7,791 on
   12.2.0 by 01:45Z on 10-07. Jellyfin compares tags without regard to case (below), so its users
   see no difference. xenotag's U9 drift check compares case-sensitively, though. At the next full
   scan, which the next release forces, it will report about 7,800 items as drifted. That noise
   would hide a real loss like (1) or (2).

**Ruled out:**
* **The hourly `jellyfin-refresh-transcoded.timer`** (`FullRefresh`, `replaceAllMetadata=false`). In
  the lab it keeps every tag, xenotag's spelling included, on both versions. It only refreshes files
  Tdarr transcoded, and Tdarr last touched the five in May and August (all "Not required").
* **xenotag's own `refresh_item()`** (Default mode) keeps every tag in the lab. It did not reproduce
  B17's undo there either.
* **`nav1s.sh`** makes no Jellyfin call (the 2026-09-26 sweep).

**Could not determine: who issued the 09-04 refresh.** Jellyfin's server log only goes back to
2026-10-04, and its ActivityLog does not record refreshes. No script on the host sends
`replaceAllMetadata=true`; `~/docker/scripts`, xenotag and van1sh were all grepped. That leaves a
"Replace all metadata" refresh from the dashboard, or an identify, as the likeliest source, but it
is inferred. Also not proven: *why* the 09-23/24 writes to the five did not stick. That needs a
`jellyfin.db` snapshot from between 2026-09-24 22:51Z and 09-26, and none exists. B17's undo is
the likeliest cause: the tags on 09-26 were byte-for-byte the 09-08 set, and B17 saw films whose
write was undone in one scan still carry their old tags after the next (scan 148).

## Evidence

### Production, read-only

Instruments:
* copies of `jellyfin.db` (with `-wal`/`-shm`): the pre-upgrade 10.11.10 backup (last save
  2026-10-06 18:12:45Z), a live 12.2.0 copy (2026-10-07 01:45Z), and the 2026-09-08 backup
  (`~/docker/jellyfin/db-backup-2026-09-08/`);
* copies of `state.db`: the live one (2026-10-07 01:45Z), and the `pre-b5-live` (09-26) and
  `pre-u1` (09-24) backups;
* the NFO files, read in place;
* a copy of Tdarr's database;
* GET-only `/Items?Ids=` reads and `Tags=` counts through `ReadOnlyTransport` (9 GETs, 0 blocked).

Every tracked item was counted, not a sample.
`scripts/measure_nfo_tag_drift.py` reproduces every count below. Its `--self-test` checks both
directions: a planted flip must be detected and predicted, a planted unexplained tag must be
detected and not predicted, and a match and a wiped item must each be classified correctly. Two
mutants were run against it (the xenotag spelling winning the merge, and a classifier that sees
nothing), and the self-test failed both.

| Jellyfin copy | tracked items | `xt-` set as written | \*arr spelling only (case) | other drift | no `xt-` |
|---|---|---|---|---|---|
| 10.11.10, 10-06 18:12Z | 9,445 | 1,707 | 7,732 | 2 | 4 |
| 12.2.0, 10-07 01:45Z | 9,445 | 1,647 | 7,791 | 3 | 4 |

* **The NFO-merge rule explains the respelling exactly.** For 7,732 of the 7,734 drifted items,
  the `xt-` set equals the NFO's tags merged ahead of xenotag's, one copy per tag regardless of case
  (12.2: 7,791 of 7,794). The 2 exceptions are B17's films *Under Paris* and *Wonder Boys*: a
  pre-B9 `xt-H.264` survived alongside the NFO's `xt-h264`. The third exception on 12.2 is the
  re-created *Jimmy Carr* item.
* **Every drifted item was saved by Jellyfin after xenotag last wrote it** (7,738 of 7,738), and
  6,084 were saved within 2 minutes of their NFO's mtime (another 752 within 10 minutes). Of the
  items still as written, 1,681 of 1,707 have not been saved since xenotag wrote them, and 1,664
  of the 1,707 have NFOs that already carry lowercase `xt-` labels. Those items will flip at their
  NFO's next rewrite.
* **The flips land in the \*arrs' refresh windows.** Saves cluster at 19–20Z (5,904 + 258), which
  is Radarr's `Refresh Movie` (every 1,440 min; last run 20:12Z on 10-06). Smaller clusters at 14Z
  (681), 17Z (672) and 06Z (137) fit Sonarr's `Refresh Series` (every 720 min, start time
  drifting). They run 3,602 on 09-27 (the day after B5 put `xt-` labels into the \*arrs), then 30 to
  1,470 a day. 60 more items flipped at 20:0xZ on 10-06, after the 12.2 upgrade. The mechanism is
  the same on 12.2.
* **No \*arr notifies Jellyfin.** None of the five has a connection configured
  (`GET /api/v3/notification`). Jellyfin finds out from its own real-time monitor. During the
  20:0xZ burst on 10-06, the prod log shows the movie genre folders queued ("will be refreshed").
* **Jellyfin matches tags without regard to case.** `Tags=xt-H264` and `Tags=xt-h264` both return
  3,694 items, `xt-AAC`/`xt-aac` both 4,917, `xt-sub-EN`/`xt-sub-en` both 7,042, and a made-up tag
  returns 0.
* **The five, by `Ids=` on 12.2.0 (02:10Z 10-07):** each carries its `xt-` tags. The \*arr-carried
  labels are in the \*arr spelling (`xt-h264`, `xt-aac`, `xt-und`); `xt-sub-EN` and the rating are
  in xenotag's.
* **The 09-04 wipe, from the 09-08 backup** (`--by-saved-date`):

  | Jellyfin last saved | with `xt-` | without |
  |---|---|---|
  | before 2026-09-04 | 1,304 | 0 |
  | 2026-09-04 | 3 | 5,454 |
  | 2026-09-05 | 6 | 312 |
  | 2026-09-06 | 15 | 502 |
  | 2026-09-07 | 0 | 14 |
  | 2026-09-08 | 64 | 1,668 |

  On 09-04, 5,113 items were saved between 04:00 and 08:00Z, about one every 2–3 s, from every
  drive (luxe 1,825, flux 1,298, scopuli 1,168, core 1,088, xtor 77, 4K 1). Of the 8,038 items
  saved 09-04 to 09-08, 26 were created on or after 09-04, so this was not re-creation. The few
  "with" rows after 09-04 are items xenotag's daily incremental scans wrote, and later union
  refreshes do not remove those.

### Lab: a throwaway Jellyfin, one synthetic film, both versions

`scripts/measure_nfo_tag_merge.py` starts `jellyfin/jellyfin:10.11.10` and `:12.2` with a scratch
`/config`, as the calling user, on 127.0.0.1, removed on exit. The library uses production's
options (NFO reader on, no NFO saver, real-time monitor on), with remote fetchers off. The harness
writes tags through the shipped `JellyfinClient.set_managed_tags()` and reads them back by `Ids=`.
Self-test: the write must read back, and nothing must change when nothing is done. Each NFO rewrite
also carries a marker tag, so a step whose NFO was never read is reported as such. **10.11.10 and
12.2.0 gave the same result at every step.**

| step | what | xenotag's tags | keywords |
|---|---|---|---|
| s2 | xenotag `refresh_item()` (Default) | kept, xenotag spelling | kept |
| s3 | timer: `FullRefresh`, `replaceAllMetadata=false` | kept | kept |
| s4 | NFO rewritten (`luxe`), real-time monitor | kept; **NFO tags merged first** | kept |
| s5 | NFO rewritten, then the timer's `FullRefresh` | kept; NFO's new tag appended last | kept |
| s9 | NFO now has lowercase `xt-`, timer's `FullRefresh` | kept, **xenotag spelling wins** | kept |
| s10 | `FullRefresh`, **`replaceAllMetadata=true`** | **all gone except the NFO's**, in the NFO's lowercase | **gone** |

The monitor did not read an NFO rewrite that followed straight after an API write (s6, s11, and
the `case-flip` scenario's c1/c3, all within 150–300 s). So the lab never reproduced the case flip
itself; production's 7,732 exact predictions are the evidence for it. What the lab does show is
the order the monitor path merges in (s4: NFO first) and the opposite order on the timer path
(s5/s9). That ordering is why only NFO-triggered refreshes respell.

## Recommended fix — an opinion, not built here

* **(a) Compare Jellyfin tags case-insensitively wherever xenotag compares them**: U9's
  `_tag_drift()` and B17's read-back. Jellyfin itself does. Without this, the next full scan logs
  roughly 7,800 false drift warnings, and real losses disappear in that noise. Nothing in this
  change needs an operator decision.
* **(b) A reconciliation pass.** Read every tracked item's tags by `Ids=` (about 95 GETs at
  `TAG_READ_BATCH = 100`), and re-write any item missing a managed tag, comparing without regard to
  case. Nothing else repairs (1) or (2): an mtime-driven scan never goes back to an unchanged file,
  so a wipe like 09-04 waits for the next forced full scan. **Needs an operator call:** how often it
  runs, and whether it may write to items whose file did not change.
* **Not recommended:** writing Jellyfin tags in the \*arrs' lowercase to stop the flip (that would
  undo B9's spelling, which is a taste call), or turning off the \*arrs' NFO tags (an ops change on
  five instances, and the Kodi consumer has no per-field switch for tags).
* **Operator practice:** "Replace all metadata" in Jellyfin wipes xenotag's tags, and so does
  anything else that sends `replaceAllMetadata=true`. After one, run a full scan.

## Re-running

    python3 scripts/measure_nfo_tag_drift.py --self-test
    python3 scripts/measure_nfo_tag_drift.py --state-db COPY/state.db --jellyfin-db COPY/jellyfin.db
    python3 scripts/measure_nfo_tag_drift.py --jellyfin-db OLD_COPY/jellyfin.db --by-saved-date
    PYTHONPATH=. python3 scripts/measure_nfo_tag_merge.py --image jellyfin/jellyfin:12.2 \
        --port 18097 --workdir /tmp/xt-nfo-lab --out /tmp/nfo-merge.json [--scenario case-flip]
