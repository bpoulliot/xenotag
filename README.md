# Xenotag

[![CI](https://github.com/bpoulliot/xenotag/actions/workflows/ci.yml/badge.svg)](https://github.com/bpoulliot/xenotag/actions/workflows/ci.yml)
[![CodeQL](https://github.com/bpoulliot/xenotag/actions/workflows/codeql.yml/badge.svg)](https://github.com/bpoulliot/xenotag/actions/workflows/codeql.yml)
[![Docker](https://img.shields.io/badge/ghcr.io-bpoulliot%2Fxenotag-blue)](https://github.com/bpoulliot/xenotag/pkgs/container/xenotag)

Xenotag scans your Jellyfin library, extracts resolution, codec, HDR, and audio language metadata from media files via `ffprobe`, and writes that metadata back as tags in Jellyfin, Sonarr, and Radarr. It also overlays badge pills directly onto poster images so your library art shows resolution, format, and language at a glance — no manual tagging required.

---

> **Mount Point Requirement**
>
> Every volume path mounted into the Xenotag container **must be identical** to the path
> Jellyfin uses inside its own container. If Jellyfin sees movies at `/data/movies`, Xenotag
> must also mount and access them at `/data/movies`. Mismatched paths cause all file-level
> lookups to fail silently — media will be scanned but poster images will not be updated and
> `ffprobe` will be unable to read files. This applies to every media mount in every container.

---

## Features

### Metadata Extraction
- Extracts **resolution** (480p, 720p, 1080p, 4K), **video codec** (H.264, H.265/HEVC, AV1, VP9, etc.), and **HDR type** (HDR10, HDR10+, Dolby Vision, HLG) via `ffprobe`
- Extracts **audio track languages** and **codecs** (TrueHD, DTS-HD, AC-3, AAC, etc.)
- Extracts **subtitle track languages** and formats (PGS, SRT, ASS, embedded vs. external)
- Reads **content rating** from Jellyfin; falls back to Sonarr/Radarr certification data

### Tag Writing
- Writes `xt-*` prefixed tags to **Jellyfin**, **Sonarr**, and **Radarr** simultaneously
- Supports multiple Sonarr and Radarr instances (separate 4K/HD instances, etc.)
- Configurable tag prefix, dual-audio tag, and multi-audio tag
- Per-destination tag routing — send video tags only to Jellyfin, audio tags only to Sonarr, etc.
- Preserves existing user-defined tags; only manages its own prefixed set

### Poster Badge Overlay
- Overlays **pill-shaped badges** directly onto poster/folder images
- **Video badges**: resolution, codec, HDR type in a single grouped pill
- **Audio badges**: codec-first grouping — `DTS-HD EN JA` instead of separate pills per language
- **Subtitle badges**: format-first grouping — `PGS EN JA IT`, `SRT EN`
- **Rating badge**: content rating rendered independently at top-right
- Badge position configurable: bottom-left, bottom-right, top-left, top-right
- Configurable opacity, font size, badge colors per group (video, audio, subtitle, rating)
- Long language lists truncate gracefully with `…` rather than overflowing the poster edge
- Automatic portrait padding for square or landscape source images (blurred edge-fill extension)
- Original posters are backed up with a `.orig` suffix before the first overlay

### Scanning
- **Full scan**: re-processes every item in the library
- **Incremental scan**: skips files whose mtime hasn't changed since last scan — runs in seconds on large libraries after initial full scan
- **Webhook-triggered scan**: single-item processing on Sonarr, Radarr, or Jellyfin download/import events
- **Scheduled scans**: configurable cron expression (default: weekly)
- Parallel `ffprobe` via configurable worker pool (`scan.max_workers`)
- Path filters: limit scanning to specific mount prefixes
- Tag config change detection: automatically forces a full re-tag when tag settings change
- Per-item error tracking: probe failures, missing files, process errors — visible in the dashboard

### Web UI
- **Dashboard**: stats summary, last scan details, health status for all connected services
- **Live scan log**: real-time SSE stream of scan progress with percentage bar and current item
- **Scan history**: per-scan records with runtime, items scanned/tagged/errored
- **Scan errors table**: live-refreshing during active scans; full file path, item ID, error type
- **Media browser**: paginated table filterable by resolution and language
- **Badge preview**: render overlay against synthetic test posters or live Jellyfin artwork using your current color/opacity/position settings
- **Settings editor**: full config editor with live preview; change password; reschedule scans
- **Cancel button**: interrupt a running scan between phases

### Security
- Session cookies: HttpOnly, SameSite=lax, 30-day expiry, `Secure` flag on by default
- Rate-limited login: 10 attempts/minute per IP
- bcrypt password hashing (12 rounds), minimum 12-character passwords
- Security headers on every response: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, X-XSS-Protection
- Changing password immediately invalidates all existing sessions
- Rotate `auth.secret_key` to force-invalidate all sessions without a password change

---

## Quick Start (Docker)

**1. Pull from GitHub Container Registry:**

```bash
docker pull ghcr.io/bpoulliot/xenotag:latest
```

**2. Create a config directory and copy the example config:**

```bash
mkdir -p ./config
cp config.example.yml ./config/config.yml
```

**3. Edit `config/config.yml`:**

- Set `jellyfin.url` and `jellyfin.api_key` (Dashboard → Advanced → API Keys)
- Add Sonarr/Radarr instances under `sonarr.instances` / `radarr.instances` (optional)
- Leave `auth.password_hash` empty — credentials are auto-generated on first boot and printed to the container log

**4. Configure `docker-compose.yml`:**

```yaml
services:
  xenotag:
    image: ghcr.io/bpoulliot/xenotag:latest
    container_name: xenotag
    volumes:
      - ./config:/config
      # Mirror your Jellyfin volume mounts exactly:
      - /path/to/media/movies:/path/to/media/movies:rw
      - /path/to/media/tv:/path/to/media/tv:rw
    ports:
      - "127.0.0.1:7755:7755"
    restart: unless-stopped
```

**5. Start:**

```bash
docker compose up -d
```

Open `http://localhost:7755`. On first boot, auto-generated credentials are printed to the container log:

```bash
docker logs xenotag | grep -A4 "FIRST RUN"
```

---

## Configuration Reference

All settings live in `/config/config.yml`.

### Jellyfin

| Key | Type | Default | Description |
|---|---|---|---|
| `jellyfin.url` | string | `http://jellyfin:8096` | Jellyfin base URL |
| `jellyfin.api_key` | string | `""` | Jellyfin API key |
| `jellyfin.library_ids` | list | `[]` | Library IDs to scan; empty = all libraries |

### Sonarr / Radarr

```yaml
sonarr:
  instances:
    - name: sonarr
      url: http://sonarr:8989
      api_key: ""
    - name: sonarr-4k         # multiple instances supported
      url: http://sonarr-4k:8989
      api_key: ""

radarr:
  instances:
    - name: radarr
      url: http://radarr:7878
      api_key: ""
```

### Scanning

| Key | Type | Default | Description |
|---|---|---|---|
| `scan.schedule` | string | `"0 3 * * 0"` | Cron expression for automatic scans; empty = manual only |
| `scan.incremental` | bool | `true` | Skip files unchanged since last scan |
| `scan.path_filters` | list | `[]` | Only scan paths matching these prefixes; empty = all |
| `scan.max_workers` | int | `4` | Parallel `ffprobe` workers; lower for slow/spinning disks |

### Tags

| Key | Type | Default | Description |
|---|---|---|---|
| `tags.managed_prefix` | string | `"xt-"` | Prefix for all tags written by Xenotag |
| `tags.dual_audio_tag` | string | `"dual-audio"` | Tag applied when exactly 2 audio languages detected |
| `tags.multi_audio_tag` | string | `"multi-audio"` | Tag applied when 3+ audio languages detected |
| `tags.destinations.video` | list | `["jellyfin","sonarr","radarr"]` | Which services receive video tags |
| `tags.destinations.audio` | list | `["jellyfin","sonarr","radarr"]` | Which services receive audio tags |
| `tags.destinations.subtitles` | list | `["jellyfin"]` | Which services receive subtitle tags |
| `tags.destinations.rating` | list | `["jellyfin"]` | Which services receive rating tags |

### Image Overlay

| Key | Type | Default | Description |
|---|---|---|---|
| `image.targets` | list | `[poster.jpg, ...]` | Poster filenames to search for in each item folder |
| `image.backup_suffix` | string | `".orig"` | Suffix appended to original poster backups |
| `image.badge_position` | string | `"bottom-left"` | Main badge group position: `bottom-left`, `bottom-right`, `top-left`, `top-right`. Rating badge is always `top-right`. |
| `image.badge_opacity` | float | `1.0` | Badge fill opacity (0.0–1.0). This is a contrast control: below `1.0` the poster shows through the badge and the label's rendered contrast drops below the figure quoted for each colour. With the shipped palette, `0.89` is the floor for WCAG AAA and `0.73` for AA — measure with `scripts/measure_badge_contrast.py`. |
| `image.badge_size` | string | `"tv"` | Base font size tier: `desktop`, `tv`, `tv_plus` |
| `image.normalize_portrait` | bool | `true` | Pad square/landscape images to 2:3 portrait ratio |
| `image.show_video_badges` | bool | `true` | Render video group badge |
| `image.show_audio_badges` | bool | `true` | Render audio group badge |
| `image.show_sub_badges` | bool | `true` | Render subtitle group badge |
| `image.show_rating_badge` | bool | `true` | Render content rating badge |
| `image.video_badge_color` | string | `"#1e3a5f"` | Video badge fill color (hex) |
| `image.audio_badge_color` | string | `"#1e3a5f"` | Audio badge fill color (hex) |
| `image.sub_badge_color` | string | `"#1e3a5f"` | Subtitle badge fill color (hex) |
| `image.rating_badge_color` | string | `"#7c2d12"` | Rating badge fill color (hex) |
| `image.badge_text_color` | string | `"#ffffff"` | Badge text color (hex) |

### Auth

| Key | Type | Default | Description |
|---|---|---|---|
| `auth.username` | string | `"admin"` | Login username |
| `auth.password_hash` | string | `""` | bcrypt hash; leave empty for auto-generation on first boot |
| `auth.secret_key` | string | `""` | HMAC signing secret for sessions; auto-generated if empty |

### Other

| Key | Type | Default | Description |
|---|---|---|---|
| `log_level` | string | `"INFO"` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Environment Variables

These override `config.yml` values and are read only on first boot (when no credentials exist yet):

| Variable | Description |
|---|---|
| `XENOTAG_USERNAME` | Initial admin username |
| `XENOTAG_PASSWORD` | Initial admin password (min 12 chars) |
| `SECURE_COOKIES` | Set to `false` when running over plain HTTP without a reverse proxy |
| `CONFIG_PATH` | Path to config file inside the container (default: `/config/config.yml`) |

---

## Webhook Integration

Xenotag can process a single item immediately when Sonarr, Radarr, or Jellyfin fires a download/import event.

**Endpoint:** `POST /webhook/{source}` where `{source}` is `sonarr`, `radarr`, or `jellyfin`

Configure the webhook URL in your *arr application's Connect settings. Xenotag will resolve the Jellyfin item, run `ffprobe`, apply tags, and update the poster overlay — all within seconds of the download completing.

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | No | Service info unauthenticated; full upstream connectivity when authenticated |
| GET | `/stats` | Yes | Scan statistics and next scheduled run time |
| POST | `/scan/full` | Yes | Trigger a full library scan |
| POST | `/scan/incremental` | Yes | Trigger an incremental scan |
| POST | `/scan/cancel` | Yes | Cancel a running scan |
| GET | `/scan/status` | Yes | Current scan progress (total, done, running, current item) |
| GET | `/scan/stream` | Yes | SSE stream of live scan log lines |
| GET | `/scan/errors` | Yes | All scan errors (probe failures, missing files) |
| DELETE | `/scan/errors` | Yes | Clear all scan errors |
| GET | `/scan/history` | Yes | Recent scan run records |
| GET | `/media` | Yes | Paginated media browser (filter by resolution, language) |
| GET | `/api/settings` | Yes | Structured settings (password_hash redacted) |
| PUT | `/api/settings` | Yes | Save settings |
| POST | `/api/auth/change-password` | Yes | Change login password |
| GET | `/config` | Yes | Raw YAML config |
| PUT | `/config` | Yes | Save raw YAML config |
| GET | `/api/jellyfin/libraries` | Yes | List Jellyfin libraries |
| GET | `/api/sonarr/{name}/rootfolders` | Yes | List Sonarr root folders |
| GET | `/api/radarr/{name}/rootfolders` | Yes | List Radarr root folders |
| GET | `/api/preview/sample-posters` | Yes | Poster sources for badge preview |
| GET | `/preview/image` | Yes | Render a preview badge overlay image |
| POST | `/webhook/{source}` | No* | Trigger single-item processing from *arr/Jellyfin webhook |

*Webhook endpoint is unauthenticated by design to support *arr's built-in webhook delivery.

---

## Integrating with an Existing *arr Stack

Attach Xenotag to your existing Docker Compose network:

```yaml
services:
  xenotag:
    image: ghcr.io/bpoulliot/xenotag:latest
    networks:
      - your_existing_network   # same network as Jellyfin/Sonarr/Radarr

networks:
  your_existing_network:
    external: true
```

Then use container names as hostnames in `config.yml`:

```yaml
jellyfin:
  url: http://jellyfin:8096
sonarr:
  instances:
    - name: sonarr
      url: http://sonarr:8989
```

---

## Development Setup

```bash
# Clone and start the full dev stack (Jellyfin + Sonarr + Radarr + Xenotag with hot-reload)
git clone https://github.com/bpoulliot/xenotag.git
cd xenotag
docker compose -f docker-compose.dev.yml up -d

# UI is at http://localhost:7755
# Default dev credentials: admin / xenotag
```

The dev compose mounts `app/` directly into the container so code changes are picked up immediately (uvicorn `--reload`).

```bash
# Lint and format before committing
pip install ruff black
ruff check app/
black --check app/
```

---

## CI/CD

Every push and pull request runs:

| Check | Tool |
|---|---|
| Python lint + format | ruff + black |
| Python SAST | bandit |
| Dependency CVE audit | pip-audit |
| Dockerfile lint | hadolint |
| Container CVE scan | Trivy (CRITICAL + HIGH, fixed only) |
| Deep Python SAST | CodeQL (weekly + on push/PR to main) |
| PR dependency risk | dependency-review (PRs only) |

Publishing to `ghcr.io/bpoulliot/xenotag` happens automatically on semver tags (`v*.*.*`) via the Release workflow, with SBOM and provenance attestation included.

---

## License

[MIT](LICENSE)
