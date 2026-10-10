"""Roadmap B26: on Jellyfin 12.2 a recursive Movie/Series ``/Items`` listing
collapses every item in a collection into its BoxSet unless the request
carries ``CollapseBoxSetItems=false``. The fake server below reproduces that:
it replaces each collected film with its BoxSet unless the request opts out,
on every listing endpoint a real server would collapse (paged and
``Limit=0``). Every call site that lists Movie/Series recursively must send
the parameter -- the scan, the webhook folder lookup, U2's completeness pass
(its pages and its recount), and the two Preview sample helpers.
"""

from __future__ import annotations

import httpx

from app.clients.jellyfin import COLLAPSE_BOX_SET_ITEMS, JellyfinClient
from app.deleted_items import complete_listing

BOXSET = {"Id": "boxset-1", "Type": "BoxSet", "Name": "A Collection"}
FILM_A = {"Id": "film-a", "Type": "Movie", "Name": "Film A", "ImageTags": {"Primary": "x"}}
FILM_B = {"Id": "film-b", "Type": "Movie", "Name": "Film B", "ImageTags": {"Primary": "x"}}
SERIES = {"Id": "series-1", "Type": "Series", "Name": "A Series", "ImageTags": {"Primary": "x"}}


class CollapsingJellyfin:
    """Collapses FILM_A and FILM_B into BOXSET unless asked not to.

    ``opt_out`` records whether each request carried the right value, so a
    test can assert the client always sends it -- never only that the count
    came out right, which a server with no collection could pass by luck.
    """

    def __init__(self):
        self.requests: list[dict] = []

    def _opted_out(self, params: dict) -> bool:
        return params.get("CollapseBoxSetItems") == COLLAPSE_BOX_SET_ITEMS

    def _collapse(self, items: list[dict], params: dict) -> list[dict]:
        if self._opted_out(params):
            return items
        out = [i for i in items if i["Id"] not in (FILM_A["Id"], FILM_B["Id"])]
        if any(i["Id"] in (FILM_A["Id"], FILM_B["Id"]) for i in items):
            out.append(BOXSET)
        return out

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        if request.method != "GET" or request.url.path != "/Items":
            return httpx.Response(404)
        include = set((params.get("IncludeItemTypes") or "").split(","))
        pool = []
        if "Movie" in include:
            pool += [FILM_A, FILM_B]
        if "Series" in include:
            pool += [SERIES]
        items = self._collapse(pool, params)
        if params.get("Limit") == "0":
            return httpx.Response(200, json={"Items": [], "TotalRecordCount": len(items)})
        return httpx.Response(200, json={"Items": items, "TotalRecordCount": len(items)})

    def client(self) -> JellyfinClient:
        return JellyfinClient("http://jf", "key", transport=httpx.MockTransport(self.handle))


# ── site 1: the scan's get_items() / _fetch_items(), and B8's list_item_paths() ──


def test_get_items_sees_the_films_not_the_boxset():
    fake = CollapsingJellyfin()
    with fake.client() as jf:
        items = jf.get_items()
    ids = {i["Id"] for i in items}
    assert ids == {FILM_A["Id"], FILM_B["Id"], SERIES["Id"]}
    assert BOXSET["Id"] not in ids


def test_list_item_paths_also_sends_it():
    fake = CollapsingJellyfin()
    with fake.client() as jf:
        items = jf.list_item_paths("Movie")
    ids = {i["Id"] for i in items}
    assert ids == {FILM_A["Id"], FILM_B["Id"]}


# ── sites 3/4: the Preview samples ──────────────────────────────────────────


def test_get_sample_items_excludes_the_boxset():
    fake = CollapsingJellyfin()
    with fake.client() as jf:
        items = jf.get_sample_items(limit=12)
    ids = {i["Id"] for i in items}
    assert ids == {FILM_A["Id"], FILM_B["Id"], SERIES["Id"]}


def test_get_diverse_sample_items_excludes_the_boxset():
    fake = CollapsingJellyfin()
    with fake.client() as jf:
        items = jf.get_diverse_sample_items(count=4)
    ids = {i["Id"] for i in items}
    assert BOXSET["Id"] not in ids
    assert FILM_A["Id"] in ids and FILM_B["Id"] in ids


# ── site 2: U2's complete_listing() -- pages AND the Limit=0 recount ───────


def test_complete_listing_pages_exclude_the_boxset():
    fake = CollapsingJellyfin()
    with fake.client() as jf:
        listing = complete_listing(jf, page_size=10)
    ids = {i["Id"] for i in listing.items}
    assert ids == {FILM_A["Id"], FILM_B["Id"], SERIES["Id"]}
    assert listing.total == 3
    assert listing.recount == 3


def test_complete_listing_requests_all_carry_the_param():
    """Both the paged requests and the Limit=0 recount must opt out -- if either
    forgets it, the two populations disagree and a real run would abort."""
    fake = CollapsingJellyfin()
    with fake.client() as jf:
        complete_listing(jf, page_size=10)
    item_requests = [r for r in fake.requests if r.get("IncludeItemTypes") == "Movie,Series"]
    assert item_requests, "no Movie,Series /Items request was made"
    assert all(r.get("CollapseBoxSetItems") == COLLAPSE_BOX_SET_ITEMS for r in item_requests)
    # At least one page request and the recount (Limit=0) must both appear.
    assert any(r.get("Limit") == "0" for r in item_requests)
    assert any(r.get("Limit") != "0" for r in item_requests)
