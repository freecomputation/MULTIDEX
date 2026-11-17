import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import pytest

from multidex import profile as profilemod

BUILD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = os.path.join(BUILD, "profiles")

# A fixed watch start for deterministic tests: 2026-11-17 12:00 UTC.
T0 = 1794916800.0


@pytest.fixture()
def profile():
    prof = profilemod.load_profile(os.path.join(PROFILES, "default.toml"))
    prof["watch"]["disk_ceiling_gb"] = 0.05  # tmp homes are small
    return prof


@pytest.fixture()
def home(tmp_path):
    return str(tmp_path / "mdx")


@pytest.fixture()
def index(home):
    from multidex.index import Index
    idx = Index(home)
    yield idx
    idx.close()
