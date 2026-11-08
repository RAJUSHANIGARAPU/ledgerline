# Dutch compliance notes

This document states what Ledgerline does and does not do. It is the project's reading of public sources, not legal or tax advice. Each claim is marked:

- **[verified]** read on the cited page on 2026-11-07.
- **[secondary]** from general knowledge or other sources, not confirmed on the cited page. Check it before relying on it.
- **[unverified]** I looked and could not confirm it.

Sources: [Invoice requirements](https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/zakelijk/btw/administratie_bijhouden/facturen_maken/factuureisen/factuureisen) - [Keeping records](https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/zakelijk/btw/administratie_bijhouden/administratie_bewaren/administratie_bewaren) - [KOR](https://www.belastingdienst.nl/kleineondernemersregeling) - [VAT return](https://www.belastingdienst.nl/wps/wcm/connect/nl/btw/content/ik-moet-btw-aangifte-doen-hoe-vul-ik-die-in) - [RGS](https://www.referentiegrootboekschema.nl/kbase) - [Peppol NL](https://www.peppol.nl/en/questions-about-peppol-service-providers)

## What Ledgerline is not

Not certified software, not an accountant, not a filing channel. It prepares figures and files; you check them and submit them yourself.

## VAT

| Topic | What the app does | Status |
|---|---|---|
| Rates 21%, 9%, 0% | Built-in VAT codes for these rates, plus reverse charge, EU and export codes | [secondary] The rates are not stated on the fetched pages. |
| 2026 change in how VAT returns are lodged | Nothing assumed. The app only produces figures per return rubric | [unverified] The fetched VAT return page mentions no 2026 change. Check the Belastingdienst site for current filing rules. |
| KOR (small business scheme) | A setting records that the administration uses the KOR. The app does not check the turnover limit or register you | [verified] Applies if turnover is at most EUR 20,000 per calendar year; participants do not charge VAT and cannot deduct input VAT; registration is a separate step with the Belastingdienst. Further conditions are on a separate page I did not read. |
| VAT return | Prepared, not filed | [verified] The page describes filing in Mijn Belastingdienst Zakelijk (log in with DigiD, eHerkenning or a European login), rows 1a, 3, 4 and 5b. [unverified] The page says nothing about filing from software. [secondary] Software filing normally goes through Digipoort and needs a PKIoverheid certificate; Ledgerline does none of this. |

## Invoices

[verified] The Belastingdienst page lists: supplier and customer legal name and address (a PO box alone is not enough), supplier VAT number, KvK number if registered, invoice date, sequential unique invoice number, description and quantity, supply date or advance-payment date, amount excluding VAT per rate, VAT rate, VAT amount. Looser rules for invoices up to EUR 100 and extra rules for cross-border supplies exist; the page links to them and I did not read them. The reverse-charge wording is not on that page [unverified].

Ledgerline numbers invoices gap-free per administration and year, and the invoice module is meant to carry these fields; check a generated sample against the list. The app does not check the content of free-text fields.

## E-invoicing files and Peppol

- UBL 2.1 files following Peppol BIS Billing 3.0 can be generated.
- [verified] Peppol requires a Peppol ID and a supported format (Peppol BIS Billing 3.0 and SI-UBL 2.0 are listed). Service providers must pass the NPa admission process.
- Ledgerline does **not** send over Peppol. [secondary] Sending needs a connection to the network through an access point provider; the fetched page does not say whether a business can send without one.

## Bank data

CAMT.053 and MT940 files are imported by upload. There is no PSD2 bank connection: [secondary] that needs a licensed account information service provider.

## RGS (Referentie Grootboekschema)

The default chart uses the project's own account codes. The `rgs_code` column is empty and RGS codes are not bundled. [unverified] The RGS knowledge base page names a proposal on licence-free use but states no reuse terms, so the dataset is not redistributed until the terms are confirmed. Reference: https://www.referentiegrootboekschema.nl/kbase

## Record retention

[verified] Records are kept for 7 years; for records about real estate, and for supplies under the EU one-stop-shop Union and Import schemes, 10 years. The period starts when the data stops being current ("Vervalt de actualiteitswaarde? Dan begint de bewaartermijn"): a 4-year lease contract stays in the administration for those 4 years and the 7 years start afterwards. Debtor and creditor records, sales and purchase records and the general ledger are always kept 7 years. The page does not say where records may be stored [unverified].

The app shows, per contact, the last record date and the date the 7-year period ends, computed as 31 December of the year of the last record plus 7 years. That is a conservative simplification of "stops being current", not the legal test. The 10-year rule is shown as text; it is not tracked per record.

## Personal data (GDPR / AVG)

- **Processing register** [secondary]: the operator should keep a register of processing (users, contacts, bookkeeping records) with purpose, legal basis, recipients and retention. The privacy template in `/legal/privacy` lists the categories as a starting point.
- **Processor agreement** [secondary]: if a hosting provider can access the data, sign a data processing agreement with it.
- **Export**: per contact (JSON of contact data, invoices, journal lines, matching bank transactions, audit events) and the full administration as a ZIP (JSON per table plus attachments). Owner only, and each export is audited.
- **Erasure and its limit** [secondary]: the AVG lets controllers refuse erasure where a legal obligation requires keeping the data. Fiscal retention is such an obligation, so bookkeeping records cannot simply be deleted on request. The app therefore anonymises instead of deleting.

### What anonymise does, and the trade-off

Anonymise blanks name (replaced by "Anonymised contact #id"), email, address, postcode, city, IBAN and KvK number, sets `anonymised_at` and writes an audit event. Invoices, journal entries and bank transactions are not touched.

It **keeps** the VAT number, the country and the contact kind. Reasoning: the VAT number is a fiscal identifier needed to support the VAT treatment of past supplies (for example intra-EU supplies and reverse charge) and is the field an audit most likely asks about; for companies it is not personal data, though for a sole trader it may be. This is a judgement, not a rule: if the VAT number identifies a natural person and you cannot justify keeping it, blank it by hand after the retention period.

The honest cost: issued invoice PDFs and UBL files are regenerated from the stored data, not archived as files. After anonymisation a regenerated invoice shows the anonymised name and no address, so it no longer meets the invoice content rules above, and the original document is not recoverable from the app. If you must be able to show the original invoice for the whole retention period, keep copies of the issued documents (or the full ZIP export) before you anonymise, or wait until the retention date shown on the contact. The app lets the owner anonymise earlier, after an explicit confirmation, because the data subject's request and the retention duty can only be balanced by a person.

Bank transactions keep the counterparty name and IBAN as imported from the bank file; anonymise does not rewrite them.

### Not implemented

Deletion of the personal data after the retention date is manual: the app shows the date but does not run a purge.

## Document extraction by an external provider (optional)

This applies only if the operator sets `LEDGERLINE_EXTRACTION_PROVIDER=anthropic`. By default nothing is sent anywhere.

- **What is transferred:** the received invoice or receipt (PDF, JPEG, PNG), when the UBL and PDF-text steps could not read it. Such a document can contain personal data (names and bank details of suppliers, including sole traders).
- **Recipient:** Anthropic, as processor. The operator needs its data processing agreement under the AVG and should check its current commercial terms and data-retention policy for retention, training use and the transfer mechanism for processing outside the EU.
- **Transparency:** name the provider in the privacy notice (template section "Document extraction (optional)") and in the processing register. Without this, the feature should stay off.
- **Control:** results are proposals; the owner reviews every figure before booking. A monthly cap per administration limits volume. Details: `docs/EXTRACTION.md`.
