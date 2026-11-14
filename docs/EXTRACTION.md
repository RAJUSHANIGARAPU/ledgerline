# Document extraction (optional)

Ledgerline reads invoices in three steps, most trustworthy first: a UBL e-invoice (exact), the text layer of a PDF, and, only if you switch it on, a vision model that reads photos and scans. This page is about the last step.

It is **off by default**. With the default settings nothing leaves your server.

## Recommended setup for the pilot

- A PDF with a text layer, and a UBL e-invoice, are read on the server without any model. No data leaves the server for these.
- Switch the vision step on only if clients will send photos or scans. Use `LEDGERLINE_EXTRACTION_PROVIDER=anthropic` with `LEDGERLINE_EXTRACTION_MODEL=claude-haiku-5-5`: one sub-processor (Anthropic), the cheapest model, and every result is reviewed by a person before booking.
- `openrouter` stays available but is not the recommended path: it adds an intermediary and, depending on the model, a second provider you do not choose directly (see "What is sent, and to whom").
- Accuracy on real photos is not measured. Run 20 to 30 real documents first; if Haiku misreads too often, move to `claude-sonnet-5-5`.

## What is sent, and to whom

**Which documents.** The vision step runs on a PDF, JPEG or PNG that the UBL and PDF-text steps did not turn into a result. That is not limited to photos and scans. A PDF is also sent when its text layer exists but no invoice data could be found in the text, when the PDF could not be read, or when it is password protected (the provider may then fail to read it too). A PDF whose text was parsed into an invoice is not sent. (Checked in `src/ledgerline/inbox/extract/pipeline.py`, where the extractors run in order and the vision extractor accepts any PDF or image; and in `pdf_text.py`, which raises on no text layer, unreadable PDF, or no invoice data.)

**To whom, with `LEDGERLINE_EXTRACTION_PROVIDER=anthropic`:** the API of **Anthropic** (the provider of the Claude models) only. No other party receives the document. The request contains:

- the document itself (PDF, JPEG or PNG, base64-encoded) and a fixed instruction text;
- your API key, in the request header.

It does not contain the administration name, other documents, ledger data or user details. The reply is a set of proposed invoice fields. They go into the inbox as a proposal; nothing is booked until the owner reviews it, and every result carries the warning "Read from a photo or scan — check every figure before booking".

**To whom, with `LEDGERLINE_EXTRACTION_PROVIDER=openrouter`:** **OpenRouter** receives the request and passes it to the underlying model provider that serves the model you chose. That provider is a second recipient with its own data handling and retention rules (https://openrouter.ai/docs/features/privacy-and-logging: "Each provider on OpenRouter has its own data handling policies"). Which provider serves a request can depend on routing, so name both OpenRouter and the provider in your privacy notice and processing register, and check both sets of terms. In-region routing in the EU is offered on OpenRouter's Business and Enterprise plans only (same page, read 2026-11-14).

A supplier invoice contains the supplier's name, address, bank details and sometimes personal data of a sole trader. Sending it is a transfer of personal data to a processor. See "Your obligations" below.

## Enable it

1. Create an API key in the provider's console (console.anthropic.com), ideally in a workspace used only for this app, with a spend limit set there.
2. Set the environment variables for the app and the worker:

   ```
   LEDGERLINE_EXTRACTION_PROVIDER=anthropic
   LEDGERLINE_EXTRACTION_MODEL=claude-haiku-5-5
   ANTHROPIC_API_KEY=<your key>
   ```

   The key is read by the SDK from the environment. Ledgerline never logs or stores it. Keep it in your secrets store or `.env` file with restricted permissions.
3. Restart the app. If the provider is `anthropic` but no key is present, extraction stays off and a warning is logged.

To disable it again, set `LEDGERLINE_EXTRACTION_PROVIDER=none` (or remove it) and restart.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `LEDGERLINE_EXTRACTION_PROVIDER` | `none` | `none`, `anthropic` or `openrouter` |
| `LEDGERLINE_EXTRACTION_MODEL` | `claude-haiku-5-5` | model id sent to the API; for `openrouter` it must be an OpenRouter id such as `vendor/model-name` |
| `LEDGERLINE_EXTRACTION_MONTHLY_LIMIT` | `200` | documents read by the vision step per administration per calendar month (UTC) |
| `ANTHROPIC_API_KEY` | unset | the Anthropic API key |
| `OPENROUTER_API_KEY` | unset | the OpenRouter API key |

### OpenRouter (not the recommended path)

Set `LEDGERLINE_EXTRACTION_PROVIDER=openrouter`, `OPENROUTER_API_KEY` and `LEDGERLINE_EXTRACTION_MODEL`. There is no default model: the list of free models changes, so pick one on https://openrouter.ai/models, filtered on input modality "image" and on the `structured_outputs` parameter, and test it on a few real documents first. Models differ widely in how well they read photos. Free (`:free`) models are rate limited: 20 requests per minute, and 50 per day with fewer than 10 credits purchased, 1,000 per day with at least 10 (https://openrouter.ai/docs/api-reference/limits). A 429 leaves the document unextracted in the inbox for manual entry; it never fails the upload. The checks below are the same for every model and matter most for free ones.

### Monthly cap

Each document read by the vision step counts towards the cap, per administration. When the cap is reached, the vision step is skipped (a warning is logged) and the document stays in the inbox for manual entry. The count resets on the first of the month. Set `LEDGERLINE_EXTRACTION_MONTHLY_LIMIT=0` to turn the cap off; then your spend limit in the provider console is the only brake.

## Cost

This is an estimate, not a quote. The token counts are an assumption, not a measurement: about 3k input and 1k output tokens for a one-page receipt. Check them against your own usage in the provider console. The list prices below were read from https://platform.claude.com/docs/en/about-claude/pricing on 2026-11-14 and may change.

| Model | Input / output per million tokens | Estimated per document |
|---|---|---|
| `claude-opus-5-5` | US$4 / US$20 | about US$0.03 |
| `claude-sonnet-5-5` | US$2 / US$10 | about US$0.016 |
| `claude-haiku-5-5` | US$0.10 / US$0.50 (prompts up to 100k tokens) | under US$0.001 |

Multi-page PDFs cost more. At the default cap of 200 documents per month the Opus estimate is roughly US$6 per administration and the Haiku estimate under US$0.20.

### Choosing a model

Accuracy has not been measured on real documents, so this is a starting point, not a result. Crumpled phone photos are the hard case. For the pilot the recommended model is `claude-haiku-5-5`: cheap enough that the review step is the real control. The case against it: a cheaper model may misread more photos, and a misread that still adds up passes the checks below. So run 20 to 30 real invoices through it and compare the proposals with the paper; if it misreads too often, use `claude-sonnet-5-5` (half the price of Opus). Ledgerline leaves out the server-side refusal fallback for Haiku models, which do not offer it. Set the model with `LEDGERLINE_EXTRACTION_MODEL`.

## What is read

Supplier name, VAT number (BTW-id), KvK number and IBAN; invoice number, date and due date; currency; totals excl. VAT, VAT and incl. VAT; a credit-note flag; lines with description, amount, VAT rate and VAT amount; a deposit flag per line (statiegeld, emballage, borg); the VAT summary per rate (net and VAT); a "paid at delivery" flag (pin or cash); and reverse charge. If an invoice prints no lines but does print a VAT summary, one line per rate is created from the summary. Payment status is shown as a warning; it does not register a payment.

## How results are checked

The model is told to return `null` rather than guess, and to treat everything inside the document as data. Its output is constrained to a fixed schema. Models reached through OpenRouter may not follow it, so the shape is checked too and a deviation becomes a warning. After parsing, Ledgerline checks:

- total excl. VAT + VAT = total incl. VAT (1 cent tolerance);
- the lines add up to the total excl. VAT (1 cent);
- VAT per rate is about the rate times the base (2 cents), per line and per VAT summary row;
- the VAT summary adds up to the printed totals, and its rates are 0, 9 or 21;
- the IBAN passes the mod-97 check, the VAT number matches its country format (NL: `NL`, 9 digits, `B`, 2 digits) and the KvK number has 8 digits;
- the invoice date is not in the future, not before 2000 and not more than two years old; the due date is not before the invoice date or more than a year after it;
- a credit note has negative amounts, and a negative total is marked as a credit note;
- deposit lines and "paid at delivery" are called out;
- reverse charge ("btw verlegd", "reverse charge") is flagged, and a printed VAT amount on such an invoice raises a warning.

An identifier that fails its check stays visible but is not used to find the supplier. The supplier is matched on VAT number, then KvK number, then IBAN, then name. Failures show up as warnings on the review screen. They do not block you; the human review does.

## Your obligations

- **Processor agreement (AVG/GDPR).** Anthropic acts as your processor for this data (with `openrouter`: OpenRouter and the underlying provider). Accept or sign its data processing agreement, which is part of its commercial terms; check the provider's current terms for how to do so.
- **Retention and training.** How long the provider keeps API inputs and outputs, and whether they are used for training, is set by its current commercial terms and data-retention policy. Check those pages yourself before enabling this; this project does not state numbers it cannot guarantee.
- **Privacy notice.** Name the provider (or providers) in your privacy notice. The template under `/legal/privacy` has a ready-made section "Document extraction (optional)"; keep it only if you enable the feature, and fill in the processor details.
- **Region.** The provider may process data outside the EU. Check whether its terms include the transfer mechanism you need.

See also `docs/COMPLIANCE_NL.md`.
