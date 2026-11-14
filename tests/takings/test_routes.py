from datetime import date

from sqlalchemy import select

from ledgerline.models import DailyTakings, JournalEntry
from ledgerline.takings.service import TakingsInput, post_takings

FORM = {"date": "2026-10-05", "gross_high": "121,00", "gross_low": "", "gross_zero": "",
        "cash": "21,00", "card": "100,00", "other": "", "note": "Z 7"}


def rows(session):
    session.expire_all()
    return session.scalars(select(DailyTakings).order_by(DailyTakings.id)).all()


def post(client, url, **data):
    return client.post(url, data={"csrf_token": client.csrf, **data}, follow_redirects=False)


def test_entry_form_posts_and_lists_the_day(client, session, admin):
    response = post(client, "/takings/new", **FORM)
    assert response.status_code == 303
    (row,) = rows(session)
    assert (row.gross_high_cents, row.cash_cents, row.card_cents, row.note) == (
        12100, 2100, 10000, "Z 7")
    page = client.get("/takings").text
    assert "05-10-2026" in page and "Z 7" in page


def test_mismatch_shows_a_clear_error_and_posts_nothing(client, session, admin):
    response = post(client, "/takings/new", **{**FORM, "card": "50,00"})
    assert response.status_code == 422
    assert "do not equal" in response.text
    assert rows(session) == []


def test_unparsable_amount_is_reported(client, session, admin):
    response = post(client, "/takings/new", **{**FORM, "cash": "twintig"})
    assert response.status_code == 422 and "not a valid amount" in response.text


def test_correct_and_reverse_from_the_list(client, session, admin):
    post(client, "/takings/new", **FORM)
    (row,) = rows(session)
    assert client.get(f"/takings/{row.id}/edit").status_code == 200
    response = post(client, f"/takings/{row.id}/edit", **{**FORM, "gross_high": "242,00",
                                                           "card": "221,00"})
    assert response.status_code == 303
    old, new = rows(session)
    assert old.reversed_at is not None and new.gross_high_cents == 24200
    post(client, f"/takings/{new.id}/reverse")
    assert all(r.reversed_at for r in rows(session))
    assert "Include reversed" in client.get("/takings?show=reversed").text
    assert client.get(f"/takings/{new.id}/edit").status_code == 404
    entries = session.scalars(select(JournalEntry)).all()
    assert len(entries) == 4


def test_locked_period_blocks_the_form(client, session, admin):
    admin.locked_until = date(2026, 10, 31)
    session.commit()
    response = post(client, "/takings/new", **FORM)
    assert response.status_code == 422 and "closed up to 2026-10-31" in response.text


def test_csv_upload_template_and_error_page(client, session, admin):
    template = client.get("/takings/template.csv")
    assert template.status_code == 200 and "gross_21" in template.text
    ok = client.post("/takings/import", data={"csrf_token": client.csrf},
                     files={"file": ("z.csv", template.content)}, follow_redirects=False)
    assert ok.status_code == 303 and len(rows(session)) == 2
    bad = client.post("/takings/import", data={"csrf_token": client.csrf},
                      files={"file": ("z.csv", b"date;gross_21;gross_9;gross_0;cash;card;other;"
                                      b"note\n2026-11-01;10;;;5;;;\n")})
    assert bad.status_code == 422 and "Line 2" in bad.text and len(rows(session)) == 2


def test_totals_by_week_and_month(client, session, admin):
    for d, amount in ((date(2026, 10, 5), 1210), (date(2026, 10, 11), 2420),
                      (date(2026, 10, 12), 100), (date(2026, 11, 2), 500)):
        post_takings(session, admin, TakingsInput(d, gross_high_cents=amount, cash_cents=amount))
    session.commit()
    week = client.get("/takings/totals?kind=week&year=2026").text
    assert "2026-W41" in week and "2026-W42" in week and "36,30" in week
    month = client.get("/takings/totals?kind=month&year=2026").text
    assert "2026-10" in month and "37,30" in month and "2026-11" in month
    assert client.get("/takings/totals?kind=day").status_code == 422


def test_accountant_reads_but_cannot_write(readonly_client, admin):
    assert readonly_client.get("/takings").status_code == 200
    assert readonly_client.get("/takings/totals").status_code == 200
    assert readonly_client.get("/takings/new").status_code == 403
    assert post(readonly_client, "/takings/new", **FORM).status_code == 403
