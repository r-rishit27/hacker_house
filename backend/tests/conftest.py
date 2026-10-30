"""Every test starts in demo mode.

Both services load the developer's .env at import, which can carry a real
SENTINEL_AUTH_SECRET. A test that wants enforced mode sets its own secret
(see test_auth.py's ``enforced`` fixture, test_portal.py's ``client``); no test
inherits one from the machine it runs on.
"""

import pytest

from api import auth


@pytest.fixture(autouse=True)
def _demo_mode_unless_a_test_says_otherwise(monkeypatch):
    monkeypatch.delenv(auth.SECRET_ENV, raising=False)
