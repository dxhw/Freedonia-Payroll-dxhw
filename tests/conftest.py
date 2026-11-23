from pathlib import Path

import pytest


@pytest.fixture
def laws_dir():
    return Path(__file__).resolve().parents[1] / "laws"
