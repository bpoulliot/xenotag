#!/usr/bin/env python3
"""Measure how a Sonarr/Radarr webhook can find its Jellyfin item (roadmap B8).

``handle_webhook()`` resolves a Sonarr/Radarr payload with
``find_item_by_provider_id()``, i.e. ``/Items?AnyProviderIdEquals=Tvdb.<id>``.
This probe answers the three questions a fix has to choose between:

1. **Filters** -- does any ``/Items`` parameter narrow by provider id on the
   server? A filter is only credited if a query for an id that is NOT in the
   library returns 0: a parameter Jellyfin ignores returns the whole library,
   so each candidate is asked for a present id AND an absent one.
2. **Listing cost** -- time and bytes of the one listing a client-side route
   needs (``Fields=ProviderIds,Path``), repeated ``--repeat`` times.
3. **Routes, over the whole library** -- every object in every *arr catalogue
   is turned into the payload its webhook would carry (Sonarr ``series.path`` +
   ``tvdbId``, Radarr ``movie.folderPath`` + ``tmdbId``; both equal the object's
   own ``path``/ids, captured from real events on the dev *arrs) and resolved
   against the listing by:

   * ``folder``   -- items of the right type whose folder is the payload path,
     using B5's own ``item_folders()``/``_norm_path()`` (imported, not copied);
   * ``provider`` -- items of the right type whose primary provider id equals
     the payload's (the client-side version of today's query);
   * ``folder_then_provider`` -- folder if it finds exactly one, else provider.

   Each result is checked against **B5's ownership** (``InstanceIndex.resolve``
   == OWNED for that object: id AND folder agree), the rule that already keeps
   HD/4K twins apart in production. A route is "right" when it returns exactly
   the B5 owner.

It also reports, from a second listing with ``MediaSources``, how many items
list more than one media source and whether ``MediaSources[0]`` is the item's
own file (what ``handle_webhook()`` and the scan probe).

Only GETs are sent. *arr catalogues are read from ``--arr-dir`` (one
``<instance>.json`` per instance, the JSON of ``GET /api/v3/series|movie``;
the kind comes from the file name prefix), so this script needs no *arr key.

    python3 scripts/measure_webhook_resolution.py --self-test
    python3 scripts/measure_webhook_resolution.py --url http://jellyfin:8096 \\
        --config config.yml --arr-dir /tmp/arr [--present Tvdb=280619] [--out /tmp/b8.json]

The Jellyfin key comes from ``--config`` (``jellyfin.api_key``) or
``JELLYFIN_API_KEY``; it is never printed. ``--self-test`` runs first on every
invocation and the probe refuses to report if it fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.arr_sync import OWNED, InstanceIndex, _norm_path, item_folders  # noqa: E402
from app.clients.radarr import RadarrClient  # noqa: E402
from app.clients.sonarr import SonarrClient  # noqa: E402

LISTING = {"Recursive": "true", "IncludeItemTypes": "Series,Movie", "Fields": "ProviderIds,Path"}
TYPE_FOR = {"sonarr": "Series", "radarr": "Movie"}
PRIMARY = {"sonarr": ("Tvdb", "tvdbId"), "radarr": ("Tmdb", "tmdbId")}
ABSENT_ID = "987654321"  # checked against the listing before use

# (label, params builder) -- every spelling of a provider-id filter worth trying.
# Jellyfin 10.11.10's OpenAPI lists none of the equality ones; they are sent
# anyway because an undocumented parameter could still be honoured.
FILTERS = (
    ("AnyProviderIdEquals={P}.id (today)", lambda p, v: {"AnyProviderIdEquals": f"{p}.{v}"}),
    ("anyProviderIdEquals={p}.id", lambda p, v: {"anyProviderIdEquals": f"{p.lower()}.{v}"}),
    ("AnyProviderIdEquals={P}:id", lambda p, v: {"AnyProviderIdEquals": f"{p}:{v}"}),
    ("AnyProviderIdEquals={P}=id", lambda p, v: {"AnyProviderIdEquals": f"{p}={v}"}),
    ("ProviderIds.{P}=id", lambda p, v: {f"ProviderIds.{p}": v}),
    ("{P}Id=id", lambda p, v: {f"{p}Id": v}),
    ("{p}Id=id", lambda p, v: {f"{p.lower()}Id": v}),
    ("HasAnyProviderId={P}.id", lambda p, v: {"HasAnyProviderId": f"{p}.{v}"}),
    ("searchTerm=id", lambda p, v: {"searchTerm": v}),
)


# --- pure logic (self-tested) ---


def filter_verdict(baseline: int, present: int, absent: int) -> str:
    """``filters`` only if the absent id finds nothing and the present one finds something."""
    if absent == 0 and 0 < present < baseline:
        return "filters"
    if present == absent == baseline:
        return "ignored"
    return "inconclusive"


def primary_id(item: dict, provider: str) -> str:
    ids = {key.lower(): val for key, val in (item.get("ProviderIds") or {}).items()}
    value = ids.get(provider.lower())
    return str(value).strip().lower() if value not in (None, "", "0") else ""


class Library:
    """A Jellyfin listing indexed the two ways a webhook could look an item up."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.by_folder: dict[tuple[str, str], list[dict]] = {}
        self.by_id: dict[tuple[str, str, str], list[dict]] = {}
        for item in items:
            for folder in item_folders(item):
                self.by_folder.setdefault((item.get("Type", ""), folder), []).append(item)
            for provider in ("Tvdb", "Tmdb"):
                value = primary_id(item, provider)
                if value:
                    self.by_id.setdefault((item.get("Type", ""), provider, value), []).append(item)

    def folder(self, kind: str, path: str) -> list[dict]:
        norm = _norm_path(path)
        return list(self.by_folder.get((TYPE_FOR[kind], norm), [])) if norm else []

    def provider(self, kind: str, value: object) -> list[dict]:
        text = str(value).strip().lower() if value not in (None, "", 0, "0") else ""
        return list(self.by_id.get((TYPE_FOR[kind], PRIMARY[kind][0], text), [])) if text else []


def _client(kind: str, name: str, objects: list[dict]):
    cls = SonarrClient if kind == "sonarr" else RadarrClient
    client = cls("http://unused.invalid", "unused", name=name)
    client._objects = {o["id"]: o for o in objects}
    return client


def b5_owners(library: Library, kind: str, client) -> dict[int, list[str]]:
    """object id -> the Jellyfin item ids B5 says own it (id AND folder agree)."""
    index = InstanceIndex(client)
    owners: dict[int, list[str]] = {}
    for item in library.items:
        if item.get("Type") != TYPE_FOR[kind]:
            continue
        match = index.resolve(item)
        if match.status == OWNED:
            owners.setdefault(match.object_id, []).append(item["Id"])
    return owners


def classify(found: list[dict], owner: list[str]) -> str:
    if not found:
        return "none"
    if len(found) > 1:
        return "multiple"
    if not owner:
        return "one, no B5 owner"
    return "right" if [found[0]["Id"]] == owner else "wrong"


def simulate(library: Library, catalogues: dict[str, tuple[str, list[dict]]]) -> dict:
    """Resolve every *arr object's webhook payload by each route; score against B5."""
    ids_by_kind: dict[str, Counter] = {"sonarr": Counter(), "radarr": Counter()}
    for kind, objects in catalogues.values():
        for obj in objects:
            value = str(obj.get(PRIMARY[kind][1]) or "")
            if value not in ("", "0"):
                ids_by_kind[kind][value] += 1
    out: dict = {"instances": {}, "twins": Counter(), "examples": {}}
    for name, (kind, objects) in sorted(catalogues.items()):
        owners = b5_owners(library, kind, _client(kind, name, objects))
        stats: dict[str, Counter] = {r: Counter() for r in ("folder", "provider", "folder_then_provider")}
        stats["b5_owner"] = Counter()
        for obj in objects:
            path = obj.get("path") or ""
            value = obj.get(PRIMARY[kind][1])
            owner = owners.get(obj["id"], [])
            stats["b5_owner"]["one" if len(owner) == 1 else ("none" if not owner else "multiple")] += 1
            by_folder = library.folder(kind, path)
            by_provider = library.provider(kind, value)
            combined = by_folder if len(by_folder) == 1 else by_provider
            results = {"folder": by_folder, "provider": by_provider, "folder_then_provider": combined}
            for route, found in results.items():
                verdict = classify(found, owner)
                stats[route][verdict] += 1
                bucket = out["examples"].setdefault(f"{name}/{route}/{verdict}", [])
                if verdict not in ("right", "none") and len(bucket) < 5:
                    bucket.append({"title": obj.get("title"), "path": path, "found": [i.get("Path") for i in found]})
            twin = str(value or "") not in ("", "0") and ids_by_kind[kind][str(value)] > 1
            if twin and owner:
                out["twins"][f"{kind}: twin objects with a B5 owner"] += 1
                out["twins"][f"{kind}: folder right"] += classify(by_folder, owner) == "right"
                out["twins"][f"{kind}: provider right"] += classify(by_provider, owner) == "right"
                out["twins"][f"{kind}: provider multiple"] += len(by_provider) > 1
        out["instances"][name] = {"kind": kind, "objects": len(objects), **{k: dict(v) for k, v in stats.items()}}
    out["twins"] = dict(out["twins"])
    out["examples"] = {k: v for k, v in out["examples"].items() if v}
    return out


def media_source_census(items: list[dict]) -> dict:
    """Items with >1 MediaSources, and whether MediaSources[0] is the item's own file."""
    out: Counter = Counter()
    examples = []
    for item in items:
        sources = item.get("MediaSources") or []
        out[f"{item.get('Type')}: items"] += 1
        if len(sources) > 1:
            out[f"{item.get('Type')}: >1 source"] += 1
        first = (sources[0].get("Path") or "") if sources else ""
        own = _norm_path(item.get("Path") or "")
        if item.get("Type") == "Movie" and first and own and _norm_path(first) != own:
            out["Movie: MediaSources[0] != own Path"] += 1
            if len(examples) < 5:
                examples.append({"name": item.get("Name"), "path": own, "source0": first})
    return {"counts": dict(out), "examples": examples}


# --- self-test ---


def self_test() -> None:
    def item(i, typ, path, **ids):
        return {"Id": i, "Type": typ, "Path": path, "ProviderIds": ids}

    items = [
        item("hd", "Movie", "/media/movies/Serenity (2005)/Serenity.mkv", Tmdb="16320"),
        item("uhd", "Movie", "/media/4k/Serenity (2005)/Serenity.mkv", Tmdb="16320"),
        item("ff", "Series", "/media/tv/Firefly", Tvdb="78874"),
        item("ff4k", "Series", "/media/4k-tv/Firefly", Tvdb="78874"),
        item("lonely", "Movie", "/media/movies/Lonely (2001)/Lonely.mkv", Tmdb="1"),
        item("wrongid", "Movie", "/media/movies/Mislabelled (2002)/m.mkv", Tmdb="2"),
        item("shareA", "Movie", "/media/movies/Shared/a.mkv", Tmdb="3"),
        item("shareB", "Movie", "/media/movies/Shared/b.mkv", Tmdb="4"),
        # B5 owns this one through Imdb; a Tmdb-only lookup finds the impostor.
        item("imdbonly", "Movie", "/media/movies/Imdb Only (2003)/i.mkv", Imdb="tt6"),
        item("impostor", "Movie", "/media/other/Impostor (2003)/i.mkv", Tmdb="77"),
        # Only the 4K copy is in Jellyfin; the HD object's id still finds it.
        item("only4k", "Movie", "/media/4k/Only 4K (2010)/f.mkv", Tmdb="78"),
    ]
    lib = Library(items)
    cats = {
        "radarr": (
            "radarr",
            [
                {"id": 1, "path": "/media/movies/Serenity (2005)", "tmdbId": 16320},
                {"id": 2, "path": "/media/movies/Lonely (2001)", "tmdbId": 1},
                {"id": 3, "path": "/media/movies/Mislabelled (2002)", "tmdbId": 99},  # id disagrees
                {"id": 4, "path": "/media/movies/Absent (1999)", "tmdbId": 5},  # not in Jellyfin
                {"id": 5, "path": "/media/movies/Shared", "tmdbId": 3},
                {"id": 6, "path": "/media/movies/Imdb Only (2003)", "tmdbId": 77, "imdbId": "tt6"},
                {"id": 7, "path": "/media/movies/Only 4K (2010)", "tmdbId": 78},
            ],
        ),
        "radarr-4k": ("radarr", [{"id": 1, "path": "/media/4k/Serenity (2005)", "tmdbId": 16320}]),
        "sonarr": ("sonarr", [{"id": 7, "path": "/media/tv/Firefly/", "tvdbId": 78874}]),
        "sonarr-4k": ("sonarr", [{"id": 7, "path": "/media/4k-tv/Firefly", "tvdbId": 78874}]),
    }
    res = simulate(lib, cats)
    r = res["instances"]["radarr"]
    assert r["folder"] == {"right": 3, "one, no B5 owner": 1, "none": 2, "multiple": 1}, r["folder"]
    assert r["provider"] == {"multiple": 1, "right": 2, "none": 2, "wrong": 1, "one, no B5 owner": 1}, r
    assert r["folder_then_provider"] == {"right": 4, "one, no B5 owner": 2, "none": 1}, r
    assert res["instances"]["radarr-4k"]["folder"] == {"right": 1}
    assert res["instances"]["radarr-4k"]["provider"] == {"multiple": 1}
    assert res["instances"]["sonarr"]["folder"] == {"right": 1}, "trailing slash must normalise"
    assert res["instances"]["sonarr-4k"]["provider"] == {"multiple": 1}
    tw = res["twins"]
    assert tw["radarr: twin objects with a B5 owner"] == 2 and tw["radarr: folder right"] == 2
    assert tw["radarr: provider right"] == 0 and tw["radarr: provider multiple"] == 2
    assert tw["sonarr: folder right"] == 2 and tw["sonarr: provider right"] == 0
    # A series must never match a movie folder, nor the other way round.
    assert lib.folder("sonarr", "/media/movies/Serenity (2005)") == []
    # Degenerate input: an empty library resolves nothing, and says so.
    empty = simulate(Library([]), cats)["instances"]["radarr"]
    assert empty["folder"] == {"none": 7} and empty["provider"] == {"none": 7}, empty
    # Filter verdicts in both directions.
    assert filter_verdict(9419, 9419, 9419) == "ignored"
    assert filter_verdict(9419, 1, 0) == "filters"
    assert filter_verdict(9419, 2, 0) == "filters"
    assert filter_verdict(9419, 0, 0) == "inconclusive"  # a filter that finds nothing is not a filter
    assert filter_verdict(9419, 5000, 4000) == "inconclusive"
    ms = media_source_census(
        [
            {"Type": "Movie", "Path": "/a/x.mkv", "MediaSources": [{"Path": "/b/x.mkv"}, {"Path": "/a/x.mkv"}]},
            {"Type": "Movie", "Path": "/c/y.mkv", "MediaSources": [{"Path": "/c/y.mkv"}]},
        ]
    )
    assert ms["counts"]["Movie: >1 source"] == 1 and ms["counts"]["Movie: MediaSources[0] != own Path"] == 1
    print("self-test: ok")


# --- live ---


def _api_key(config: str | None) -> str:
    if os.environ.get("JELLYFIN_API_KEY"):
        return os.environ["JELLYFIN_API_KEY"]
    if not config:
        sys.exit("need --config or JELLYFIN_API_KEY")
    import yaml

    key = ((yaml.safe_load(Path(config).read_text()) or {}).get("jellyfin") or {}).get("api_key") or ""
    if not key:
        sys.exit(f"no jellyfin.api_key in {config}")
    return key


def _count(client: httpx.Client, url: str, **extra) -> int:
    params = {"Recursive": "true", "IncludeItemTypes": "Series,Movie", "Limit": "1", **extra}
    r = client.get(f"{url}/Items", params=params)
    r.raise_for_status()
    return int(r.json().get("TotalRecordCount", -1))


def live(args: argparse.Namespace) -> dict:
    url = args.url.rstrip("/")
    headers = {"Authorization": f'MediaBrowser Token="{_api_key(args.config)}"'}
    report: dict = {"url": url}
    with httpx.Client(headers=headers, timeout=300) as client:
        info = client.get(f"{url}/System/Info")
        info.raise_for_status()
        report["version"] = info.json().get("Version")

        timings = []
        body = b""
        for _ in range(args.repeat):
            t0 = time.perf_counter()
            r = client.get(f"{url}/Items", params=LISTING)
            r.raise_for_status()
            body = r.content
            timings.append(round(time.perf_counter() - t0, 3))
        items = json.loads(body)["Items"]
        report["listing"] = {
            "params": LISTING,
            "seconds": timings,
            "bytes": len(body),
            "items": len(items),
            "types": dict(Counter(i.get("Type") for i in items)),
        }
        library = Library(items)

        # Filters: one present id per provider (from the listing unless given), one absent.
        baseline = _count(client, url)
        present: dict[str, str] = dict(p.split("=", 1) for p in args.present)
        for provider in ("Tvdb", "Tmdb"):
            if provider not in present:
                present[provider] = next(v for (_, p, v) in library.by_id if p == provider)
            known = {v for (_, p, v) in library.by_id if p == provider}
            assert ABSENT_ID not in known, f"{ABSENT_ID} is a real {provider} id here; pick another"
        filters = {"baseline (no filter)": baseline}
        for provider, value in present.items():
            for label, build in FILTERS:
                p_count = _count(client, url, **build(provider, value))
                a_count = _count(client, url, **build(provider, ABSENT_ID))
                name = label.replace("{P}", provider).replace("{p}", provider.lower())
                filters[f"{name} present={value}"] = {
                    "present": p_count,
                    "absent": a_count,
                    "verdict": filter_verdict(baseline, p_count, a_count),
                }
        for flag in ("hasTvdbId", "hasTmdbId", "hasImdbId"):
            filters[flag] = {v: _count(client, url, **{flag: v}) for v in ("true", "false")}
        report["filters"] = filters

        if args.media_sources:
            t0 = time.perf_counter()
            r = client.get(f"{url}/Items", params={**LISTING, "Fields": "ProviderIds,Path,MediaSources"})
            r.raise_for_status()
            report["media_sources"] = {
                "seconds": round(time.perf_counter() - t0, 3),
                "bytes": len(r.content),
                **media_source_census(r.json()["Items"]),
            }

    if args.arr_dir:
        catalogues: dict[str, tuple[str, list[dict]]] = {}
        for path in sorted(Path(args.arr_dir).glob("*.json")):
            kind = "sonarr" if path.stem.startswith("sonarr") else "radarr" if path.stem.startswith("radarr") else ""
            if kind:
                catalogues[path.stem] = (kind, json.loads(path.read_text()))
        if not catalogues or not any(objs for _, objs in catalogues.values()):
            sys.exit(f"--arr-dir {args.arr_dir} holds no sonarr*/radarr* catalogue")
        report["routes"] = simulate(library, catalogues)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true", help="run the self-test only")
    ap.add_argument("--url", help="Jellyfin base URL")
    ap.add_argument("--config", help="xenotag config.yml to read jellyfin.api_key from")
    ap.add_argument("--arr-dir", help="directory of <instance>.json *arr catalogues")
    ap.add_argument("--present", action="append", default=[], help="Provider=id known to be in the library")
    ap.add_argument("--repeat", type=int, default=3, help="times to time the listing")
    ap.add_argument("--media-sources", action="store_true", help="also census MediaSources (a heavier listing)")
    ap.add_argument("--out", help="write the full JSON report here")
    args = ap.parse_args(argv)

    self_test()
    if args.self_test:
        return 0
    if not args.url:
        ap.error("--url is required unless --self-test")
    report = live(args)
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
