import re

import pytest

PAGES = ["/", "/sales", "/sales/new", "/purchases", "/purchases/new", "/bank", "/ledger",
         "/ledger/new", "/accounts", "/vat", "/reports", "/contacts", "/contacts/new",
         "/settings", "/settings/privacy"]


@pytest.mark.parametrize("path", PAGES)
def test_every_rendered_form_carries_the_session_csrf_token(client, path):
    response = client.get(path)
    assert response.status_code == 200, path
    values = re.findall(r'name="csrf_token" value="([^"]*)"', response.text)
    assert values, f"{path} renders no CSRF field"
    assert set(values) == {client.csrf}, f"{path} has a missing or stale CSRF token"
