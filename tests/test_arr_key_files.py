"""
Roadmap I9 — a Sonarr/Radarr instance may read its API key from a file
(`api_key_file`), and config.yml must never re-acquire a key for such a row.

I8's contract, carried over to a list: the *arr instances live in
`sonarr.instances` / `radarr.instances`, which I8's dotted-path table cannot
address, so the binding lives in the row itself. As in I8, the tests that
matter are the save-path ones — the override appears to work either way until
someone opens Settings — and they are marked STRIP below.

No real credential appears in this file. The sentinels are obviously fake and
are asserted *absent* from the written file, so a leak shows up as a failure
rather than as a value printed anywhere.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
import yaml

from app import auth as _auth
from app import config as _config
from app.config import (
    AuthConfig,
    ConfigError,
    config_as_dict_safe,
    config_as_yaml,
    file_backed_instances,
    get_config,
    load_config,
    log_env_overrides,
    save_auth,
    save_config,
    save_config_from_dict,
)

YAML_4K = "yaml-sentinel-sonarr-4k-key-0000"
FILE_4K = "file-sentinel-sonarr-4k-key-1111"
YAML_MAIN = "yaml-sentinel-sonarr-main-key-2222"
YAML_RADARR = "yaml-sentinel-radarr-key-3333"
FILE_RADARR = "file-sentinel-radarr-key-4444"
ROTATED_4K = "file-sentinel-sonarr-4k-rotated-5555"
ENV_JF = "env-sentinel-jellyfin-key-6666"
HASH = "$2b$12$notarealhashjustastringforthetest"
SIGNING = "not-a-real-signing-key"

ENV_VARS = (
    "JELLYFIN_API_KEY",
    "JELLYFIN_API_KEY_FILE",
    "XENOTAG_SECRET_KEY",
    "XENOTAG_SECRET_KEY_FILE",
    "XENOTAG_WEBHOOK_SECRET",
    "XENOTAG_WEBHOOK_SECRET_FILE",
    "CONFIG_PATH",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """The ambient environment must not decide the outcome of these tests.

    B25's allowed root is a module constant, not an env var — point it at
    this test's own tmp_path so every fixture's key files resolve under it.
    A test exercising the root refusal itself monkeypatches the constant
    again, to a narrower root, after this fixture has already run.
    """
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_config, "API_KEY_FILE_ROOT", tmp_path)


@pytest.fixture
def secrets_dir(tmp_path):
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "sonarr_4k").write_text(FILE_4K + "\n")
    (d / "radarr").write_text(FILE_RADARR)
    return d


def _base(secrets_dir, *, with_stale_keys: bool = True) -> dict:
    """Two sonarr rows (one file-backed) and one file-backed radarr row."""
    four_k = {"name": "sonarr-4k", "url": "http://sonarr-4k:8989", "api_key_file": str(secrets_dir / "sonarr_4k")}
    radarr = {"name": "radarr", "url": "http://radarr:7878", "api_key_file": str(secrets_dir / "radarr")}
    if with_stale_keys:
        four_k["api_key"] = YAML_4K
        radarr["api_key"] = YAML_RADARR
    return {
        "jellyfin": {"url": "http://jellyfin:8096", "api_key": "", "library_ids": []},
        "sonarr": {"instances": [{"name": "main", "url": "http://sonarr:8989", "api_key": YAML_MAIN}, four_k]},
        "radarr": {"instances": [radarr]},
        "auth": {"username": "admin", "password_hash": HASH, "secret_key": SIGNING},
        "log_level": "INFO",
    }


@pytest.fixture
def cfg_file(tmp_path, secrets_dir):
    """A config.yml fixture — never the live one at ~/docker/xenotag/config."""
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(_base(secrets_dir)))
    return path


def _written(path):
    text = path.read_text()
    return yaml.safe_load(text) or {}, text


def _no_file_keys_in(text: str) -> None:
    for secret in (FILE_4K, YAML_4K, FILE_RADARR, YAML_RADARR, ROTATED_4K):
        assert secret not in text, "a file-backed key reached config.yml"


def _by_name(data: dict, section: str, name: str) -> dict:
    return next(i for i in data[section]["instances"] if i["name"] == name)


def _settings_body(secrets_dir) -> dict:
    """What the Settings page sends: file-backed rows carry their path and a blank key."""
    body = _base(secrets_dir, with_stale_keys=False)
    _by_name(body, "sonarr", "sonarr-4k")["api_key"] = ""
    _by_name(body, "radarr", "radarr")["api_key"] = ""
    return body


# ---------------------------------------------------------------------------
# Baseline: a row with no api_key_file behaves exactly as before
# ---------------------------------------------------------------------------


def test_no_key_file_reads_and_round_trips_the_yaml_key(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump({"sonarr": {"instances": [{"name": "main", "url": "http://s", "api_key": YAML_MAIN}]}}))
    cfg = load_config(path)
    assert cfg.sonarr.instances[0].api_key == YAML_MAIN
    assert file_backed_instances() == []
    save_config_from_dict({"sonarr": {"instances": [{"name": "main", "url": "http://s", "api_key": YAML_MAIN}]}})
    data, _ = _written(path)
    assert data["sonarr"]["instances"][0]["api_key"] == YAML_MAIN
    assert config_as_dict_safe()["sonarr"]["instances"][0]["api_key"] == YAML_MAIN


def test_no_key_file_raw_yaml_is_written_verbatim(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("")
    load_config(path)
    raw = "sonarr:\n  instances:\n  - name: main\n    url: http://s\n    api_key: " + YAML_MAIN + "\n# kept\n"
    save_config(raw)
    assert path.read_text() == raw


def test_a_null_key_file_is_unset(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("sonarr:\n  instances:\n  - name: main\n    url: http://s\n    api_key: k\n    api_key_file:\n")
    cfg = load_config(path)
    assert cfg.sonarr.instances[0].api_key == "k"
    assert cfg.sonarr.instances[0].api_key_file == ""
    assert file_backed_instances() == []


# ---------------------------------------------------------------------------
# The file supplies the key, and wins
# ---------------------------------------------------------------------------


def test_key_file_supplies_the_key_and_wins_over_api_key(cfg_file):
    cfg = load_config(cfg_file)
    main, four_k = cfg.sonarr.instances
    assert four_k.api_key == FILE_4K, "the file must win over the api_key beside it (and be stripped of its newline)"
    assert cfg.radarr.instances[0].api_key == FILE_RADARR
    assert main.api_key == YAML_MAIN, "a row without a key file keeps its own key"
    assert [label for label, _ in file_backed_instances()] == [
        "sonarr instance 'sonarr-4k'",
        "radarr instance 'radarr'",
    ]


def test_missing_key_file_is_fatal(cfg_file, secrets_dir):
    (secrets_dir / "sonarr_4k").unlink()
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "sonarr-4k" in str(exc.value)
    assert YAML_4K not in str(exc.value)


@pytest.mark.parametrize("content", ["", "  \n\t\n"])
def test_empty_key_file_is_fatal(cfg_file, secrets_dir, content):
    (secrets_dir / "radarr").write_text(content)
    with pytest.raises(ConfigError, match="radarr instance 'radarr'.*empty"):
        load_config(cfg_file)


def test_loading_never_writes(cfg_file):
    before = cfg_file.read_text()
    load_config(cfg_file)
    assert cfg_file.read_text() == before, "enabling a key file must not purge the stale key on load"


def test_module_state_is_not_sticky_between_loads(cfg_file, tmp_path):
    load_config(cfg_file)
    assert file_backed_instances()
    other = tmp_path / "other.yml"
    other.write_text(yaml.dump({"sonarr": {"instances": [{"name": "main", "url": "http://s", "api_key": "k"}]}}))
    load_config(other)
    assert file_backed_instances() == []


# ---------------------------------------------------------------------------
# B25 (fixes CodeQL #11) — an api_key_file must resolve under
# API_KEY_FILE_ROOT before it is ever opened. The root is a module constant
# (operator decision 2026-10-09: (a), not an environment variable), so each
# test below narrows it with monkeypatch rather than through the environment.
# ---------------------------------------------------------------------------


def test_path_outside_the_root_is_refused_and_never_read(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(_config, "API_KEY_FILE_ROOT", root)
    outside = tmp_path / "outside"
    outside.write_text("outside-sentinel-must-never-be-read")

    def _must_not_read(self, *a, **k):
        raise AssertionError(f"must not be read: {self} resolves outside the root")

    monkeypatch.setattr(Path, "read_text", _must_not_read)
    data = {"sonarr": {"instances": [{"name": "x", "url": "http://s", "api_key_file": str(outside)}]}}
    with pytest.raises(ConfigError, match="does not resolve under"):
        _config._apply_arr_key_files(data)


def test_a_symlink_inside_the_root_pointing_outside_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(_config, "API_KEY_FILE_ROOT", root)
    outside = tmp_path / "outside-secret"
    outside.write_text("outside-secret-sentinel")
    link = root / "sonarr_4k"
    link.symlink_to(outside)
    data = {"sonarr": {"instances": [{"name": "x", "url": "http://s", "api_key_file": str(link)}]}}
    with pytest.raises(ConfigError, match="does not resolve under"):
        _config._apply_arr_key_files(data)


def test_dot_dot_escaping_the_root_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(_config, "API_KEY_FILE_ROOT", root)
    escaping = str(root / ".." / "escaped")
    data = {"radarr": {"instances": [{"name": "r", "url": "http://r", "api_key_file": escaping}]}}
    with pytest.raises(ConfigError, match="does not resolve under"):
        _config._apply_arr_key_files(data)


def test_a_file_inside_the_root_still_loads(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(_config, "API_KEY_FILE_ROOT", root)
    key_file = root / "sonarr_main"
    key_file.write_text("inside-root-sentinel")
    data = {"sonarr": {"instances": [{"name": "main", "url": "http://s", "api_key_file": str(key_file)}]}}
    _config._apply_arr_key_files(data)
    assert data["sonarr"]["instances"][0]["api_key"] == "inside-root-sentinel"


# ---------------------------------------------------------------------------
# The startup log names instances and paths, never values
# ---------------------------------------------------------------------------


def test_startup_log_names_the_instance_but_never_the_value(cfg_file, secrets_dir, caplog):
    load_config(cfg_file)
    with caplog.at_level(logging.INFO, logger="app.config"):
        log_env_overrides()
    assert "sonarr instance 'sonarr-4k'" in caplog.text
    assert "radarr instance 'radarr'" in caplog.text
    assert str(secrets_dir / "sonarr_4k") in caplog.text
    assert "'main'" not in caplog.text, "a row with no key file is not file-backed"
    for secret in (FILE_4K, YAML_4K, FILE_RADARR, YAML_RADARR, YAML_MAIN):
        assert secret not in caplog.text


def test_startup_log_says_so_when_no_instance_is_file_backed(tmp_path, caplog):
    path = tmp_path / "config.yml"
    path.write_text("")
    load_config(path)
    with caplog.at_level(logging.INFO, logger="app.config"):
        log_env_overrides()
    assert "No Sonarr/Radarr instance reads its API key from a file" in caplog.text


# ---------------------------------------------------------------------------
# THE POINT OF THE ITEM: no save path writes a file-backed key back (STRIP)
# ---------------------------------------------------------------------------


def test_settings_save_does_not_write_a_file_backed_key(cfg_file, secrets_dir):
    """STRIP. The path persists; the key does not; the other row's key does."""
    load_config(cfg_file)
    save_config_from_dict(_settings_body(secrets_dir))
    data, text = _written(cfg_file)
    _no_file_keys_in(text)
    four_k = _by_name(data, "sonarr", "sonarr-4k")
    assert "api_key" not in four_k
    assert four_k["api_key_file"] == str(secrets_dir / "sonarr_4k")
    assert "api_key" not in _by_name(data, "radarr", "radarr")
    assert _by_name(data, "sonarr", "main")["api_key"] == YAML_MAIN
    assert get_config().sonarr.instances[1].api_key == FILE_4K


def test_a_key_typed_into_settings_cannot_displace_the_file(cfg_file, secrets_dir):
    """STRIP. A key submitted beside a key file is neither used nor written."""
    load_config(cfg_file)
    body = _settings_body(secrets_dir)
    _by_name(body, "sonarr", "sonarr-4k")["api_key"] = YAML_4K
    save_config_from_dict(body)
    _, text = _written(cfg_file)
    _no_file_keys_in(text)
    assert get_config().sonarr.instances[1].api_key == FILE_4K


def test_raw_yaml_save_cannot_reintroduce_a_file_backed_key(cfg_file, secrets_dir, caplog):
    """STRIP. The raw editor is re-dumped when a file-backed row carries a key."""
    load_config(cfg_file)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        save_config(yaml.dump(_base(secrets_dir)))
    data, text = _written(cfg_file)
    _no_file_keys_in(text)
    assert _by_name(data, "sonarr", "main")["api_key"] == YAML_MAIN
    assert "sonarr instance 'sonarr-4k'" in caplog.text
    assert YAML_4K not in caplog.text


def test_raw_yaml_save_without_a_key_is_written_verbatim(cfg_file, secrets_dir):
    """A file-backed row with no key in the submitted text keeps the operator's formatting."""
    load_config(cfg_file)
    raw = (
        "sonarr:\n  instances:\n  - name: sonarr-4k\n    url: http://sonarr-4k:8989\n"
        f"    api_key_file: {secrets_dir / 'sonarr_4k'}  # rendered by SOPS\n"
    )
    save_config(raw)
    assert cfg_file.read_text() == raw
    assert get_config().sonarr.instances[0].api_key == FILE_4K


def test_save_auth_does_not_write_a_file_backed_key(cfg_file):
    """STRIP. The bootstrap/password-change path dumps the whole loaded config."""
    load_config(cfg_file)
    save_auth(AuthConfig(username="admin", password_hash=HASH, secret_key=SIGNING))
    data, text = _written(cfg_file)
    _no_file_keys_in(text)
    assert _by_name(data, "sonarr", "main")["api_key"] == YAML_MAIN


def test_first_run_bootstrap_does_not_write_a_file_backed_key(tmp_path, secrets_dir, monkeypatch):
    """STRIP. The very first write of a config (no hash yet) goes through the same strip."""
    monkeypatch.setenv("XENOTAG_PASSWORD", "a-long-enough-test-password")
    data = _base(secrets_dir)
    data["auth"] = {"username": "admin"}
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(data))
    cfg = load_config(path)
    _auth.bootstrap(cfg.auth, save_auth)
    written, text = _written(path)
    assert written["auth"]["password_hash"], "the bootstrap must have written"
    _no_file_keys_in(text)


def test_a_stale_key_already_in_the_file_is_purged_by_the_first_save(cfg_file, secrets_dir):
    """STRIP. Enabling a key file does not purge on load (above); the next save does."""
    load_config(cfg_file)
    assert YAML_4K in cfg_file.read_text()
    save_config_from_dict(_settings_body(secrets_dir))
    _no_file_keys_in(cfg_file.read_text())


def test_both_mechanisms_strip_together(cfg_file, secrets_dir, monkeypatch):
    """STRIP. I8's environment override and I9's key files in one save."""
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_JF)
    load_config(cfg_file)
    body = _settings_body(secrets_dir)
    body["jellyfin"]["api_key"] = ENV_JF
    save_config_from_dict(body)
    data, text = _written(cfg_file)
    _no_file_keys_in(text)
    assert ENV_JF not in text
    assert "api_key" not in data["jellyfin"]


def test_a_save_with_an_unreadable_key_file_is_refused_and_writes_nothing(cfg_file, secrets_dir):
    load_config(cfg_file)
    before = cfg_file.read_text()
    (secrets_dir / "radarr").unlink()
    with pytest.raises(ConfigError):
        save_config_from_dict(_settings_body(secrets_dir))
    assert cfg_file.read_text() == before


def test_a_rotated_key_file_takes_effect_on_the_next_save(cfg_file, secrets_dir):
    load_config(cfg_file)
    (secrets_dir / "sonarr_4k").write_text(ROTATED_4K)
    save_config_from_dict(_settings_body(secrets_dir))
    assert get_config().sonarr.instances[1].api_key == ROTATED_4K
    _no_file_keys_in(cfg_file.read_text())


# ---------------------------------------------------------------------------
# The binding lives in the row: reorder and rename cannot detach it
# ---------------------------------------------------------------------------


def test_reordering_instances_keeps_each_key_with_its_row(cfg_file, secrets_dir):
    load_config(cfg_file)
    body = _settings_body(secrets_dir)
    body["sonarr"]["instances"].reverse()
    save_config_from_dict(body)
    four_k, main = get_config().sonarr.instances
    assert (four_k.name, four_k.api_key) == ("sonarr-4k", FILE_4K)
    assert (main.name, main.api_key) == ("main", YAML_MAIN)


def test_renaming_an_instance_keeps_its_key(cfg_file, secrets_dir):
    load_config(cfg_file)
    body = _settings_body(secrets_dir)
    _by_name(body, "sonarr", "sonarr-4k")["name"] = "UHD shows"
    save_config_from_dict(body)
    assert get_config().sonarr.instances[1].api_key == FILE_4K
    data, text = _written(cfg_file)
    assert _by_name(data, "sonarr", "UHD shows")["api_key_file"] == str(secrets_dir / "sonarr_4k")
    _no_file_keys_in(text)


# ---------------------------------------------------------------------------
# What the browser sees
# ---------------------------------------------------------------------------


def test_safe_dict_blanks_a_file_backed_key_and_keeps_its_path(cfg_file, secrets_dir):
    load_config(cfg_file)
    d = config_as_dict_safe()
    four_k = _by_name(d, "sonarr", "sonarr-4k")
    assert four_k["api_key"] == ""
    assert four_k["api_key_file"] == str(secrets_dir / "sonarr_4k")
    assert _by_name(d, "radarr", "radarr")["api_key"] == ""
    assert _by_name(d, "sonarr", "main")["api_key"] == YAML_MAIN


def test_config_as_yaml_strips_a_stale_key_beside_a_key_file(cfg_file):
    load_config(cfg_file)
    shown = config_as_yaml()
    _no_file_keys_in(shown)
    assert YAML_MAIN in shown


def test_settings_page_keeps_api_key_file_through_a_save():
    """The page rebuilds each row for the save body; a row that lost its
    api_key_file there would be saved with a blank key and no file."""
    from pathlib import Path

    html = (Path(__file__).parent.parent / "app" / "web" / "templates" / "index.html").read_text()
    start = html.index("function cleanInstance(")
    body = html[start : html.index("\n}", start)]
    returned = body[body.index("return {") :]
    assert "api_key_file:" in returned, "cleanInstance must put api_key_file in the object it returns"
    render = html[html.index("function renderInstances(") :]
    render = render[: render.index("\nfunction ")]
    assert "readonly" in render and "Externally managed" in render


# ---------------------------------------------------------------------------
# The routes the Settings page actually calls
# ---------------------------------------------------------------------------


def _stub_routes(monkeypatch):
    from app.web import routes

    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "reschedule", lambda *a, **k: None)
    monkeypatch.setattr(routes, "clear_pill_cache", lambda *a, **k: None)
    return routes


def test_put_settings_route_does_not_write_a_file_backed_key(cfg_file, secrets_dir, monkeypatch):
    """STRIP, one level up: the route reshapes the body before saving."""
    routes = _stub_routes(monkeypatch)
    load_config(cfg_file)
    body = _settings_body(secrets_dir)
    del body["auth"]

    class _Req:
        async def json(self):
            return body

    assert asyncio.run(routes.save_settings(_Req())) == {"status": "saved"}
    data, text = _written(cfg_file)
    _no_file_keys_in(text)
    assert _by_name(data, "sonarr", "sonarr-4k")["api_key_file"] == str(secrets_dir / "sonarr_4k")
    assert data["auth"]["password_hash"] == HASH


class _FakeArr:
    seen: dict[str, str] = {}

    def __init__(self, url, api_key, name=""):
        _FakeArr.seen = {"url": url, "api_key": api_key}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def health(self):
        return {"ok": False, "status": 0, "message": "stubbed — no network in tests"}


def test_arr_test_route_sends_the_file_key_for_a_file_backed_row(cfg_file, secrets_dir, monkeypatch):
    routes = _stub_routes(monkeypatch)
    monkeypatch.setattr(routes, "SonarrClient", _FakeArr)
    load_config(cfg_file)
    req = routes._ArrTestReq(
        arr_type="sonarr",
        url="http://sonarr-4k:8989",
        api_key="typed-7777",
        api_key_file=str(secrets_dir / "sonarr_4k"),
    )
    asyncio.run(routes.arr_test(None, req))
    assert _FakeArr.seen["api_key"] == FILE_4K


def test_arr_test_route_will_not_read_a_file_the_config_does_not_name(cfg_file, tmp_path, monkeypatch):
    """A test request must not become a way to read an arbitrary path into a header."""
    from fastapi import HTTPException

    routes = _stub_routes(monkeypatch)
    monkeypatch.setattr(routes, "RadarrClient", _FakeArr)
    load_config(cfg_file)
    stray = tmp_path / "not-a-configured-secret"
    stray.write_text("stray-sentinel-8888")
    _FakeArr.seen = {}
    req = routes._ArrTestReq(arr_type="radarr", url="http://evil:1", api_key="", api_key_file=str(stray))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.arr_rootfolders_test(None, req))
    assert exc.value.status_code == 400
    assert _FakeArr.seen == {}, "no client may be built for an unconfigured key file"


def test_arr_test_route_without_a_key_file_sends_the_typed_key(cfg_file, monkeypatch):
    routes = _stub_routes(monkeypatch)
    monkeypatch.setattr(routes, "SonarrClient", _FakeArr)
    load_config(cfg_file)
    asyncio.run(routes.arr_test(None, routes._ArrTestReq(arr_type="sonarr", url="http://s", api_key="typed-9999")))
    assert _FakeArr.seen["api_key"] == "typed-9999"
