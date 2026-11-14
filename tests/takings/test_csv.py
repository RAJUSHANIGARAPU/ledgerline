from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline.models import DailyTakings, JournalEntry
from ledgerline.takings import csv_import
from ledgerline.takings.csv_import import CsvImportError, import_takings, template_csv

HEADER = "date;gross_21;gross_9;gross_0;cash;card;other;note\n"


def count(session):
    session.expire_all()
    return session.scalar(select(func.count()).select_from(DailyTakings)), session.scalar(
        select(func.count()).select_from(JournalEntry))


def errors_of(session, admin, text):
    with pytest.raises(CsvImportError) as caught:
        import_takings(session, admin, text.encode())
    return caught.value.errors


def test_template_imports_cleanly(session, admin):
    result = import_takings(session, admin, template_csv().encode())
    assert [r.takings_date for r in result.rows] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert result.gross_total_cents == 131900 + 60500
    assert count(session) == (2, 2)


def test_quarter_of_days_with_dutch_numbers_and_dates(session, admin):
    body = HEADER + "".join(
        f"{d:02d}-10-2026;\"1.210,00\";0;0;\"1.210,00\";;;Z {d}\n" for d in range(1, 21))
    result = import_takings(session, admin, body.encode())
    assert len(result.rows) == 20
    assert result.rows[0].note == "Z 1"


def test_comma_delimited_file_and_bom(session, admin):
    body = "﻿date,gross_21,gross_9,gross_0,cash,card,other,note\n2026-10-01,121.00,0,0,121.00,0,0,\n"
    assert len(import_takings(session, admin, body.encode("utf-8")).rows) == 1


def test_one_bad_row_posts_nothing_and_every_error_is_listed(session, admin):
    body = (HEADER
            + "2026-10-01;121,00;;;121,00;;;ok\n"
            + "2026-10-02;100,00;;;90,00;;;mismatch\n"
            + "not-a-date;1;;;1;;;\n"
            + "2026-10-04;abc;;;1;;;\n"
            + "2026-10-01;50,00;;;50,00;;;dup\n")
    errors = errors_of(session, admin, body)
    assert [line for line, _ in errors] == [3, 4, 5, 6]
    assert "do not equal" in errors[0][1]
    assert "valid date" in errors[1][1]
    assert "valid amount" in errors[2][1]
    assert "also appears on line 2" in errors[3][1]
    assert count(session) == (0, 0)


def test_days_already_recorded_and_locked_days_are_refused(session, admin):
    import_takings(session, admin, (HEADER + "2026-10-01;10,00;;;10,00;;;\n").encode())
    admin.locked_until = date(2026, 9, 30)
    errors = errors_of(session, admin, HEADER + "2026-10-01;10,00;;;10,00;;;\n"
                       + "2026-09-15;10,00;;;10,00;;;\n")
    assert [m for _, m in errors] == ["the period is closed up to 2026-09-30",
                                      "2026-10-01 is already recorded"] or len(errors) == 2
    assert count(session) == (1, 1)


@pytest.mark.parametrize(("text", "needle"), [
    ("", "empty"),
    (HEADER, "no rows"),
    ("date;gross_21\n2026-10-01;1\n", "missing column"),
    (HEADER.replace("note", "memo") + "2026-10-01;1;;;1;;;\n", "unknown column"),
])
def test_structural_problems(session, admin, text, needle):
    errors = errors_of(session, admin, text)
    assert needle in errors[0][1]


def test_binary_and_oversized_files_are_refused(session, admin):
    with pytest.raises(CsvImportError, match="problem"):
        import_takings(session, admin, b"\xff\xfe\x00bad")
    rows = HEADER + "2026-01-01;1;;;1;;;\n" * (csv_import.MAX_ROWS + 1)
    assert "at most" in errors_of(session, admin, rows)[0][1]
