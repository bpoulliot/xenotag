# I59 decision 4: where xenotag's release image lives (measured 2026-10-10)

Roadmap item **I15**, filed from the operator's ~/docker backlog item I59, decision 4. Should the release
image stay on **ghcr**, move to the **Gitea registry** (`registry.bitmapserv.org`), or go to **both**?
This is a measurement only. Nothing was pushed to any registry, no workflow was dispatched, no token was
minted, and no workflow or compose file was changed.

## Verdict

- **ghcr has to stay whatever else is decided.** The repo is public, and the README's install lines
  (`README.md` lines 116, 137, 549 and 633) pull `ghcr.io/bpoulliot/xenotag`. This Gitea **cannot serve an
  anonymous pull at all**: it runs with `REQUIRE_SIGNIN_VIEW=true`. Its `/v2/token` endpoint refuses an
  anonymous pull scope with `401`, whatever the repository, and the API answers *"Only signed in user is
  allowed to call APIs"*. A Gitea-only image (option B) would break every public install.
- **The Cloudflare 100 MB cap does not apply to `registry.bitmapserv.org`, but it does apply to
  `git.bitmapserv.org`.** The registry name is a DNS-only (grey-cloud) record pointing at the WAN address.
  It is served by SWAG's nginx with `client_max_body_size 0`, with no Cloudflare in the path.
  `git.bitmapserv.org` is Cloudflare-proxied, and it also answers `/v2/`. A push to that name carries the
  image's largest layer, **171.5 MB** in one request body, which is more than the cap.
- **Recommendation: (A) stay on ghcr.** Each alternative adds a secret, a moving part, or a new way for a
  release to fail. None of them has a consumer today: production pulls ghcr, and a cold pull from ghcr
  takes 7.5–9.2 s on this host. Revisit (D), a host-side mirror, only if the shared `release-image.yml` (I59
  "Then") lands for van1sh and sightline and the operator wants every deploy pinned to
  `registry.bitmapserv.org/…@sha256`.

## 1. ghcr today

**Instrument:** the registry API, with an anonymous pull token from `https://ghcr.io/token`. The package is
public; the token was issued without credentials. Manifests were fetched by digest, and each body's sha256
was checked against its digest. The deployed tag is `1.11.1` (`= latest`). Index digest:
`sha256:b9308049e7c0…cb29c`. That is the digest the production container's image carries locally.

The index has 2 manifests: `linux/amd64` and a buildx attestation manifest (`unknown/unknown`, SBOM and
SLSA provenance).

| # | Compressed bytes | What (from `docker history` of the local image) |
|--:|--:|---|
| 0 | 29,843,350 | Debian trixie base |
| 1 | 1,294,052 | python image: ca-certificates, netbase, tzdata |
| 2 | 12,121,637 | python image: CPython 3.12.15 build |
| 3 | 249 | python image: symlinks |
| **4** | **171,510,771** | `apt-get upgrade` + `ffmpeg`, `curl`, `fonts-dejavu-core` (451 MB uncompressed) |
| 5–6 | 450 | `WORKDIR`, `COPY requirements.txt` |
| 7 | 34,006,008 | `pip install -r requirements.txt` (104 MB uncompressed) |
| 8–10 | 1,230,620 | `VERSION`, `app/`, `useradd` + `chown` |
| config | 9,344 | |
| attestation | 3,927,567 | SPDX SBOM 3,898,207 + provenance 29,360 |

- **amd64 image: 250,007,137 B compressed** (11 layers + config). Local image `Size`: 677,011,544 B.
- **Largest single layer: 171,510,771 B** (163.6 MiB), the ffmpeg layer.
- **All blobs of the tag (amd64 + attestation): 253,944,050 B in 15 blobs.**

**It does not stay cached between releases.** The new compressed bytes per release (blobs not in any earlier
tag) are 38.8 MB (1.8.0), 5.1 MB (1.9.0), 39.0 MB (1.10.0), 253.8 MB (1.11.0) and 210.7 MB (1.11.1). The
ffmpeg layer is rebuilt whenever `apt-get upgrade` sees a new Debian package, and its digest changed in 4 of
the last 5 releases. That is **547 MB of new blobs in 5 releases** (2026-09-26 to 10-10, about 110 MB per
release). Every registry that keeps every version grows at that rate.

### Pull cost on this host

**Instrument:** `scripts/measure_i59_ghcr_pull.py 1.11.1 3`. It does a cold fetch of all 15 blobs over the
registry API with a fresh anonymous token each run. Each blob is streamed into a sha256 and discarded, with no
disk write and nothing in the Docker store. **Self-test:** each blob's hash must equal its digest, the byte
total must equal the manifests' declared sizes, and a deliberately altered input must hash differently. Any
failure aborts the run without reporting.

| Run | Bytes | Wall | Throughput | Slowest blob |
|--:|--:|--:|--:|---|
| 1 | 253,944,050 | 9.22 s | 27.6 MB/s | layer 4, 2.76 s |
| 2 | 253,944,050 | 7.51 s | 33.8 MB/s | layer 4, 1.84 s |
| 3 | 253,944,050 | 7.60 s | 33.4 MB/s | layer 4, 1.84 s |

The sample is n = 3, back-to-back, at about 13:20 MDT. A fourth run, made with the committed version of the
script after a lint-only edit, took 8.95 s at 28.4 MB/s. Timing uses `time.monotonic()`. The wall time
includes the token and manifest round trips. This is the WAN download from ghcr's CDN. `docker pull` adds
decompression and extraction, so it is not included. A pull on release day reuses the unchanged layers, so it
fetches less than this.

### Publish cost

**Instrument:** `gh run list` and the REST jobs API, for the last 5 runs of each workflow.

| Release | `docker-publish` run | job | queue | `Build and push` step | export + push (buildkit `#19`) | `pushing layers` |
|---|--:|--:|--:|--:|--:|--:|
| v1.11.1 | 51 s | 48 s | 3 s | 28 s | 12.3 s | 4.6 s |
| v1.11.0 | 57 s | 53 s | 4 s | 33 s | 13.8 s | 5.7 s |
| v1.10.0 | 55 s | 51 s | 3 s | 26 s | 9.4 s | 3.6 s |
| v1.9.0 | 46 s | 42 s | 3 s | 21 s | 7.0 s | 2.1 s |
| v1.8.0 | 49 s | 44 s | 4 s | 27 s | 10.9 s | 3.7 s |

`Release` (the bump-and-tag workflow) took 13–17 s per run across the same five releases. Both workflows run
on GitHub-hosted `ubuntu-latest` runners, so ghcr is in the same network as the build. Pushing the layers took
2.1–5.7 s, including releases that uploaded about 250 MB of new blobs.

## 2. Gitea registry readiness

| Question | Answer | Instrument |
|---|---|---|
| Version | **1.27.3** | `/api/v1/version`. An anonymous call is refused (*"Only signed in user is allowed to call APIs"*), so this was read with the existing API token |
| `/v2/` | `401`, `Docker-Distribution-Api-Version: registry/2.0`, `Www-Authenticate: Bearer realm="https://registry.bitmapserv.org/v2/token",service="container_registry",scope="*"` and a Basic realm | `curl -D -` on `https://registry.bitmapserv.org/v2/` |
| Anonymous pull of a public package | **Impossible.** `GET /v2/token?scope=repository:<any>:pull` returns `401 UNAUTHORIZED` for an existing package (`bitmap/peakevents`) and a nonexistent one alike. `/v2/_catalog` also returns `401` | Registry API, no credentials |
| Why | `GITEA__service__REQUIRE_SIGNIN_VIEW=true` in the gitea service's environment (compose file). Owner `bitmap` has visibility `0` (public), but sign-in-required overrides that | `env` key names in the container; the `user` table |
| Packages listing over the API | Not possible with the tokens on hand. The session's token is refused with `required=[read:package], token scope=write:issue,write:repository,read:user`. That is a finding: no token on this host can read packages | `/api/v1/packages/bitmap` |
| What is in the registry | 1 package, `container/peakevents` (owner `bitmap`, no linked repo), 1 version, last pushed 2026-06-03. **0 cleanup rules.** 476 MB of blobs | Read-only `SELECT`s on the `package`, `package_version`, `package_cleanup_rule` and `package_blob` tables |
| `[packages]` settings | **There is no `[packages]` section in `app.ini` and no `GITEA__packages__*` variable**, so Gitea's defaults apply: enabled, no size limits (`LIMIT_TOTAL_OWNER_SIZE` / `LIMIT_SIZE_CONTAINER` default `-1`), local storage under `APP_DATA_PATH` | Section headers and key names in `app.ini`, plus env key names. No values outside those keys were read |
| Retention | Keep every version forever until a cleanup rule exists (0 do) | as above |
| Storage | `/data/gitea/packages`, a bind of `~/docker/gitea`, on the root NVMe: 914.8 G, **501.3 G free** (42 % used) | `df -h` in the container |
| Backups | `~/docker/gitea` is in the nightly restic set, and no exclude covers `gitea/gitea/packages`. Every pushed blob also goes to B2 (gzip layers do not compress further) | `scripts/restic-backup.sh` |

## 3. The push path from GitHub Actions

| Name | Resolves to | In front | Body limit |
|---|---|---|---|
| `registry.bitmapserv.org` | `216.82.9.150`, outside all 15 Cloudflare IPv4 ranges and equal to this host's egress address (`1.1.1.1/cdn-cgi/trace`) | **SWAG directly** (`Server: nginx`, no `cf-ray`; Let's Encrypt cert `CN=bitmapserv.org`) | `client_max_body_size 0`, `proxy_request_buffering off`, 900 s send/read timeouts (`registry.subdomain.conf`) |
| `git.bitmapserv.org` | `104.21.50.108`, `172.67.204.242` (Cloudflare) | **Cloudflare** (`server: cloudflare`, `cf-ray`) | Cloudflare's 100 MB request body on Free/Pro. The zone's plan was not read |

**How Docker uploads a blob:** each blob goes up as one request body, whichever client does it. Buildkit's
containerd pusher (what `docker/build-push-action` uses) sends `POST /v2/<name>/blobs/uploads/` and then one
`PUT …?digest=` that streams the whole blob. The classic engine client sends a single `PATCH` with the whole
blob, then a `PUT`. Neither splits a blob into chunks unless configured to. So:

- **Through `git.bitmapserv.org`: layer 4 (171.5 MB) exceeds 100 MB and would be refused at the edge, on
  every release that changes it** (4 of the last 5). Layers 0–3 and 5–10 would fit. The trap: Gitea builds
  its displayed `docker login` / `docker pull` host from `ROOT_URL`, which is `git.bitmapserv.org`. The package
  page therefore points users at the capped name. This comes from Gitea's source; the UI was not viewed.
- **Through `registry.bitmapserv.org`: no 100 MB cap.** SWAG passes the body unbuffered and unlimited. A
  GitHub-hosted runner's push would fit.

**Not determined without a push:**

- Whether WAN 443 is reachable from a GitHub runner. Only the hairpin from this host was tested, and it shows
  the port-forward works.
- Whether CrowdSec reacts to a burst of uploads from an Azure address.
- The real upload throughput from GitHub into this host's downlink. The download measured above was
  27.6–33.8 MB/s. At that rate one release's new blobs (5–254 MB) would take about 0.2–9 s. That is an
  estimate, not a measurement.
- Whether Gitea 1.27.3 accepts the buildx attestation manifest (`unknown/unknown`) inside an OCI index.
- Gitea's own write speed for a 171 MB blob.

## 4. The alternatives, as a written diff (none applied)

### (A) Stay on ghcr: no change

Moving parts: none new. Secrets: none new; the workflow uses the job's `GITHUB_TOKEN`. If Gitea is down at
release time: nothing happens. Public image: yes, as today.

### (B) Gitea only

```diff
 # .github/workflows/docker-publish.yml
     permissions:
       contents: read
-      packages: write
       id-token: write
 ...
-      - name: Log in to GitHub Container Registry
+      - name: Log in to the Gitea registry
         uses: docker/login-action@v4
         with:
-          registry: ghcr.io
-          username: ${{ github.actor }}
-          password: ${{ secrets.GITHUB_TOKEN }}
+          registry: registry.bitmapserv.org   # NEVER git.bitmapserv.org (Cloudflare, 100 MB)
+          username: bitmap
+          password: ${{ secrets.GITEA_REGISTRY_TOKEN }}
 ...
-          images: ghcr.io/${{ github.repository }}
+          images: registry.bitmapserv.org/bitmap/xenotag
 ...
-          cache-from: type=gha
-          cache-to: type=gha,mode=max
+          cache-from: type=gha          # unchanged; the cache stays on GitHub
+          cache-to: type=gha,mode=max
```

```diff
 # ~/docker/docker-compose.yml (prod), line 216
-    image: ghcr.io/bpoulliot/xenotag:latest
+    image: registry.bitmapserv.org/bitmap/xenotag:latest
```

- **Secret:** a Gitea token with `write:package`, stored as a GitHub repo secret. It needs a new
  `~/docker/SECRETS.md` entry, and the operator mints it. Gitea's package scopes are **per user, not per
  package**, so this token on GitHub could write every `bitmap` package, including future van1sh and
  sightline images.
- **Prod pull:** with `REQUIRE_SIGNIN_VIEW=true` every pull is authenticated. That means a `read:package`
  token and a `docker login registry.bitmapserv.org` as the operator. ~/docker I48 part 1, the
  `docker-credential-pass` helper, is already DONE (2026-10-06), so that is a login and a `SECRETS.md`
  entry, not a blocker.
- **If Gitea is down at release time,** the publish fails and the release has no image anywhere.
- **The public image is gone** unless Gitea's sign-in requirement is lifted for the whole site. That would
  expose every repo and package, not just this one. The README (4 places) and the shields badge would have
  to change.
- Registry growth: about 110 MB per release, kept forever until a cleanup rule exists (I59 decision 5), and
  backed up to B2.
- **Ruled out by the measurement:** it breaks public installs.

### (C) Both

Keep the ghcr login and push exactly as they are, and add a separate copy step after the build. Keeping the
copy out of the build export means a Gitea failure cannot fail or half-push the ghcr publish:

```diff
 # .github/workflows/docker-publish.yml, after "Build and push"
+      - name: Log in to the Gitea registry
+        uses: docker/login-action@v4
+        with:
+          registry: registry.bitmapserv.org   # NEVER git.bitmapserv.org (Cloudflare, 100 MB)
+          username: bitmap
+          password: ${{ secrets.GITEA_REGISTRY_TOKEN }}
+      - name: Copy the release to the Gitea registry
+        continue-on-error: true   # ghcr is the release; the Gitea copy is best-effort
+        run: |
+          for t in ${{ steps.meta.outputs.version }} latest; do
+            docker buildx imagetools create \
+              --tag registry.bitmapserv.org/bitmap/xenotag:$t \
+              ghcr.io/${{ github.repository }}:${{ steps.meta.outputs.version }}
+          done
```

- `imagetools create` copies the index and blobs registry to registry, so the digest stays the same on both
  sides (`@sha256` pins agree). The blobs go through the runner: about 5–254 MB down from ghcr and the same
  amount up to this host.
- **Double push time:** the ghcr export and push took 7.0–13.8 s. The Gitea leg would upload the same new
  bytes over the internet instead of within GitHub's network, which is an estimate of seconds, not measured.
  The job would grow from about 50 s to about 1 min.
- **Secrets:** the same `write:package` GitHub secret as (B), with the same user-wide scope, plus a
  `SECRETS.md` entry.
- **If Gitea is down at release time,** the step fails, the job is marked with a warning, and ghcr is
  unaffected. The copy is then missing until someone re-runs it.
- The public image stays (ghcr). Prod's compose line does not change unless the operator also wants prod to
  pull from Gitea, which brings back (B)'s authenticated pull.

### (D) A host-side mirror (no GitHub change)

There is no diff in this repo. A `systemd --user` timer on the host:

1. lists ghcr tags anonymously (the registry API, as the probe does);
2. compares them with Gitea (this needs `read:package`);
3. runs `docker buildx imagetools create --tag registry.bitmapserv.org/bitmap/xenotag:<v> ghcr.io/bpoulliot/xenotag:<v>`.

That uses buildx v0.38.0, which is already installed, so there is no new dependency. It keeps the digest.
Running `docker pull` + `tag` + `push` from the local overlay2 store would re-compress the layers and drop
the attestations, giving a different digest.

- **Push address:** use `registry.bitmapserv.org` (HTTPS, the hairpin to SWAG, no Cloudflare). A
  plain-HTTP `192.168.1.3:3333` is not loopback, so Docker would need it in `insecure-registries`
  (`/etc/docker/daemon.json`, root, an operator step).
- **Secrets:** one Gitea token with `write:package` (which implies read), kept on the host in the `pass`
  credential store via `docker login`, plus a `SECRETS.md` entry. Nothing goes on GitHub.
- **If Gitea is down at release time,** nothing breaks. The next timer run copies what is missing.
- The public image stays (ghcr), and the release workflow is untouched.
- Growth and backups are the same as (B) and (C).

### Cost table

| | GitHub change | New secrets | New moving parts | Release blocked if Gitea down? | Public image | Prod change |
|---|---|---|---|---|---|---|
| **A** | none | none | none | no | ghcr | none |
| **B** | login, metadata, permissions | `write:package` on GitHub (user-wide) + `read:package` on host | — | **yes, no image anywhere** | **lost** (sign-in required site-wide) | image line + authenticated pull |
| **C** | +2 steps | `write:package` on GitHub (user-wide) | Gitea copy step; about 110 MB/release growth | no (`continue-on-error`) | ghcr | none, or (B)'s pull |
| **D** | none | `write:package` on host | a timer + script; about 110 MB/release growth | no | ghcr | none, or (B)'s pull |

## 5. Side effects on ~/docker I59's "open, measure" list

1. **Job-token auth:** not tested here. xenotag builds on GitHub, not Gitea Actions.
2. **Push address:** for a push from *outside* (GitHub), the only usable name is `registry.bitmapserv.org`,
   which is grey-cloud and goes straight to SWAG. It is not capped, but that rests on reasoning; no push was
   made. `git.bitmapserv.org` is capped. `gitea:3000` / `192.168.1.3:3333` are LAN-only plain HTTP.
3. **Build headroom:** not applicable. No xenotag build runs on chromaserv.
5. **Retention:** the registry has **0 cleanup rules** today, and at least 476 MB already sits in it from
   `peakevents`'s single version.

## Re-running

```
python3 scripts/measure_i59_ghcr_pull.py 1.11.1 3     # ghcr cold pull, 3 runs
curl -sS -D - -o /dev/null https://registry.bitmapserv.org/v2/   # Server: nginx = SWAG direct
curl -sS -D - -o /dev/null https://git.bitmapserv.org/v2/        # server: cloudflare
```

The manifest and layer tables come from the same token flow (`/v2/bpoulliot/xenotag/manifests/<tag>`, then
each manifest by digest).
