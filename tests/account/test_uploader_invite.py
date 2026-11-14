from tests.account.conftest import link_in, path_of
from tests.account.test_invitations import PASSWORD, accept, invite
from tests.conftest import _CSRF


def test_owner_invites_uploader_who_can_only_capture(client, admin, outbox, q):
    assert invite(client, "kitchen@example.com", "uploader").status_code == 303
    role = q("SELECT role FROM memberships m JOIN users u ON u.id = m.user_id "
             "WHERE u.email = 'kitchen@example.com'")[0].role
    assert role == "uploader"
    anon, done = accept(None, path_of(link_in(outbox[0])))
    assert done.status_code == 303
    token = _CSRF.search(anon.get("/login").text).group(1)
    login = anon.post("/login", data={"email": "kitchen@example.com", "password": PASSWORD,
                                      "csrf_token": token}, follow_redirects=False)
    assert login.status_code == 303
    assert anon.get("/capture").status_code == 200
    assert anon.get("/sales", follow_redirects=False).headers["location"] == "/capture"


def test_deactivated_uploader_loses_capture(uploader_client, uploader, client):
    assert uploader_client.get("/capture").status_code == 200
    r = client.post(f"/settings/users/{uploader.id}/deactivate",
                    data={"csrf_token": client.csrf}, follow_redirects=False)
    assert r.status_code == 303
    gone = uploader_client.get("/capture", follow_redirects=False)
    assert gone.status_code == 303 and gone.headers["location"] in ("/login", "/tenants")
