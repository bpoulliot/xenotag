#!/usr/bin/env python3
"""What does a Jellyfin refresh do to tags xenotag wrote, when an NFO sits beside the file? (roadmap B12)

Production's movie and series libraries read local metadata from NFO files (reader
``Nfo``, saver none, real-time monitor on), and every \\*arr writes those NFOs with
its own tag labels as ``<tag>``. This harness runs a THROWAWAY Jellyfin of the given
image with a scratch ``/config`` and one synthetic film, writes xenotag's tags
through the shipped ``JellyfinClient.set_managed_tags()``, and then drives each
refresh path production has -- xenotag's own ``refresh_item()`` (Default mode), the
host timer's ``FullRefresh`` with ``replaceAllMetadata=false``, an NFO rewrite picked
up by the real-time monitor, an NFO rewrite followed by the timer's refresh,
``replaceAllMetadata=true`` and a library scan -- reading the item's ``Tags`` back by
``/Items?Ids=`` after each one.

    python3 scripts/measure_nfo_tag_merge.py --image jellyfin/jellyfin:10.11.10 \\
        --port 18096 --workdir /tmp/xt-b12-jf1011 --out /tmp/xt-b12-jf1011.json

Nothing outside ``--workdir`` is touched, the container runs ``--rm`` as the calling
user (so no root-owned files are left), publishes on 127.0.0.1 only, carries the
``overnight.item`` label when ``OVERNIGHT_ITEM`` is set, and is removed on exit.
Remote metadata fetchers are disabled for the library: only the NFO and the API
write tags.

Self-test, both directions, before any result is reported:
  * the reader must SEE a change -- xenotag's write must read back exactly;
  * the reader must see NO change where none was made -- two reads with nothing in
    between must be equal;
  * every NFO rewrite carries a step-unique marker tag (``probe-sN``), and a step
    whose marker never reads back is reported as ``nfo_not_ingested`` rather than as
    evidence that the NFO kept or dropped anything.
The script exits non-zero and writes no result if either of the first two fails.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.jellyfin import JellyfinClient  # noqa: E402

FILM = "Probe Film (2000)"
XT_TAGS = ["xt-1080p", "xt-H264", "xt-AAC", "xt-sub-EN", "xt-R"]
KEYWORDS = ["kw-time travel", "kw-heist"]
ARR_LOWER = ["xt-1080p", "xt-h264", "xt-aac"]  # what an *arr's NFO carries after B5
AUTH = 'MediaBrowser Client="xt-b12", Device="probe", DeviceId="xt-b12-probe", Version="1.0"'


def nfo_text(tags: list[str]) -> str:
    body = "".join(f"  <tag>{t}</tag>\n" for t in tags)
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<movie>\n'
        "  <title>Probe Film</title>\n  <year>2000</year>\n  <mpaa>R</mpaa>\n"
        f"{body}</movie>\n"
    )


def make_fixture(workdir: Path) -> Path:
    film_dir = workdir / "media" / "movies" / FILM
    film_dir.mkdir(parents=True, exist_ok=True)
    (workdir / "config").mkdir(exist_ok=True)
    video = film_dir / f"{FILM}.mkv"
    if not video.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=24",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440",
                "-t",
                "3",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-metadata:s:a:0",
                "language=eng",
                str(video),
            ],
            check=True,
        )
    return film_dir / f"{FILM}.nfo"


class Jf:
    def __init__(self, base: str) -> None:
        self.base = base
        self.c = httpx.Client(timeout=30, headers={"Authorization": AUTH})

    def wait_up(self, limit: float = 240) -> None:
        end = time.time() + limit
        while time.time() < end:
            try:
                r = self.c.get(f"{self.base}/System/Info/Public")
                # an early answer during startup carries no Version yet
                if r.status_code == 200 and "Version" in r.json():
                    time.sleep(5)
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(2)
        raise SystemExit("Jellyfin never came up")

    def setup(self) -> str:
        """Startup wizard, admin login, an API key, a Movies library. Returns the key."""
        version = self.c.get(f"{self.base}/System/Info/Public").json()["Version"]
        p = self.c.post
        p(
            f"{self.base}/Startup/Configuration",
            json={"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"},
        )
        self.c.get(f"{self.base}/Startup/User")
        p(f"{self.base}/Startup/User", json={"Name": "probe", "Password": "probe-pass"})
        p(f"{self.base}/Startup/RemoteAccess", json={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False})
        p(f"{self.base}/Startup/Complete")
        r = p(f"{self.base}/Users/AuthenticateByName", json={"Username": "probe", "Pw": "probe-pass"})
        r.raise_for_status()
        token = r.json()["AccessToken"]
        self.c.headers["Authorization"] = f'{AUTH}, Token="{token}"'
        p(f"{self.base}/Auth/Keys", params={"app": "xt-b12"}).raise_for_status()
        keys = self.c.get(f"{self.base}/Auth/Keys").json()["Items"]
        key = keys[0]["AccessToken"]
        opts = {
            "LibraryOptions": {
                "EnableRealtimeMonitor": True,
                "MetadataSavers": [],
                "LocalMetadataReaderOrder": ["Nfo"],
                "DisabledLocalMetadataReaders": [],
                "SaveLocalMetadata": False,
                "EnableInternetProviders": False,
                "TypeOptions": [{"Type": "Movie", "MetadataFetchers": [], "ImageFetchers": []}],
                "PathInfos": [{"Path": "/media/movies"}],
            }
        }
        p(
            f"{self.base}/Library/VirtualFolders",
            params={"name": "Movies", "collectionType": "movies", "refreshLibrary": "true"},
            json=opts,
        ).raise_for_status()
        print(f"Jellyfin {version} up; library created", flush=True)
        return key

    def find_film(self, limit: float = 180) -> str:
        end = time.time() + limit
        while time.time() < end:
            d = self.c.get(f"{self.base}/Items", params={"Recursive": "true", "IncludeItemTypes": "Movie"}).json()
            if d.get("Items"):
                return d["Items"][0]["Id"]
            time.sleep(3)
        raise SystemExit("the library scan never found the film")

    def read(self, iid: str) -> dict:
        d = self.c.get(f"{self.base}/Items", params={"Ids": iid, "Fields": "Tags,DateLastRefreshed"}).json()
        it = d["Items"][0]
        return {"tags": it.get("Tags") or [], "refreshed": it.get("DateLastRefreshed")}


def wait_for(jf: Jf, iid: str, pred, limit: float) -> tuple[dict, bool]:
    end = time.time() + limit
    cur = jf.read(iid)
    while time.time() < end:
        cur = jf.read(iid)
        if pred(cur):
            return cur, True
        time.sleep(3)
    return cur, False


def run(args) -> dict:
    workdir = Path(args.workdir).resolve()
    nfo = make_fixture(workdir)
    nfo.write_text(nfo_text(["luxe", "probe-s0"]))
    name = f"xt-b12-jf-{args.port}"
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-p",
        f"127.0.0.1:{args.port}:8096",
        "-v",
        f"{workdir/'config'}:/config",
        "-v",
        f"{workdir/'media'}:/media:ro",
        "--tmpfs",
        "/cache",
        "--memory",
        "2g",
    ]
    if os.environ.get("OVERNIGHT_ITEM"):
        cmd += ["--label", f"overnight.item={os.environ['OVERNIGHT_ITEM']}"]
    subprocess.run(cmd + [args.image], check=True, capture_output=True)
    steps: list[dict] = []
    try:
        jf = Jf(f"http://127.0.0.1:{args.port}")
        jf.wait_up()
        key = jf.setup()
        iid = jf.find_film()
        xt = JellyfinClient(jf.base, key)
        version = jf.c.get(f"{jf.base}/System/Info/Public").json()["Version"]

        def record(step: str, what: str, state: dict, ingested: bool | None = None) -> None:
            tags = state["tags"]
            row = {
                "step": step,
                "what": what,
                "tags": tags,
                "xenotag_spelling_kept": [t for t in XT_TAGS if t in tags],
                "xt_any_case": sorted({t for t in tags if t.lower().startswith("xt-")}),
                "keywords_kept": [k for k in KEYWORDS if k in tags],
            }
            if ingested is not None:
                row["nfo_ingested"] = ingested
            steps.append(row)
            print(json.dumps(row), flush=True)

        def full_item():
            return xt.get_item_by_id(iid)

        def write_xenotag() -> dict:
            xt.set_managed_tags(iid, full_item(), "xt-", XT_TAGS)
            return wait_for(jf, iid, lambda s: all(t in s["tags"] for t in XT_TAGS), 30)[0]

        def refresh_and_wait(params: dict | None, xenotag_path: bool = False) -> dict:
            before = jf.read(iid)["refreshed"]
            if xenotag_path:
                xt.refresh_item(iid)
            else:
                jf.c.post(f"{jf.base}/Items/{iid}/Refresh", params=params).raise_for_status()
            state, _ = wait_for(jf, iid, lambda s: s["refreshed"] != before, 60)
            time.sleep(3)
            return jf.read(iid)

        def rewrite_nfo(step: str, tags: list[str]) -> str:
            marker = f"probe-{step}"
            time.sleep(1.1)  # a distinct mtime
            nfo.write_text(nfo_text(tags + [marker]))
            return marker

        s0, ok0 = wait_for(jf, iid, lambda s: "probe-s0" in s["tags"], 60)
        record("s0", "initial scan, NFO tags [luxe]", s0, ok0)

        # keywords stand in for TMDB's: tags the item has that neither the NFO nor xenotag owns
        xt.set_managed_tags(iid, full_item(), "kw-", KEYWORDS)
        wait_for(jf, iid, lambda s: all(k in s["tags"] for k in KEYWORDS), 30)
        s1 = write_xenotag()
        # self-test, direction 1: the write must read back exactly
        if not all(t in s1["tags"] for t in XT_TAGS):
            raise SystemExit(f"SELF-TEST FAILED: xenotag's write never read back: {s1['tags']}")
        # self-test, direction 2: nothing changes when nothing is done
        time.sleep(5)
        if jf.read(iid)["tags"] != s1["tags"]:
            raise SystemExit("SELF-TEST FAILED: tags changed with nothing done")
        record("s1", "xenotag set_managed_tags (keywords first, then xt-)", s1)

        if args.scenario == "case-flip":
            # production's sequence: xenotag writes, later the *arr rewrites its NFO with
            # its lowercase labels, and only the real-time monitor tells Jellyfin
            time.sleep(20)
            m = rewrite_nfo("c1", ["luxe"] + ARR_LOWER)
            st, ok = wait_for(jf, iid, lambda s: m in s["tags"], args.monitor_wait)
            record("c1", "NFO rewritten WITH lowercase xt- (post-B5 *arr), real-time monitor only", st, ok)
            record("c2", "xenotag set_managed_tags again (the next re-tag)", write_xenotag())
            time.sleep(20)
            m = rewrite_nfo("c3", ["luxe"] + ARR_LOWER)
            st, ok = wait_for(jf, iid, lambda s: m in s["tags"], args.monitor_wait)
            record("c3", "NFO rewritten again, same lowercase labels, real-time monitor only", st, ok)
            return {"image": args.image, "version": version, "scenario": args.scenario, "steps": steps}

        record("s2", "xenotag refresh_item() (Default mode), NFO unchanged", refresh_and_wait(None, True))
        record(
            "s3",
            "timer: FullRefresh replaceAllMetadata=false, NFO unchanged",
            refresh_and_wait(
                {
                    "metadataRefreshMode": "FullRefresh",
                    "imageRefreshMode": "Default",
                    "replaceAllMetadata": "false",
                    "replaceAllImages": "false",
                }
            ),
        )

        m = rewrite_nfo("s4", ["luxe"])
        st, ok = wait_for(jf, iid, lambda s: m in s["tags"], args.monitor_wait)
        record("s4", "NFO rewritten without xt- (pre-B5 *arr), real-time monitor only", st, ok)
        write_xenotag()

        m = rewrite_nfo("s5", ["luxe"])
        st = refresh_and_wait(
            {
                "metadataRefreshMode": "FullRefresh",
                "imageRefreshMode": "Default",
                "replaceAllMetadata": "false",
                "replaceAllImages": "false",
            }
        )
        record("s5", "NFO rewritten without xt-, then timer FullRefresh replaceAllMetadata=false", st, m in st["tags"])
        write_xenotag()

        m = rewrite_nfo("s6", ["luxe"] + ARR_LOWER)
        st, ok = wait_for(jf, iid, lambda s: m in s["tags"], args.monitor_wait)
        record("s6", "NFO rewritten WITH lowercase xt- (post-B5 *arr), real-time monitor only", st, ok)

        record("s7", "xenotag set_managed_tags again (re-tag after s6)", write_xenotag())
        record("s8", "xenotag refresh_item() (Default mode), NFO unchanged since s6", refresh_and_wait(None, True))
        record(
            "s9",
            "timer: FullRefresh replaceAllMetadata=false, NFO unchanged since s6",
            refresh_and_wait(
                {
                    "metadataRefreshMode": "FullRefresh",
                    "imageRefreshMode": "Default",
                    "replaceAllMetadata": "false",
                    "replaceAllImages": "false",
                }
            ),
        )
        write_xenotag()
        record(
            "s10",
            "FullRefresh replaceAllMetadata=true, NFO unchanged since s6",
            refresh_and_wait(
                {
                    "metadataRefreshMode": "FullRefresh",
                    "imageRefreshMode": "Default",
                    "replaceAllMetadata": "true",
                    "replaceAllImages": "false",
                }
            ),
        )
        write_xenotag()
        m = rewrite_nfo("s11", ["luxe"])
        jf.c.post(f"{jf.base}/Library/Refresh").raise_for_status()
        st, ok = wait_for(jf, iid, lambda s: m in s["tags"], args.monitor_wait)
        record("s11", "NFO rewritten without xt-, then a library scan (/Library/Refresh)", st, ok)
        return {"image": args.image, "version": version, "scenario": args.scenario, "steps": steps}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--image", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--scenario",
        choices=["paths", "case-flip"],
        default="paths",
        help="paths: every refresh path in turn; case-flip: xenotag writes, then the NFO gains "
        "the *arr's lowercase labels (production's sequence)",
    )
    ap.add_argument(
        "--monitor-wait",
        type=float,
        default=150.0,
        help="seconds to wait for the real-time monitor to ingest an NFO rewrite",
    )
    args = ap.parse_args()
    result = run(args)
    Path(args.out).write_text(json.dumps(result, indent=1))
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
