"""Several Reddit logins, kept apart.

The failure this guards against is subtle and bad: a shared token cache, or a
tool reading cfg.reddit_username instead of the account it was asked for, makes
"as me" silently answer as the wrong person.
"""
from __future__ import annotations

import pytest

from core import reddit_accounts as ra


@pytest.fixture(autouse=True)
def _no_env_account(monkeypatch):
    """The env account is synthesised from cfg, which the dev box may have set."""
    from config import cfg

    for f in ra.FIELDS:
        monkeypatch.setattr(cfg, f"reddit_{f}", "", raising=False)


def _add(root, label, user):
    return ra.add_account(root, label, client_id="cid", client_secret="sec",
                          username=user, password="pw")


def test_add_and_list(tmp_path):
    a = _add(tmp_path, "Main", "alice")
    b = _add(tmp_path, "Project", "bob")
    assert [x["id"] for x in ra.list_accounts(tmp_path)] == [a["id"], b["id"]]
    assert ra.default_id(tmp_path) == a["id"], "first account becomes the default"


def test_partial_credentials_are_refused(tmp_path):
    with pytest.raises(ValueError) as e:
        ra.add_account(tmp_path, "Half", client_id="cid", client_secret="",
                       username="alice", password="pw")
    assert "client_secret" in str(e.value)


def test_duplicate_username_is_refused(tmp_path):
    _add(tmp_path, "Main", "alice")
    with pytest.raises(ValueError, match="already exists"):
        _add(tmp_path, "Another", "Alice")


def test_resolve_by_id_label_or_username(tmp_path):
    a = _add(tmp_path, "Main", "alice")
    _add(tmp_path, "Project", "bob")
    assert ra.resolve(tmp_path, a["id"])["username"] == "alice"
    assert ra.resolve(tmp_path, "Main")["username"] == "alice"
    assert ra.resolve(tmp_path, "alice")["username"] == "alice"
    assert ra.resolve(tmp_path, "ALICE")["username"] == "alice"
    assert ra.resolve(tmp_path, "")["username"] == "alice", "empty means the default"
    assert ra.resolve(tmp_path, "nobody") is None


def test_secrets_never_reach_the_public_view(tmp_path):
    _add(tmp_path, "Main", "alice")
    pub = ra.public_accounts(tmp_path)
    assert pub and all(
        not any(k in a for k in ("password", "client_secret", "client_id")) for a in pub)


def test_default_survives_removal_of_another_account(tmp_path):
    a = _add(tmp_path, "Main", "alice")
    b = _add(tmp_path, "Project", "bob")
    ra.set_default(tmp_path, b["id"])
    ra.remove_account(tmp_path, a["id"])
    assert ra.default_id(tmp_path) == b["id"]


def test_removing_the_default_picks_another(tmp_path):
    a = _add(tmp_path, "Main", "alice")
    b = _add(tmp_path, "Project", "bob")
    assert ra.default_id(tmp_path) == a["id"]
    ra.remove_account(tmp_path, a["id"])
    assert ra.default_id(tmp_path) == b["id"]


def test_env_account_is_surfaced_and_protected(tmp_path, monkeypatch):
    from config import cfg

    monkeypatch.setattr(cfg, "reddit_client_id", "cid", raising=False)
    monkeypatch.setattr(cfg, "reddit_client_secret", "sec", raising=False)
    monkeypatch.setattr(cfg, "reddit_username", "envuser", raising=False)
    monkeypatch.setattr(cfg, "reddit_password", "pw", raising=False)

    accounts = ra.list_accounts(tmp_path)
    assert accounts[0]["id"] == ra.ENV_ID
    assert ra.resolve(tmp_path, "envuser")["from_env"] is True
    # It lives in .env, so it cannot be edited or deleted from the store.
    with pytest.raises(ValueError):
        ra.update_account(tmp_path, ra.ENV_ID, {"label": "x"})
    with pytest.raises(ValueError):
        ra.remove_account(tmp_path, ra.ENV_ID)


def test_a_stored_account_cannot_shadow_the_env_one(tmp_path, monkeypatch):
    from config import cfg

    _add(tmp_path, "Main", "alice")
    for f, v in (("client_id", "cid"), ("client_secret", "sec"),
                 ("username", "envuser"), ("password", "pw")):
        monkeypatch.setattr(cfg, f"reddit_{f}", v, raising=False)
    ids = [a["id"] for a in ra.list_accounts(tmp_path)]
    assert ids.count(ra.ENV_ID) == 1


# ── the tool layer ───────────────────────────────────────────────────────────

def test_tokens_are_cached_per_account(tmp_path, monkeypatch):
    """A shared slot would hand account B the token minted for account A."""
    import tools.social as S

    monkeypatch.setattr(S, "_SOCIAL_ROOT", tmp_path)
    S.forget_reddit_token()
    a = _add(tmp_path, "Main", "alice")
    b = _add(tmp_path, "Project", "bob")

    S._REDDIT_TOKENS[a["id"]] = {"value": "token-a", "expires": 1e18}
    S._REDDIT_TOKENS[b["id"]] = {"value": "token-b", "expires": 1e18}

    import asyncio
    assert asyncio.run(S.reddit_token("alice")) == "token-a"
    assert asyncio.run(S.reddit_token("bob")) == "token-b"

    S.forget_reddit_token(a["id"])
    assert b["id"] in S._REDDIT_TOKENS and a["id"] not in S._REDDIT_TOKENS
    S.forget_reddit_token()
    assert S._REDDIT_TOKENS == {}


def test_an_unknown_account_is_refused_not_silently_defaulted(tmp_path, monkeypatch):
    """Falling back to the default here would answer as the wrong person."""
    import tools.social as S

    monkeypatch.setattr(S, "_SOCIAL_ROOT", tmp_path)
    _add(tmp_path, "Main", "alice")

    assert S.reddit_auth_error("") == ""
    assert S.reddit_auth_error("alice") == ""
    err = S.reddit_auth_error("carol")
    assert "No Reddit account matching 'carol'" in err
    assert "Main" in err, "the error lists what does exist"


def test_reddit_configured_follows_the_accounts(tmp_path, monkeypatch):
    import tools.social as S

    monkeypatch.setattr(S, "_SOCIAL_ROOT", tmp_path)
    assert S.reddit_configured() is False
    _add(tmp_path, "Main", "alice")
    assert S.reddit_configured() is True
