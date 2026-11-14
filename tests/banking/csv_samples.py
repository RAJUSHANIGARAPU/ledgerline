"""Synthetic CSV exports. Names and IBANs are invented; the IBANs only pass the checksum."""
import csv
import io

RABO_IBAN = "NL44RABO0123456789"
OTHER_RABO_IBAN = "NL91ABNA0417164300"

# Header row exactly as in the Rabobank CSV format description (26 fields).
RABO_HEADER = [
    "IBAN/BBAN", "Munt", "BIC", "Volgnr", "Datum", "Rentedatum", "Bedrag", "Saldo na trn",
    "Tegenrekening IBAN/BBAN", "Naam tegenpartij", "Naam uiteindelijke partij",
    "Naam initiërende partij", "BIC tegenpartij", "Code", "Batch ID", "Transactiereferentie",
    "Machtigingskenmerk", "Incassant ID", "Betalingskenmerk", "Omschrijving-1",
    "Omschrijving-2", "Omschrijving-3", "Reden retour", "Oorspr bedrag", "Oorspr munt", "Koers",
]


def rabo_row(seq, day, amount, name, iban, desc, own=RABO_IBAN, currency="EUR", value=None):
    row = dict.fromkeys(RABO_HEADER, "")
    row.update({
        "IBAN/BBAN": own, "Munt": currency, "BIC": "RABONL2U", "Volgnr": str(seq),
        "Datum": day, "Rentedatum": value or day, "Bedrag": amount, "Saldo na trn": "+1000,00",
        "Tegenrekening IBAN/BBAN": iban, "Naam tegenpartij": name, "Omschrijving-1": desc,
    })
    return [row[h] for h in RABO_HEADER]


def rabo_csv(rows, encoding="utf-8") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(RABO_HEADER)
    writer.writerows(rows)
    return out.getvalue().encode(encoding)


def rabo_standard():
    return rabo_csv([
        rabo_row(101, "2026-06-02", "+121,00", "Klant BV", "NL02ABNA0123456789",
                 "Factuur 2026-0001"),
        rabo_row(102, "2026-06-03", "-50,00", "Leverancier BV", "NL86INGB0002445588",
                 "Inkoop juni"),
    ])


def generic_csv(rows, header, delimiter=";", encoding="utf-8", bom=False) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    data = out.getvalue().encode(encoding)
    return b"\xef\xbb\xbf" + data if bom else data
