import pytest
from sqlalchemy import select

from ledgerline import cli
from ledgerline.auth import hasher
from ledgerline.models import Administration, Role, User


def test_create_user_uses_env_password(session, monkeypatch):
    monkeypatch.setenv("LEDGERLINE_INITIAL_PASSWORD", "initial-password-123")
    assert cli.main(["create-user", "--email", "Boss@Example.com", "--role", "owner"]) == 0
    session.expire_all()
    user = session.scalar(select(User).where(User.email == "boss@example.com"))
    assert user.role == Role.OWNER
    hasher.verify(user.password_hash, "initial-password-123")


def test_create_user_rejects_short_password_and_duplicates(session, monkeypatch, capsys):
    monkeypatch.setenv("LEDGERLINE_INITIAL_PASSWORD", "short")
    assert cli.main(["create-user", "--email", "a@example.com"]) == 1
    assert "at least 12" in capsys.readouterr().err
    monkeypatch.setenv("LEDGERLINE_INITIAL_PASSWORD", "long-enough-password")
    assert cli.main(["create-user", "--email", "a@example.com"]) == 0
    assert cli.main(["create-user", "--email", "A@example.com"]) == 1


def test_create_administration_seeds_chart(session):
    assert cli.main(["create-administration", "--name", "CLI BV", "--legal-form", "bv"]) == 0
    session.expire_all()
    admin = session.scalar(select(Administration).where(Administration.name == "CLI BV"))
    assert admin.legal_form == "bv"


def test_invalid_role_is_rejected():
    with pytest.raises(SystemExit):
        cli.main(["create-user", "--email", "x@example.com", "--role", "root"])
