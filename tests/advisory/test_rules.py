from datetime import date

from ledgerline.advisory import rules

I9, I21 = frozenset({"I9"}), frozenset({"I21"})
MIXED = frozenset({"I9", "I21"})


def test_usual_code_needs_enough_history():
    assert rules.usual_vat_code([I9, I9]) is None
    assert rules.usual_vat_code([I9, I9, I9]) == "I9"


def test_usual_code_needs_a_clear_majority():
    assert rules.usual_vat_code([I9] * 4 + [I21]) == "I9"
    assert rules.usual_vat_code([I9] * 3 + [I21] * 2) is None


def test_mixed_invoices_never_give_a_usual_code():
    assert rules.usual_vat_code([MIXED] * 5) is None
    assert rules.usual_vat_code([I9, I9, I9, MIXED, MIXED]) is None


def test_vat_rate_flag_names_supplier_and_rates():
    found = rules.vat_rate_advice("Sligro", [I9] * 4, ["I9", "I21"])
    assert [a.code for a in found] == ["vat_rate"]
    assert "Line 2" in found[0].message and "9%" in found[0].message
    assert "21%" in found[0].message and "Sligro" in found[0].message


def test_wholesaler_with_mixed_history_is_not_flagged():
    assert rules.vat_rate_advice("Sligro", [MIXED] * 6, ["I21", "I9"]) == []


def test_too_little_history_is_not_flagged():
    assert rules.vat_rate_advice("Sligro", [I9, I9], ["I21"]) == []


def test_blank_unknown_and_reverse_charge_codes_are_skipped():
    assert rules.vat_rate_advice("Sligro", [I9] * 4, ["", "NOPE", "IRV21"]) == []


def test_amount_flag_above_five_times_median():
    history = [10000, 12000, 9000, 11000, 10500]
    assert rules.amount_advice("Sligro", history, 52500) == []
    found = rules.amount_advice("Sligro", history, 52501)
    assert [a.code for a in found] == ["amount"] and "5 times" in found[0].message


def test_amount_flag_needs_five_history_points_and_a_total():
    assert rules.amount_advice("Sligro", [10000] * 4, 10**7) == []
    assert rules.amount_advice("Sligro", [10000] * 5, None) == []


def test_amount_flag_ignores_zero_totals_in_history():
    assert rules.amount_advice("Sligro", [0, 0, 0, 0, 0, 10000], 10**7) == []


def test_period_advice_locked_settled_approved():
    day = date(2026, 2, 10)
    assert rules.period_advice(day, locked_until=None, approved_period=None,
                               settled_period=None) == []
    assert rules.period_advice(None, locked_until=date(2026, 12, 31), approved_period="x",
                               settled_period="y") == []
    locked = rules.period_advice(day, locked_until=date(2026, 2, 10), approved_period=None,
                                 settled_period=None)
    assert [a.code for a in locked] == ["period_locked"]
    both = rules.period_advice(day, locked_until=None, approved_period="p",
                               settled_period="2026-Q1")
    assert [a.code for a in both] == ["period_settled"]
    approved = rules.period_advice(day, locked_until=None, approved_period="p",
                                   settled_period=None)
    assert [a.code for a in approved] == ["period_approved"]
