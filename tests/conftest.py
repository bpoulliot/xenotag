import sys
from pathlib import Path

# The probe under test lives in scripts/, which is not a package; put the repo
# root on the path so `scripts.measure_badge_contrast` and `app.*` both import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_tag_readback_settle(monkeypatch):
    """B17's settle delay before a tag read-back is wall-clock time; tests that time it set it themselves."""
    from app import pipeline

    monkeypatch.setattr(pipeline, "TAG_READBACK_SETTLE_S", 0.0)
