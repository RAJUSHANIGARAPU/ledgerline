from ledgerline.reports.export import Cents, csv_response, to_csv


def test_plain_csv_uses_comma_and_dot():
    text = to_csv(["a", "b"], [("x", Cents(123456)), ("y", Cents(-5))], comma=False)
    assert text == "a,b\r\nx,1234.56\r\ny,-0.05\r\n"


def test_decimal_comma_switches_to_semicolon_separator():
    text = to_csv(["a", "b"], [("x", Cents(123456)), ("y", Cents(7))], comma=True)
    assert text == "a;b\r\nx;1234,56\r\ny;0,07\r\n"


def test_text_that_looks_like_a_formula_is_defused():
    text = to_csv(["n"], [("=SUM(A1)",), ("+1",), ("@x",), ("-2",), ("plain",)], comma=False)
    assert text.splitlines()[1:] == ["'=SUM(A1)", "'+1", "'@x", "'-2", "plain"]


def test_quotes_delimiters_and_counts():
    text = to_csv(["n", "c"], [('He said "hi"; ok', 3)], comma=True)
    assert text.splitlines()[1] == '"He said ""hi""; ok";3'


def test_response_headers_and_bom():
    response = csv_response("r.csv", ["a"], [("é",)])
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="r.csv"'
    assert response.body.startswith(b"\xef\xbb\xbfa")
