"""Keep tests and their CLI subprocesses away from the user's live MemHall state."""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_user_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("memhall-test-home")
    previous = {key: os.environ.get(key) for key in ("HOME", "USERPROFILE")}
    for key in previous:
        os.environ[key] = str(home)
    yield home
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
