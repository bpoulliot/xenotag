"""
Roadmap I8 — secrets may be supplied by the environment, and config.yml must
never re-acquire one it did not supply.

`config.yml` is read-write application state: every save rewrites the whole
file. So the test that matters is not "does the override apply" but "does a
save put the secret back" — and it is the one that is easy to omit, because the
override appears to work either way until someone opens Settings.

No real credential appears in this file. The sentinels below are obviously fake
and are asserted *absent* from the written file, so a leak shows up as a failure
rather than as a value printed anywhere.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from app import config as cfgmod
from app.config import (
    AuthConfig,
    ConfigError,
    config_as_dict_safe,
    config_as_yaml,
    get_config,
    load_config,
    log_env_overrides,
    overridden_fields,
    save_auth,
    save_config,
    save_config_from_dict,
)

# Sentinels. Distinct per source so precedence is provable, and long enough that
# a substring search over the written file cannot match by accident.
YAML_KEY = "yaml-sentinel-jellyfin-key-0000"
ENV_KEY = "env-sentinel-jellyfin-key-1111"
FILE_KEY = "file-sentinel-jellyfin-key-2222"
YAML_SECRET = "yaml-sentinel-secret-key-3333"
ENV_SECRET = "env-sentinel-secret-key-4444"
HASH = "$2b$12$notarealhashjustastringforthetest"

ALL_VARS = (
    "JELLYFIN_API_KEY",
    "JELLYFIN_API_KEY_FILE",
    "XENOTAG_SECRET_KEY",
    "XENOTAG_SECRET_KEY_FILE",
    "XENOTAG_WEBHOOK_SECRET",
    "XENOTAG_WEBHOOK_SECRET_FILE",
    "CONFIG_PATH",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The ambient environment must not decide the outcome of these tests."""
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cfg_file(tmp_path):
    """A config.yml fixture — never the live one at ~/docker/xenotag/config."""
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.dump(
            {
                "jellyfin": {"url": "http://jellyfin:8096", "api_key": YAML_KEY, "library_ids": []},
                "auth": {"username": "admin", "password_hash": HASH, "secret_key": YAML_SECRET},
                "log_level": "INFO",
            }
        )
    )
    return path


def _written(path):
    """The file as parsed YAML plus its raw text, for presence and absence checks."""
    text = path.read_text()
    return yaml.safe_load(text) or {}, text


# ---------------------------------------------------------------------------
# Baseline: with no environment variables set, nothing changes
# ---------------------------------------------------------------------------


def test_no_env_reads_yaml_value(cfg_file):
    cfg = load_config(cfg_file)
    assert cfg.jellyfin.api_key == YAML_KEY
    assert cfg.auth.secret_key == YAML_SECRET
    assert overridden_fields() == {}


def test_no_env_save_round_trips_every_field(cfg_file):
    load_config(cfg_file)
    save_config_from_dict(
        {
            "jellyfin": {"url": "http://jf:8096", "api_key": YAML_KEY},
            "auth": {"username": "admin", "password_hash": HASH, "secret_key": YAML_SECRET},
        }
    )
    data, _ = _written(cfg_file)
    assert data["jellyfin"]["api_key"] == YAML_KEY
    assert data["auth"]["secret_key"] == YAML_SECRET
    assert data["auth"]["password_hash"] == HASH


def test_no_env_raw_yaml_is_written_verbatim(cfg_file):
    load_config(cfg_file)
    raw = "jellyfin:\n  api_key: " + YAML_KEY + "\n# a comment the operator wrote\n"
    save_config(raw)
    assert cfg_file.read_text() == raw, "with no override active the raw editor must not be re-dumped"


def test_no_env_safe_dict_still_carries_the_key(cfg_file):
    load_config(cfg_file)
    d = config_as_dict_safe()
    assert d["jellyfin"]["api_key"] == YAML_KEY
    assert d["env_managed_fields"] == []
    assert "password_hash" not in d["auth"]


def test_no_env_config_as_yaml_is_the_file(cfg_file):
    load_config(cfg_file)
    assert config_as_yaml() == cfg_file.read_text()


# ---------------------------------------------------------------------------
# The override itself, and its precedence
# ---------------------------------------------------------------------------


def test_env_var_overrides_yaml(cfg_file, monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    cfg = load_config(cfg_file)
    assert cfg.jellyfin.api_key == ENV_KEY
    assert overridden_fields() == {"jellyfin.api_key": "JELLYFIN_API_KEY"}


def test_env_file_overrides_yaml(cfg_file, tmp_path, monkeypatch):
    secret = tmp_path / "jellyfin_api_key"
    secret.write_text(FILE_KEY + "\n")  # trailing newline: what a renderer writes
    monkeypatch.setenv("JELLYFIN_API_KEY_FILE", str(secret))
    cfg = load_config(cfg_file)
    assert cfg.jellyfin.api_key == FILE_KEY
    assert overridden_fields() == {"jellyfin.api_key": "JELLYFIN_API_KEY_FILE"}


def test_file_wins_over_plain_var(cfg_file, tmp_path, monkeypatch):
    secret = tmp_path / "jellyfin_api_key"
    secret.write_text(FILE_KEY)
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    monkeypatch.setenv("JELLYFIN_API_KEY_FILE", str(secret))
    cfg = load_config(cfg_file)
    assert cfg.jellyfin.api_key == FILE_KEY
    assert overridden_fields()["jellyfin.api_key"] == "JELLYFIN_API_KEY_FILE"


def test_webhook_secret_overrides_and_is_not_written_back(cfg_file, monkeypatch):
    """It has no Settings field, but it still round-trips through the browser."""
    monkeypatch.setenv("XENOTAG_WEBHOOK_SECRET", "env-sentinel-webhook-6666")
    load_config(cfg_file)
    assert get_config().webhooks.secret == "env-sentinel-webhook-6666"
    save_config_from_dict({"webhooks": {"secret": "typed-in-by-hand-5555"}, "auth": {"password_hash": HASH}})
    data, text = _written(cfg_file)
    assert data.get("webhooks", {}) == {}
    assert "env-sentinel-webhook-6666" not in text
    assert "typed-in-by-hand-5555" not in text
    assert config_as_dict_safe()["webhooks"]["secret"] == ""


def test_secret_key_overrides_the_same_way(cfg_file, monkeypatch):
    monkeypatch.setenv("XENOTAG_SECRET_KEY", ENV_SECRET)
    cfg = load_config(cfg_file)
    assert cfg.auth.secret_key == ENV_SECRET
    assert overridden_fields() == {"auth.secret_key": "XENOTAG_SECRET_KEY"}


# ---------------------------------------------------------------------------
# A silently ignored override is worse than none — it must be loud
# ---------------------------------------------------------------------------


def test_missing_secret_file_is_fatal(cfg_file, tmp_path, monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY_FILE", str(tmp_path / "does-not-exist"))
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "JELLYFIN_API_KEY_FILE" in str(exc.value)


def test_empty_secret_file_is_fatal(cfg_file, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.write_text("   \n")
    monkeypatch.setenv("JELLYFIN_API_KEY_FILE", str(empty))
    with pytest.raises(ConfigError):
        load_config(cfg_file)


def test_empty_plain_var_is_ignored_with_a_warning(cfg_file, monkeypatch, caplog):
    monkeypatch.setenv("JELLYFIN_API_KEY", "")
    with caplog.at_level(logging.WARNING, logger="app.config"):
        cfg = load_config(cfg_file)
    assert cfg.jellyfin.api_key == YAML_KEY, "an empty variable must not blank a working key"
    assert overridden_fields() == {}
    assert "JELLYFIN_API_KEY" in caplog.text


def test_startup_log_names_the_field_but_never_the_value(cfg_file, monkeypatch, caplog):
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    with caplog.at_level(logging.INFO, logger="app.config"):
        log_env_overrides()
    assert "jellyfin.api_key" in caplog.text
    assert "JELLYFIN_API_KEY" in caplog.text
    assert ENV_KEY not in caplog.text
    assert YAML_KEY not in caplog.text


def test_startup_log_says_so_when_nothing_is_overridden(cfg_file, caplog):
    load_config(cfg_file)
    with caplog.at_level(logging.INFO, logger="app.config"):
        log_env_overrides()
    assert "No config fields overridden" in caplog.text


# ---------------------------------------------------------------------------
# THE POINT OF THE ITEM: a save must not write an overridden field back
# ---------------------------------------------------------------------------


def test_settings_save_does_not_write_the_overridden_secret(cfg_file, monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)

    # Exactly what PUT /api/settings hands to save_config_from_dict — including
    # the blank the UI sends for a read-only field.
    save_config_from_dict(
        {
            "jellyfin": {"url": "http://jellyfin:8096", "api_key": "", "library_ids": []},
            "auth": {"username": "admin", "password_hash": HASH, "secret_key": YAML_SECRET},
        }
    )

    data, text = _written(cfg_file)
    assert "api_key" not in data["jellyfin"], "the overridden field must be absent, not blank"
    assert ENV_KEY not in text
    assert YAML_KEY not in text
    # Everything else still persists, and the app keeps working in memory.
    assert data["jellyfin"]["url"] == "http://jellyfin:8096"
    assert data["auth"]["password_hash"] == HASH
    assert get_config().jellyfin.api_key == ENV_KEY


def test_a_value_typed_into_settings_cannot_displace_the_override(cfg_file, monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    cfg = save_config_from_dict({"jellyfin": {"api_key": "typed-in-by-hand-5555"}})
    _, text = _written(cfg_file)
    assert cfg.jellyfin.api_key == ENV_KEY
    assert "typed-in-by-hand-5555" not in text


def test_raw_yaml_save_cannot_reintroduce_the_secret(cfg_file, monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    save_config(yaml.dump({"jellyfin": {"url": "http://jf:8096", "api_key": FILE_KEY}}))
    data, text = _written(cfg_file)
    assert "api_key" not in data["jellyfin"]
    assert FILE_KEY not in text
    assert ENV_KEY not in text


def test_save_auth_does_not_write_the_overridden_secret_key(cfg_file, monkeypatch):
    monkeypatch.setenv("XENOTAG_SECRET_KEY", ENV_SECRET)
    load_config(cfg_file)
    save_auth(AuthConfig(username="admin", password_hash=HASH, secret_key=ENV_SECRET))
    data, text = _written(cfg_file)
    assert "secret_key" not in data["auth"]
    assert ENV_SECRET not in text
    assert YAML_SECRET not in text
    assert data["auth"]["password_hash"] == HASH, "the bootstrap must still be able to persist the hash"
    assert get_config().auth.secret_key == ENV_SECRET


def test_both_fields_drop_together(cfg_file, monkeypatch):
    """Guards a real short-circuit bug: dropping must not stop at the first hit."""
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    monkeypatch.setenv("XENOTAG_SECRET_KEY", ENV_SECRET)
    load_config(cfg_file)
    save_config_from_dict({"jellyfin": {"api_key": ""}, "auth": {"password_hash": HASH}})
    data, text = _written(cfg_file)
    assert "api_key" not in data["jellyfin"]
    assert "secret_key" not in data["auth"]
    for sentinel in (ENV_KEY, ENV_SECRET, YAML_KEY, YAML_SECRET):
        assert sentinel not in text


def test_a_stale_secret_already_in_the_file_is_purged_by_the_first_save(cfg_file, monkeypatch):
    """
    Enabling the override does not rewrite config.yml — load must not write. The
    stale value goes away on the next save, which is the documented procedure.
    """
    assert YAML_KEY in cfg_file.read_text()
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    assert YAML_KEY in cfg_file.read_text(), "load_config must not rewrite the file"
    save_config_from_dict({"jellyfin": {"api_key": ""}, "auth": {"password_hash": HASH}})
    assert YAML_KEY not in cfg_file.read_text()


# ---------------------------------------------------------------------------
# What the browser is allowed to see
# ---------------------------------------------------------------------------


def test_safe_dict_blanks_the_override_and_names_it(cfg_file, monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    d = config_as_dict_safe()
    assert d["jellyfin"]["api_key"] == ""
    assert d["env_managed_fields"] == ["jellyfin.api_key"]
    assert ENV_KEY not in yaml.dump(d)


def test_config_as_yaml_strips_a_stale_overridden_value(cfg_file, monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    served = config_as_yaml()
    assert YAML_KEY not in served, "the raw editor must not hand the browser a stale secret"
    assert ENV_KEY not in served
    assert "api_key" not in (yaml.safe_load(served) or {})["jellyfin"]


def test_env_managed_fields_survives_the_settings_round_trip(cfg_file, monkeypatch):
    """
    The UI spreads the fetched settings back into its PUT body, so the extra key
    reaches AppConfig.model_validate. It must be ignored, not rejected.
    """
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    body = config_as_dict_safe()
    body["auth"]["password_hash"] = HASH
    cfg = save_config_from_dict(body)
    assert cfg.jellyfin.api_key == ENV_KEY
    assert not hasattr(cfg, "env_managed_fields")


def test_module_state_is_not_sticky_between_loads(cfg_file, monkeypatch):
    """A removed variable must stop overriding — the record is per-load, not cumulative."""
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)
    assert overridden_fields()
    monkeypatch.delenv("JELLYFIN_API_KEY")
    cfg = load_config(cfg_file)
    assert overridden_fields() == {}
    assert cfg.jellyfin.api_key == YAML_KEY


def test_every_overridable_field_is_a_real_config_path(cfg_file):
    """A typo in ENV_OVERRIDABLE would make an override silently do nothing."""
    cfg = load_config(cfg_file)
    for dotted in cfgmod.ENV_OVERRIDABLE:
        node = cfg
        for part in dotted.split("."):
            assert hasattr(node, part), f"{dotted} is not a field of AppConfig"
            node = getattr(node, part)


# ---------------------------------------------------------------------------
# The route the Settings page actually calls
# ---------------------------------------------------------------------------


def test_put_settings_route_does_not_write_the_overridden_secret(cfg_file, monkeypatch):
    """
    The same proof one level up, through `PUT /api/settings` itself, because the
    route reshapes the body before saving (it reinstates the password hash the
    frontend never sends) and that reshaping is where a secret could sneak back.
    """
    import asyncio

    from app.web import routes

    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "reschedule", lambda *a, **k: None)
    monkeypatch.setattr(routes, "clear_pill_cache", lambda *a, **k: None)
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)

    body = {"jellyfin": {"url": "http://jellyfin:8096", "api_key": ""}, "auth": {"username": "admin"}}

    class _Req:
        async def json(self):
            return body

    result = asyncio.run(routes.save_settings(_Req()))
    assert result == {"status": "saved"}

    data, text = _written(cfg_file)
    assert "api_key" not in data["jellyfin"]
    assert ENV_KEY not in text
    assert YAML_KEY not in text
    assert data["auth"]["password_hash"] == HASH, "the route must still preserve the hash it reinstates"
    assert get_config().jellyfin.api_key == ENV_KEY


def test_jellyfin_test_route_ignores_a_submitted_key_when_overridden(cfg_file, monkeypatch):
    """A connection test must exercise the key that will actually be sent."""
    import asyncio

    from app.web import routes

    seen: dict[str, str] = {}

    class _FakeJF:
        def __init__(self, url, api_key):
            seen["url"] = url
            seen["api_key"] = api_key

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def health(self):
            return {"ok": False, "status": 0, "message": "stubbed — no network in tests"}

    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "JellyfinClient", _FakeJF)
    monkeypatch.setenv("JELLYFIN_API_KEY", ENV_KEY)
    load_config(cfg_file)

    req = routes._ConnTestReq(url="http://jellyfin:8096/", api_key="typed-in-by-hand-5555")
    asyncio.run(routes.jellyfin_test(None, req))
    assert seen["api_key"] == ENV_KEY
    assert seen["url"] == "http://jellyfin:8096"
