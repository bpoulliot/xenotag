"""Sonarr/Radarr tag sync (roadmap B5).

The fake *arr below runs behind ``httpx.MockTransport``, so these tests drive
the real clients -- URLs, editor payloads, the read-only transport -- and the
fake reproduces what the dev instances were measured to do (Sonarr
4.0.18.2978, Radarr 6.3.0.10514, the versions production runs):

* labels are stored lowercased; POSTing a label that exists in another case is
  a 409, not a lookup;
* Radarr answers 400 for a label outside ``[a-z0-9-]``, Sonarr accepts it;
* ``PUT /{series|movie}/editor`` with ``applyTags`` add/remove changes the tags
  and nothing else; with an unknown id it is a 500.
"""

from __future__ import annotations

import copy
import json
import re

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import pipeline
from app.arr_sync import (
    AMBIGUOUS,
    NONE,
    OWNED,
    ArrTagSync,
    InstanceIndex,
    item_folders,
    plan_tags,
    readback_problems,
)
from app.clients import readonly
from app.clients.radarr import RadarrClient
from app.clients.readonly import ReadOnlyTransport, ReadOnlyViolation
from app.clients.sonarr import SonarrClient
from app.config import AppConfig, ArrInstance
from app.scanner import AudioTrack, MediaInfo
from app.state import Base, MediaState

_RADARR_LABEL = re.compile(r"[a-z0-9-]+")


class FakeArr:
    """Just enough of a Sonarr/Radarr v3 API, with a request log."""

    def __init__(self, kind: str, objects: list[dict], tags: dict[str, int] | None = None) -> None:
        self.kind = kind
        self.collection = "series" if kind == "sonarr" else "movie"
        self.objects = {o["id"]: copy.deepcopy(o) for o in objects}
        for o in self.objects.values():
            o.setdefault("tags", [])
        self.tags = dict(tags or {})
        self.requests: list[tuple[str, str]] = []
        self.on_edit = None  # hook(obj) -> None, to misbehave on purpose
        self.fail_status: dict[str, int] = {}  # method -> status to answer instead

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[0] != "GET"]

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path.removeprefix("/api/v3")
        self.requests.append((method, path))
        if method in self.fail_status:
            return httpx.Response(self.fail_status[method], json={"message": "fake failure"})
        coll = f"/{self.collection}"
        if method == "GET" and path == coll:
            return httpx.Response(200, json=list(self.objects.values()))
        if method == "GET" and path.startswith(coll + "/"):
            oid = int(path.rsplit("/", 1)[1])
            if oid not in self.objects:
                return httpx.Response(404, json={"message": "NotFound"})
            return httpx.Response(200, json=self.objects[oid])
        if method == "GET" and path == "/tag":
            return httpx.Response(200, json=[{"label": k, "id": v} for k, v in self.tags.items()])
        if method == "POST" and path == "/tag":
            label = json.loads(request.content)["label"]
            if self.kind == "radarr" and not _RADARR_LABEL.fullmatch(label.lower()):
                return httpx.Response(400, json=[{"errorMessage": "Allowed characters a-z, 0-9 and -"}])
            if label in self.tags:
                return httpx.Response(201, json={"label": label, "id": self.tags[label]})
            if label.lower() in self.tags:
                return httpx.Response(409, json={"message": "UNIQUE constraint failed: Tags.Label"})
            self.tags[label.lower()] = max(self.tags.values(), default=0) + 1
            return httpx.Response(201, json={"label": label.lower(), "id": self.tags[label.lower()]})
        if method == "PUT" and path == f"{coll}/editor":
            body = json.loads(request.content)
            ids = body["seriesIds" if self.kind == "sonarr" else "movieIds"]
            if any(i not in self.objects for i in ids):
                return httpx.Response(500, json={"message": "Expected query to return 1 rows but returned 0"})
            for i in ids:
                obj = self.objects[i]
                if body["applyTags"] == "add":
                    obj["tags"] = list(dict.fromkeys(obj["tags"] + body["tags"]))
                elif body["applyTags"] == "remove":
                    obj["tags"] = [t for t in obj["tags"] if t not in body["tags"]]
                if self.on_edit:
                    self.on_edit(obj)
            return httpx.Response(202, json=[self.objects[i] for i in ids])
        if method == "PUT" and path.startswith(coll + "/"):
            oid = int(path.rsplit("/", 1)[1])
            self.objects[oid] = json.loads(request.content)
            return httpx.Response(202, json=self.objects[oid])
        return httpx.Response(404, json={"message": f"fake: no route {method} {path}"})


def client_for(fake: FakeArr, name: str, *, read_only: bool = False):
    transport = httpx.MockTransport(fake.handle)
    if read_only:
        transport = ReadOnlyTransport(transport)
    cls = SonarrClient if fake.kind == "sonarr" else RadarrClient
    return cls(f"http://{name}", "key", name, transport=transport)


def series(oid, title, tvdb, path, *, imdb=None, tmdb=None, tags=(), cert=None, **extra):
    return {
        "id": oid,
        "title": title,
        "tvdbId": tvdb,
        "tmdbId": tmdb or 0,
        "imdbId": imdb,
        "path": path,
        "tags": list(tags),
        "certification": cert,
        "monitored": False,
        "qualityProfileId": 1,
        **extra,
    }


def movie(oid, title, tmdb, path, *, imdb=None, tags=(), cert=None, **extra):
    return {
        "id": oid,
        "title": title,
        "tmdbId": tmdb,
        "imdbId": imdb,
        "path": path,
        "tags": list(tags),
        "certification": cert,
        "monitored": False,
        "qualityProfileId": 1,
        **extra,
    }


def jf_series(item_id, name, path, **pids):
    return {"Id": item_id, "Name": name, "Type": "Series", "Path": path, "ProviderIds": pids}


def jf_movie(item_id, name, file_path, **pids):
    return {
        "Id": item_id,
        "Name": name,
        "Type": "Movie",
        "Path": file_path,
        "MediaSources": [{"Path": file_path}],
        "ProviderIds": pids,
    }


def cfg(mode="dry_run", cert_fallback=False) -> AppConfig:
    return AppConfig.model_validate({"arr_sync": {"mode": mode, "certification_fallback": cert_fallback}})


def make_sync(sonarr_fakes=(), radarr_fakes=(), *, mode="dry_run", cert_fallback=False, read_only=None):
    guarded = (mode != "live") if read_only is None else read_only
    sonarrs = [client_for(f, f"s{i}", read_only=guarded) for i, f in enumerate(sonarr_fakes)]
    radarrs = [client_for(f, f"r{i}", read_only=guarded) for i, f in enumerate(radarr_fakes)]
    sync = ArrTagSync(cfg(mode, cert_fallback), sonarrs, radarrs)
    sync.prepare()
    return sync


# ── the read-only guard ─────────────────────────────────────────────────────
def test_guard_self_test_passes():
    assert readonly.self_test() == []


def test_guard_self_test_can_fail(monkeypatch):
    """A self-test that cannot fail proves nothing: open the guard and it must notice."""
    monkeypatch.setattr(
        readonly, "SAFE_METHODS", frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE", "PATCH"})
    )
    failures = readonly.self_test()
    assert any("POST was NOT blocked" in f for f in failures)
    assert any("reached the inner transport" in f for f in failures)
    assert any("network layer" in f for f in failures)


def test_guard_blocks_before_sending():
    fake = FakeArr("sonarr", [])
    client = client_for(fake, "s", read_only=True)
    assert client._get("/tag") == []
    with pytest.raises(ReadOnlyViolation):
        client.create_tag("xt-1080p")
    with pytest.raises(ReadOnlyViolation):
        client.edit_tags(1, [1], "add")
    assert fake.writes == []


@pytest.mark.parametrize("mode, guarded", [("dry_run", True), ("live", False)])
def test_arr_clients_are_read_only_unless_live(mode, guarded):
    c = AppConfig.model_validate(
        {
            "arr_sync": {"mode": mode},
            "sonarr": {"instances": [ArrInstance(name="a", url="http://a").model_dump()]},
            "radarr": {"instances": [ArrInstance(name="b", url="http://b").model_dump()]},
        }
    )
    jf, sonarrs, radarrs = pipeline._clients_from_config(c)
    try:
        for client in sonarrs + radarrs:
            assert isinstance(client._client._transport, ReadOnlyTransport) is guarded
        assert not isinstance(jf._client._transport, ReadOnlyTransport)  # a scan still writes Jellyfin
    finally:
        pipeline._close_clients(jf, sonarrs, radarrs)


def test_dry_run_guards_jellyfin_too():
    guards: list = []
    jf, sonarrs, radarrs = pipeline._clients_from_config(cfg("live"), read_only=True, guards=guards)
    try:
        assert isinstance(jf._client._transport, ReadOnlyTransport)
        assert len(guards) == 1  # no *arr instances configured
    finally:
        pipeline._close_clients(jf, sonarrs, radarrs)


def test_shipped_default_is_dry_run_without_rating_fallback():
    assert AppConfig().arr_sync.mode == "dry_run"
    assert AppConfig().arr_sync.certification_fallback is False


def test_default_tag_config_hash_is_unchanged_by_b5():
    """An upgrade must not force a full rescan: the defaults hash exactly as before B5."""
    assert pipeline._tag_config_hash(AppConfig()) == "3163f57ce472c152"
    assert pipeline._tag_config_hash(cfg("live")) != "3163f57ce472c152"
    assert pipeline._tag_config_hash(cfg("dry_run", cert_fallback=True)) != "3163f57ce472c152"


# ── trap 1: an *arr id is per instance ──────────────────────────────────────
def test_same_id_on_two_instances_is_two_shows_and_only_the_owner_is_written():
    general = FakeArr("sonarr", [series(42, "The Expanse", 280619, "/media/core/tv/The Expanse")])
    anime = FakeArr("sonarr", [series(42, "Cowboy Bebop", 76885, "/media/core/tv/anime/Cowboy Bebop")])
    sync = make_sync([general, anime], mode="live")
    item = jf_series("i1", "The Expanse", "/media/core/tv/The Expanse", Tvdb="280619")
    sync.resolve_all([item])
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert general.objects[42]["tags"] == [general.tags["xt-1080p"]]
    assert anime.objects[42]["tags"] == []
    assert anime.writes == []


def test_resolution_never_uses_another_instances_id():
    """The old code sent ONE id to every instance; resolution must be per instance."""
    a = FakeArr("radarr", [movie(7, "Alien", 348, "/media/core/movies/Alien (1979)")])
    b = FakeArr("radarr", [movie(7, "Heat", 949, "/media/core/movies/Heat (1995)")])
    sync = make_sync(radarr_fakes=[a, b])
    item = jf_movie("i1", "Alien", "/media/core/movies/Alien (1979)/Alien.mkv", Tmdb="348")
    assert InstanceIndex(sync.clients["radarr"][0]).resolve(item).status == OWNED
    assert InstanceIndex(sync.clients["radarr"][1]).resolve(item).status == NONE


def test_hd_and_4k_copies_each_get_their_own_items_tags():
    """Measured: 133 films match by id in BOTH radarr/general and radarr/4k."""
    hd = FakeArr("radarr", [movie(1, "Mission Impossible", 954, "/media/core/movies/MI (1996)")])
    uhd = FakeArr("radarr", [movie(9, "Mission Impossible", 954, "/media/4K/movies/MI (1996)")])
    sync = make_sync(radarr_fakes=[hd, uhd], mode="live")
    hd_item = jf_movie("h", "Mission: Impossible", "/media/core/movies/MI (1996)/MI.1080p.mkv", Tmdb="954")
    uhd_item = jf_movie("u", "Mission: Impossible", "/media/4K/movies/MI (1996)/MI.2160p.mkv", Tmdb="954")
    sync.resolve_all([hd_item, uhd_item])
    sync.sync_item(hd_item, {"radarr": ["xt-1080p"]})
    sync.sync_item(uhd_item, {"radarr": ["xt-4K"]})
    assert hd.objects[1]["tags"] == [hd.tags["xt-1080p"]] and "xt-4k" not in hd.tags
    assert uhd.objects[9]["tags"] == [uhd.tags["xt-4k"]] and "xt-1080p" not in uhd.tags
    assert sync.stats["radarr/r0"].elsewhere == 1 and sync.stats["radarr/r1"].elsewhere == 1


# ── trap 2: nothing is created in a dry run ─────────────────────────────────
def test_dry_run_counts_labels_it_would_create_without_creating_them():
    fake = FakeArr("sonarr", [series(1, "Firefly", 78874, "/media/tv/Firefly")], tags={"anime": 1})
    sync = make_sync([fake])
    item = jf_series("i", "Firefly", "/media/tv/Firefly", Tvdb="78874")
    sync.resolve_all([item])
    sync.sync_item(item, {"sonarr": ["xt-1080p", "xt-EN"]})
    st = sync.stats["sonarr/s0"]
    assert st.would_change == 1 and dict(st.labels_to_create) == {"xt-1080p": 1, "xt-en": 1}
    assert fake.writes == [] and fake.tags == {"anime": 1}


def test_dry_run_is_safe_even_without_the_guard():
    """The guard is belt and braces: the dry-run path itself never writes."""
    fake = FakeArr("radarr", [movie(1, "Serenity", 16320, "/media/movies/Serenity (2005)", tags=[5])])
    sync = make_sync(radarr_fakes=[fake], mode="dry_run", read_only=False)
    item = jf_movie("i", "Serenity", "/media/movies/Serenity (2005)/S.mkv", Tmdb="16320")
    sync.resolve_all([item])
    sync.sync_item(item, {"radarr": ["xt-1080p"]})
    assert fake.writes == []


# ── trap 3: never a whole-object PUT, never a stale revert ──────────────────
def test_writes_go_through_the_editor_only():
    fake = FakeArr("sonarr", [series(1, "Firefly", 78874, "/media/tv/Firefly")], tags={"xt-720p": 3})
    fake.objects[1]["tags"] = [3]
    sync = make_sync([fake], mode="live")
    item = jf_series("i", "Firefly", "/media/tv/Firefly", Tvdb="78874")
    sync.resolve_all([item])
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert ("PUT", "/series/1") not in fake.requests
    assert [r for r in fake.writes if r[0] == "PUT"] == [("PUT", "/series/editor"), ("PUT", "/series/editor")]
    assert fake.objects[1]["tags"] == [fake.tags["xt-1080p"]]
    assert sync.stats["sonarr/s0"].written == 1


def test_an_operator_edit_after_preload_is_not_reverted():
    """Demonstrated on dev: the old PUT /series/{id} of a cached copy reverted this."""
    fake = FakeArr("sonarr", [series(1, "Firefly", 78874, "/media/tv/Firefly")])
    sync = make_sync([fake], mode="live")
    fake.objects[1]["monitored"] = True  # the operator, after the preload
    fake.objects[1]["qualityProfileId"] = 3
    item = jf_series("i", "Firefly", "/media/tv/Firefly", Tvdb="78874")
    sync.resolve_all([item])
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert fake.objects[1]["monitored"] is True and fake.objects[1]["qualityProfileId"] == 3
    assert sync.stats["sonarr/s0"].written == 1 and sync.halted is None


def test_an_object_deleted_or_repurposed_since_preload_is_not_written():
    fake = FakeArr("sonarr", [series(1, "Firefly", 78874, "/media/tv/Firefly"), series(2, "Heat", 1, "/media/tv/Heat")])
    sync = make_sync([fake], mode="live")
    del fake.objects[1]
    fake.objects[2] = series(2, "Other", 99, "/media/tv/Other")
    a = jf_series("a", "Firefly", "/media/tv/Firefly", Tvdb="78874")
    b = jf_series("b", "Heat", "/media/tv/Heat", Tvdb="1")
    sync.resolve_all([a, b])
    sync.sync_item(a, {"sonarr": ["xt-1080p"]})
    sync.sync_item(b, {"sonarr": ["xt-1080p"]})
    assert sync.stats["sonarr/s0"].changed_since_preload == 2
    assert fake.writes == [] and sync.halted is None


# ── trap 4: match quality ───────────────────────────────────────────────────
def test_ids_pointing_at_two_series_in_one_instance_write_nothing():
    """Production: 'Cunk on Britain' — its Tvdb/Tmdb/Imdb point at two different series."""
    fake = FakeArr(
        "sonarr",
        [
            series(921, "Cunk on Britain", 339732, "/media/tv/Cunk on Britain", imdb="tt8000000"),
            series(344, "Cunk on Earth", 414217, "/media/tv/Cunk on Earth", imdb="tt9000000"),
        ],
    )
    sync = make_sync([fake], mode="live")
    item = jf_series("i", "Cunk on Britain", "/media/tv/Cunk on Britain", Tvdb="339732", Imdb="tt9000000")
    sync.resolve_all([item])
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert InstanceIndex(sync.clients["sonarr"][0]).resolve(item).candidates == (344, 921)
    assert sync.stats["sonarr/s0"].ambiguous == 1 and fake.writes == []
    assert sync.items["sonarr_unowned_ambiguous"] == 1


def test_duplicate_provider_id_inside_one_instance_is_ambiguous():
    fake = FakeArr("radarr", [movie(1, "A", 5, "/m/A"), movie(2, "A again", 5, "/m/A")])
    sync = make_sync(radarr_fakes=[fake])
    item = jf_movie("i", "A", "/m/A/a.mkv", Tmdb="5")
    assert InstanceIndex(sync.clients["radarr"][0]).resolve(item).status == AMBIGUOUS


def test_an_object_claimed_by_two_items_is_refused():
    fake = FakeArr("radarr", [movie(1, "Film", 5, "/m/Film")])
    sync = make_sync(radarr_fakes=[fake], mode="live")
    a = jf_movie("a", "Film", "/m/Film/cut1.mkv", Tmdb="5")
    b = jf_movie("b", "Film", "/m/Film/cut2.mkv", Tmdb="5")
    sync.resolve_all([a, b])
    sync.sync_item(a, {"radarr": ["xt-1080p"]})
    sync.sync_item(b, {"radarr": ["xt-4k"]})
    assert sync.stats["radarr/r0"].claimed_twice == 1 and fake.writes == []


def test_match_accounting_by_key_and_by_reason():
    s = FakeArr(
        "sonarr",
        [
            series(1, "By tvdb", 100, "/tv/A"),
            series(2, "By imdb", 0, "/tv/B", imdb="tt0000002"),
            series(3, "Other folder", 300, "/tv/elsewhere/C"),
        ],
    )
    sync = make_sync([s])
    items = [
        jf_series("a", "A", "/tv/A", Tvdb="100"),
        jf_series("b", "B", "/tv/B", Imdb="tt0000002"),
        jf_series("c", "C", "/tv/C", Tvdb="300"),
        jf_series("d", "D", "/tv/D", Tvdb="999"),
        jf_series("e", "E", "/tv/E", AniDB="5813"),
        jf_series("f", "F", "/tv/F"),
    ]
    sync.resolve_all(items)
    st = sync.stats["sonarr/s0"]
    assert dict(st.matched_by) == {"Tvdb": 2, "Imdb": 1}
    assert (st.owned, st.elsewhere, st.ambiguous) == (2, 1, 0)
    assert sync.items["sonarr_owned"] == 2
    assert sync.items["sonarr_unowned_elsewhere"] == 1
    assert sync.items["sonarr_unowned_not_in_any_instance"] == 1
    assert sync.items["sonarr_unowned_anidb_only"] == 1
    assert sync.items["sonarr_unowned_no_provider_ids"] == 1


def test_a_series_never_matches_radarr_and_a_film_never_matches_sonarr():
    s = FakeArr("sonarr", [series(1, "X", 100, "/x", tmdb=55)])
    r = FakeArr("radarr", [movie(1, "X", 55, "/x")])
    sync = make_sync([s], [r])
    sync.resolve_all([jf_series("a", "X", "/x", Tmdb="55")])
    assert sync.stats["sonarr/s0"].owned == 1 and sync.stats["radarr/r0"].owned == 0


def test_item_folders():
    assert item_folders(jf_movie("m", "M", "/a/b/M (1)/M.mkv")) >= {"/a/b/M (1)/M.mkv", "/a/b/M (1)"}
    assert "/a/tv/S" in item_folders(jf_series("s", "S", "/a/tv/S/"))
    assert item_folders({"Path": None}) == set()


# ── labels ──────────────────────────────────────────────────────────────────
def test_labels_are_matched_lowercase_and_an_existing_label_is_not_posted():
    fake = FakeArr("sonarr", [series(1, "F", 1, "/tv/F")], tags={"xt-hevc": 7})
    sync = make_sync([fake], mode="live")
    item = jf_series("i", "F", "/tv/F", Tvdb="1")
    sync.resolve_all([item])
    sync.sync_item(item, {"sonarr": ["xt-HEVC"]})
    assert ("POST", "/tag") not in fake.requests and fake.objects[1]["tags"] == [7]


def test_a_stored_label_is_compared_lowercase():
    """Both *arrs store lowercase today; a label stored otherwise must still match."""
    fake = FakeArr("sonarr", [series(1, "F", 1, "/tv/F")], tags={"XT-Hevc": 7})
    client = client_for(fake, "s")
    assert client.tags == {"xt-hevc": 7}
    assert plan_tags(client, [], ["xt-HEVC"], ("xt-",)).desired_ids == [7]


def test_create_tag_sends_the_label_lowercased():
    """Measured: POSTing `xt-HEVC` when `xt-hevc` exists is a 409; lowercase is a lookup."""
    fake = FakeArr("sonarr", [], tags={"xt-hevc": 7})
    client = client_for(fake, "s")
    client._tags = {}  # a stale map: the label exists but the client does not know
    assert client.create_tag("xt-HEVC") == 7


def test_create_tag_409_resolves_to_the_existing_label():
    fake = FakeArr("sonarr", [], tags={"xt-hevc": 7})
    client = client_for(fake, "s")
    client._tags = {}
    fake.fail_status["POST"] = 409  # e.g. created by someone else since the load
    assert client.create_tag("xt-hevc") == 7


def test_radarr_refuses_labels_outside_its_charset_and_sonarr_does_not():
    r = FakeArr("radarr", [movie(1, "F", 1, "/m/F")])
    s = FakeArr("sonarr", [series(1, "S", 1, "/tv/S")])
    sync = make_sync([s], [r], mode="live")
    film = jf_movie("f", "F", "/m/F/f.mkv", Tmdb="1")
    show = jf_series("s", "S", "/tv/S", Tvdb="1")
    sync.resolve_all([film, show])
    sync.sync_item(film, {"radarr": ["xt-H.265", "xt-DD+ Atmos", "xt-1080p"]})
    sync.sync_item(show, {"sonarr": ["xt-H.265", "xt-1080p"]})
    assert set(r.tags) == {"xt-1080p"}
    assert dict(sync.stats["radarr/r0"].labels_rejected) == {"xt-h.265": 1, "xt-dd+ atmos": 1}
    assert set(s.tags) == {"xt-h.265", "xt-1080p"}
    assert sync.halted is None


def test_only_managed_tags_are_ever_removed():
    fake = FakeArr("sonarr", [], tags={"anime": 1, "xt-720p": 2, "mf-720p": 3, "xtra": 4})
    client = client_for(fake, "s")
    plan = plan_tags(client, [1, 2, 3, 4], ["xt-1080p"], ("xt-", "mf-"))
    assert sorted(plan.remove) == [2, 3] and plan.create == ["xt-1080p"]


def test_a_desired_label_outside_the_managed_prefix_is_refused():
    client = client_for(FakeArr("sonarr", []), "s")
    with pytest.raises(ValueError, match="outside the managed prefixes"):
        plan_tags(client, [], ["anime"], ("xt-",))


# ── the read-back ───────────────────────────────────────────────────────────
def test_readback_problems_both_directions():
    managed = {10, 11}.__contains__
    before = {"id": 1, "monitored": False, "tags": [1, 10], "statistics": {"sizeOnDisk": 1}}
    good = {"id": 1, "monitored": False, "tags": [1, 11], "statistics": {"sizeOnDisk": 2}}
    assert readback_problems(before, good, {11}, managed) == []
    assert readback_problems(before, good, {10}, managed)  # wrong managed set
    assert readback_problems(before, {**good, "tags": [11]}, {11}, managed)  # user tag lost
    assert readback_problems(before, {**good, "monitored": True}, {11}, managed) == ["field changed: .monitored"]
    nested = {**good, "seasons": [{"monitored": True}]}
    assert readback_problems({**before, "seasons": [{"monitored": False}]}, nested, {11}, managed) == [
        "field changed: .seasons[0].monitored"
    ]


def test_a_readback_mismatch_halts_every_further_write():
    fake = FakeArr("sonarr", [series(1, "A", 1, "/tv/A"), series(2, "B", 2, "/tv/B")])
    fake.on_edit = lambda obj: obj.update(qualityProfileId=99)  # the *arr changed something else
    sync = make_sync([fake], mode="live")
    a, b = jf_series("a", "A", "/tv/A", Tvdb="1"), jf_series("b", "B", "/tv/B", Tvdb="2")
    sync.resolve_all([a, b])
    sync.sync_item(a, {"sonarr": ["xt-1080p"]})
    assert sync.halted and "qualityProfileId" in sync.halted
    edits_after_a = len([r for r in fake.writes if r[1] == "/series/editor"])
    sync.sync_item(b, {"sonarr": ["xt-1080p"]})
    st = sync.stats["sonarr/s0"]
    assert (st.readback_failures, st.written, st.skipped_halted) == (1, 0, 1)
    assert len([r for r in fake.writes if r[1] == "/series/editor"]) == edits_after_a
    assert fake.objects[2]["tags"] == []


def test_a_lost_user_tag_is_caught_by_the_readback():
    fake = FakeArr("sonarr", [series(1, "A", 1, "/tv/A", tags=[1])], tags={"anime": 1})
    fake.on_edit = lambda obj: obj.update(tags=[t for t in obj["tags"] if t != 1])
    sync = make_sync([fake], mode="live")
    item = jf_series("a", "A", "/tv/A", Tvdb="1")
    sync.resolve_all([item])
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert sync.halted and "non-managed tags changed" in sync.halted


def test_volatile_fields_do_not_halt():
    fake = FakeArr("radarr", [movie(1, "A", 1, "/m/A", statistics={"sizeOnDisk": 1}, lastSearchTime="t0")])
    fake.on_edit = lambda obj: obj.update(statistics={"sizeOnDisk": 2}, lastSearchTime="t1")
    sync = make_sync(radarr_fakes=[fake], mode="live")
    item = jf_movie("a", "A", "/m/A/a.mkv", Tmdb="1")
    sync.resolve_all([item])
    sync.sync_item(item, {"radarr": ["xt-1080p"]})
    assert sync.halted is None and sync.stats["radarr/r0"].written == 1


def test_a_write_error_halts():
    fake = FakeArr("sonarr", [series(1, "A", 1, "/tv/A")])
    sync = make_sync([fake], mode="live")
    item = jf_series("a", "A", "/tv/A", Tvdb="1")
    sync.resolve_all([item])
    fake.fail_status["POST"] = 503
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert sync.halted and sync.stats["sonarr/s0"].write_errors == 1
    assert [r for r in fake.writes if r[1] == "/series/editor"] == []


# ── trap 5: the certification fallback ──────────────────────────────────────
@pytest.mark.parametrize("enabled, used", [(False, ""), (True, "TV-14")])
def test_certification_fallback_is_counted_always_and_used_only_when_on(enabled, used):
    fake = FakeArr("sonarr", [series(1, "A", 1, "/tv/A", cert="TV-14")])
    sync = make_sync([fake], cert_fallback=enabled)
    item = jf_series("a", "A", "/tv/A", Tvdb="1")
    sync.resolve_all([item])
    assert sync.fallback_rating(item) == used
    assert sync.cert["blank_rating"] == 1 and sync.cert["would_fill"] == 1
    assert sync.fallback_rating({**item, "Id": "b", "OfficialRating": "TV-MA"}) == ""


def test_certification_ignores_an_object_in_another_folder():
    fake = FakeArr("radarr", [movie(1, "A", 1, "/m/4K/A", cert="R")])
    sync = make_sync(radarr_fakes=[fake], cert_fallback=True)
    item = jf_movie("a", "A", "/m/HD/A/a.mkv", Tmdb="1")
    sync.resolve_all([item])
    assert sync.fallback_rating(item) == ""


# ── the pipeline ────────────────────────────────────────────────────────────
class FakeJellyfin:
    def __init__(self, items=()):
        self.items = list(items)
        self.writes: list[tuple] = []

    def set_managed_tags(self, item_id, item, prefix, tags, fallback_rating="", legacy_prefixes=()):
        self.writes.append((item_id, tuple(tags), fallback_rating))

    def refresh_item(self, item_id):
        self.writes.append(("refresh", item_id))

    def get_items(self, library_ids=None):
        return self.items

    def close(self):
        pass


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _info():
    return MediaInfo("1080p", ["EN"], ["eng"], "H.265", None, [AudioTrack("EN", "DTS-HD")], [])


def test_process_one_item_in_dry_run_writes_jellyfin_but_no_arr(session, tmp_path):
    fake = FakeArr("sonarr", [series(1, "A", 1, str(tmp_path), cert="TV-14")])
    sync = make_sync([fake])
    item = jf_series("a", "A", str(tmp_path), Tvdb="1")
    sync.resolve_all([item])
    jf = FakeJellyfin()
    pipeline._process_one_item(jf, sync, session, cfg(), item, str(tmp_path / "e.mkv"), str(tmp_path), 1.0, _info())
    assert fake.writes == []
    assert jf.writes[0][2] == ""  # no fallback rating written to Jellyfin while it is off
    st = sync.stats["sonarr/s0"]
    assert st.would_change == 1 and set(st.labels_to_create) == {"xt-1080p", "xt-h.265", "xt-en", "xt-dts-hd"}


def test_process_one_item_live_writes_the_owner(session, tmp_path):
    fake = FakeArr("sonarr", [series(1, "A", 1, str(tmp_path))])
    sync = make_sync([fake], mode="live")
    item = jf_series("a", "A", str(tmp_path), Tvdb="1")
    sync.resolve_all([item])
    pipeline._process_one_item(
        FakeJellyfin(), sync, session, cfg("live"), item, str(tmp_path / "e.mkv"), str(tmp_path), 1.0, _info()
    )
    labels = {fake_id: label for label, fake_id in fake.tags.items()}
    assert {labels[t] for t in fake.objects[1]["tags"]} == {"xt-1080p", "xt-h.265", "xt-en", "xt-dts-hd"}


def test_index_dry_run_reads_a_copied_index_read_only(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(
        MediaState(
            item_id="jellyfin:a",
            source="jellyfin",
            resolution="4K",
            video_codec="H.265",
            hdr_type="HDR10+",
            audio_tracks=json.dumps([{"lang": "EN", "codec": "TrueHD Atmos"}]),
            subtitle_tracks=json.dumps([{"lang": "EN", "format": "PGS", "embedded": True}]),
        )
    )
    s.commit()
    s.close()
    engine.dispose()
    before = db.read_bytes()

    radarr = FakeArr("radarr", [movie(1, "A", 1, "/m/A", cert="PG-13"), movie(2, "B", 2, "/m/B")])
    items = [jf_movie("a", "A", "/m/A/a.mkv", Tmdb="1"), jf_movie("b", "B", "/m/B/b.mkv", Tmdb="2")]

    def fake_clients(c, *, read_only=False, guards=None):
        assert read_only is True
        client = client_for(radarr, "r0", read_only=True)
        return FakeJellyfin(items), [], [client]

    monkeypatch.setattr(pipeline, "_clients_from_config", fake_clients)
    report = pipeline.run_arr_dry_run(cfg(), db_path=db, store=False)
    st = report["instances"]["radarr/r0"]
    assert st["owned"] == 2 and st["synced"] == 1 and st["would_change"] == 1
    assert st["labels_rejected"] == {"xt-h.265": 1, "xt-hdr10+": 1, "xt-truehd atmos": 1}
    assert set(st["labels_to_create"]) == {"xt-4k", "xt-en"}
    assert report["items"]["no_probe_record"] == 1
    assert report["certification_fallback"]["would_fill"] == 1
    assert radarr.writes == [] and db.read_bytes() == before
