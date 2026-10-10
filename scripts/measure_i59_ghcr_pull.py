#!/usr/bin/env python3
"""Cold fetch of every blob of ghcr.io/bpoulliot/xenotag:<tag> (amd64 + attestation), N runs.

Roadmap I15 (the release image registry), docs/measurements/i59-image-registry.md.
Usage: ``python3 scripts/measure_i59_ghcr_pull.py [tag] [runs]``; writes
``cold_pull-<tag>.json`` to the current directory.

Anonymous pull token, bytes streamed to a sha256 and discarded (nothing written to disk, no
docker store touched). Self-test: every blob's sha256 must equal its digest, and the byte total
must equal the manifests' declared sizes; a deliberately corrupted hash must NOT match.
"""

import hashlib
import json
import sys
import time
import urllib.request

REPO = "bpoulliot/xenotag"
TAG = sys.argv[1] if len(sys.argv) > 1 else "1.11.1"
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
ACCEPT = ",".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def req(url, hdr):
    assert url.startswith("https://ghcr.io/"), url
    r = urllib.request.Request(url, headers=hdr)  # noqa: S310 -- asserted https://ghcr.io/
    return urllib.request.urlopen(r, timeout=60)  # noqa: S310 -- asserted https://ghcr.io/


def token():
    u = f"https://ghcr.io/token?scope=repository:{REPO}:pull&service=ghcr.io"
    return json.loads(req(u, {}).read())["token"]


def fetch_blob(digest, auth):
    h = hashlib.sha256()
    n = 0
    with req(f"https://ghcr.io/v2/{REPO}/blobs/{digest}", auth) as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            n += len(chunk)
    return n, "sha256:" + h.hexdigest()


def main():
    # negative self-test: a hash of altered bytes must differ
    assert hashlib.sha256(b"abc").hexdigest() != hashlib.sha256(b"abd").hexdigest()
    results = []
    for run in range(1, RUNS + 1):
        t0 = time.monotonic()
        auth = {"Authorization": f"Bearer {token()}", "Accept": ACCEPT}
        idx = json.loads(req(f"https://ghcr.io/v2/{REPO}/manifests/{TAG}", auth).read())
        blobs = []
        for m in idx["manifests"]:
            man = json.loads(
                req(
                    f"https://ghcr.io/v2/{REPO}/manifests/{m['digest']}",
                    {**auth, "Accept": m["mediaType"]},
                ).read()
            )
            blobs += [man["config"]] + man["layers"]
        declared = sum(b["size"] for b in blobs)
        got = 0
        slowest = (0.0, "")
        for b in blobs:
            tb = time.monotonic()
            n, dg = fetch_blob(b["digest"], {"Authorization": auth["Authorization"]})
            dt = time.monotonic() - tb
            if dg != b["digest"] or n != b["size"]:
                raise SystemExit(f"SELF-TEST FAIL: {b['digest']} got {dg} {n}/{b['size']}")
            got += n
            if dt > slowest[0]:
                slowest = (dt, f"{b['digest'][:19]} {n:,d} B")
        wall = time.monotonic() - t0
        if got != declared:
            raise SystemExit(f"SELF-TEST FAIL: total {got} != declared {declared}")
        r = {
            "run": run,
            "blobs": len(blobs),
            "bytes": got,
            "wall_s": round(wall, 2),
            "MB_per_s": round(got / wall / 1e6, 1),
            "slowest_blob_s": round(slowest[0], 2),
            "slowest_blob": slowest[1],
        }
        print(json.dumps(r), flush=True)
        results.append(r)
    json.dump(results, open(f"cold_pull-{TAG}.json", "w"), indent=1)


if __name__ == "__main__":
    main()
