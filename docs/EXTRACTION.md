# Document extraction (optional)

Ledgerline reads invoices in three steps, most trustworthy first: a UBL e-invoice (exact), the text layer of a PDF, and, only if you switch it on, a vision model that reads photos and scans. This page is about the last step.

It is **off by default**. With the default settings nothing leaves your server.

## What is sent, and to whom

When enabled, each document that the first two steps could not read is sent to the API of **Anthropic** (the provider of the Claude models). The request contains:

- the document itself (PDF, JPEG or PNG, base64-encoded) and a fixed instruction text;
- your API key, in the request header.

It does not contain the administration name, other documents, ledger data or user details. The reply is a set of proposed invoice fields. They go into the inbox as a proposal; nothing is booked until the owner reviews it, and every result carries the warning "Read from a photo or scan — check every figure before booking".

A supplier invoice contains the supplier's name, address, bank details and sometimes personal data of a sole trader. Sending it is a transfer of personal data to a processor. See "Your obligations" below.

## Enable it

1. Create an API key in the provider's console (console.anthropic.com), ideally in a workspace used only for this app, with a spend limit set there.
2. Set the environment variables for the app and the worker:

   ```
   LEDGERLINE_EXTRACTION_PROVIDER=anthropic
   ANTHROPIC_API_KEY=<your key>
   ```

   The key is read by the SDK from the environment. Ledgerline never logs or stores it. Keep it in your secrets store or `.env` file with restricted permissions.
3. Restart the app. If the provider is `anthropic` but no key is present, extraction stays off and a warning is logged.

To disable it again, set `LEDGERLINE_EXTRACTION_PROVIDER=none` (or remove it) and restart.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `LEDGERLINE_EXTRACTION_PROVIDER` | `none` | `none` or `anthropic` |
| `LEDGERLINE_EXTRACTION_MODEL` | `claude-opus-5-5` | model id sent to the API |
| `LEDGERLINE_EXTRACTION_MONTHLY_LIMIT` | `200` | documents read by the vision step per administration per calendar month (UTC) |
| `ANTHROPIC_API_KEY` | unset | the API key |

### Monthly cap

Each document read by the vision step counts towards the cap, per administration. When the cap is reached, the vision step is skipped (a warning is logged) and the document stays in the inbox for manual entry. The count resets on the first of the month. Set `LEDGERLINE_EXTRACTION_MONTHLY_LIMIT=0` to turn the cap off; then your spend limit in the provider console is the only brake.

## Cost

This is an estimate, not a quote. At the list prices of Claude Opus 5.5 (US$4 per million input tokens, US$20 per million output tokens; check the provider's pricing page), a one-page receipt is roughly 2-3k input tokens and about 1k output tokens, which comes to about US$0.03 per document. Multi-page PDFs cost more, because every page is read as an image as well as text. At the default cap of 200 documents per month the estimate is roughly US$6 per administration.

### A cheaper model

Set `LEDGERLINE_EXTRACTION_MODEL` to another vision-capable model id from the provider's model list. Smaller models cost less per document but misread blurry figures more often. Ledgerline sends structured-output and effort parameters; confirm that the model you choose supports them by extracting a few real documents and comparing the result with the paper before relying on it. The totals and VAT reconciliation warnings help, but they only catch errors that break the arithmetic.

## How results are checked

The model is told to return `null` rather than guess, and to treat everything inside the document as data. Its output is constrained to a fixed schema. After parsing, Ledgerline checks:

- total excl. VAT + VAT = total incl. VAT (1 cent tolerance);
- the lines add up to the total excl. VAT (1 cent);
- VAT per rate is about the rate times the base (2 cents);
- the invoice date is not in the future and not before 2000;
- reverse charge ("btw verlegd", "reverse charge") is flagged, and a printed VAT amount on such an invoice raises a warning.

Failures show up as warnings on the review screen. They do not block you; the human review does.

## Your obligations

- **Processor agreement (AVG/GDPR).** Anthropic acts as your processor for this data. Accept or sign its data processing agreement, which is part of its commercial terms; check the provider's current terms for how to do so.
- **Retention and training.** How long the provider keeps API inputs and outputs, and whether they are used for training, is set by its current commercial terms and data-retention policy. Check those pages yourself before enabling this; this project does not state numbers it cannot guarantee.
- **Privacy notice.** Name the provider in your privacy notice. The template under `/legal/privacy` has a ready-made section "Document extraction (optional)"; keep it only if you enable the feature, and fill in the processor details.
- **Region.** The provider may process data outside the EU. Check whether its terms include the transfer mechanism you need.

See also `docs/COMPLIANCE_NL.md`.
