"""Hand-built journal entries that follow the posting rules the VAT return relies on."""
from ledgerline.ledger.service import LineSpec, post_entry
from ledgerline.money import percent_of
from ledgerline.vat.codes import VAT_CODES
from tests.helpers import account

REVENUE = {"V21": "8000", "V9": "8010", "V0": "8020", "VRV": "8020", "VEU": "8030",
           "VEX": "8040", "VVR": "8050", "VKOR": "8050"}


def book_sale(session, admin, code, excl, when, contact_id=None):
    vc = VAT_CODES[code]
    vat = percent_of(excl, vc.rate)
    specs = [
        LineSpec(account(session, admin, "1300").id, debit_cents=excl + vat, contact_id=contact_id),
        LineSpec(account(session, admin, REVENUE[code]).id, credit_cents=excl,
                 vat_code=code, vat_role="base"),
    ]
    if vat:
        vat_account = "1700" if vc.rate == 21 else "1710"
        specs.append(LineSpec(account(session, admin, vat_account).id, credit_cents=vat,
                              vat_code=code, vat_role="vat"))
    return post_entry(session, admin.id, when, f"Sale {code}", specs)


def book_purchase(session, admin, code, excl, when, vat=None):
    vc = VAT_CODES[code]
    vat = percent_of(excl, vc.rate) if vat is None else vat
    specs = [LineSpec(account(session, admin, "4200").id, debit_cents=excl,
                      vat_code=code, vat_role="base")]
    if vat:
        specs.append(LineSpec(account(session, admin, "1400").id, debit_cents=vat,
                              vat_code=code, vat_role="vat"))
    if vc.reverse_charge:
        specs.append(LineSpec(account(session, admin, "1720").id, credit_cents=vat,
                              vat_code=code, vat_role="vat"))
        specs.append(LineSpec(account(session, admin, "1600").id, credit_cents=excl))
    else:
        specs.append(LineSpec(account(session, admin, "1600").id, credit_cents=excl + vat))
    return post_entry(session, admin.id, when, f"Purchase {code}", specs)



def approve_form(client, kind: str = "quarter", year: str = "2026", number: str = "1",
                 **overrides) -> dict:
    """The approve form as the page offers it: its digest and every Checked box ticked."""
    import re

    page = client.get(f"/vat?kind={kind}&year={year}&number={number}").text
    digest = re.search(r'name="figures" value="([0-9a-f]+)"', page).group(1)
    form = {"kind": kind, "year": year, "number": number, "figures": digest,
            "documents_checked": "yes",
            "confirm": re.findall(r'name="confirm" value="([^"]+)"', page),
            "csrf_token": client.csrf}
    return {**form, **overrides}


def approve_period(client, kind: str = "quarter", year: str = "2026", number: str = "1"):
    return client.post("/vat/approve", data=approve_form(client, kind, year, number),
                       follow_redirects=False)
