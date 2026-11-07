from datetime import date

from ledgerline.banking.matching import (
    PURCHASE,
    SALES,
    Candidate,
    name_similarity,
    number_in_text,
    score_candidates,
)


def cand(kind, invoice_id, number, remaining, name="Klant BV", iban=None):
    return Candidate(kind, invoice_id, number, 1, name, iban, remaining, date(2026, 6, 30))


def test_invoice_number_and_amount_rank_first():
    candidates = [
        cand(SALES, 1, "2026-0001", 12_100),
        cand(SALES, 2, "2026-0002", 12_100),
        cand(SALES, 3, "2026-0003", 9_999),
    ]
    ranked = score_candidates(12_100, "Betaling factuur 2026-0002", None, None, candidates)
    assert ranked[0].candidate.invoice_id == 2
    assert ranked[0].exact_amount and ranked[0].number_hit
    assert "invoice number 2026-0002 in description" in ranked[0].reason
    assert ranked[0].score > ranked[1].score >= ranked[2].score


def test_iban_and_name_signals_add_to_score():
    c = cand(SALES, 1, "2026-0001", 5_000, name="Acme Holding BV", iban="NL02ABNA0123456789")
    [s] = score_candidates(5_000, "x", "NL02 ABNA 0123 4567 89", "ACME Holding B.V.", [c])
    assert "IBAN matches contact" in s.reason
    assert "name similar" in s.reason
    assert s.score > 50 + 25


def test_direction_decides_which_invoices_are_candidates():
    candidates = [cand(SALES, 1, "S-1001", 5_000), cand(PURCHASE, 2, "P-1001", 5_000)]
    money_in = score_candidates(5_000, "", None, None, candidates)
    money_out = score_candidates(-5_000, "", None, None, candidates)
    assert [s.candidate.kind for s in money_in] == [SALES]
    assert [s.candidate.kind for s in money_out] == [PURCHASE]


def test_partial_payment_is_flagged():
    c = cand(SALES, 1, "2026-0001", 20_000)
    [s] = score_candidates(12_100, "2026-0001", None, None, [c])
    assert not s.exact_amount
    assert "partial payment" in s.reason


def test_no_signal_scores_zero():
    candidates = [cand(SALES, 1, "2026-0001", 9_900)]
    [s] = score_candidates(1_234, "unrelated", None, "Someone Else", candidates)
    assert s.score == 0


def test_number_matching_ignores_punctuation_but_not_short_fragments():
    assert number_in_text("2026-0001", "factuur 20260001 bedankt")
    assert not number_in_text("2026-0001", "factuur 2026-0010")
    assert number_in_text("12", "factuur 12")
    assert not number_in_text("12", "factuur 123")


def test_name_similarity_bounds():
    assert name_similarity("Klant BV", "klant b.v.") > 0.8
    assert name_similarity("Klant BV", "Zzz") < 0.4
    assert name_similarity(None, "x") == 0.0
