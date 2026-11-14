import { expect, test } from '@playwright/test';
import { signIn } from './auth';

// Needs a freshly seeded database: the contact and invoice numbers may only be imported once.
const CONTACTS = [
  'name;kind;email;address;postcode;city;country;vat_number;kvk;iban',
  'E2E Klant BV;customer;boekhouding@e2e-klant.example;Teststraat 1;1011 AB;Amsterdam;NL;;90000001;',
  'E2E Leverancier;supplier;;;;;;;;',
].join('\r\n');

const camt = (iban: string) => `<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>
<GrpHdr><MsgId>E2E-1</MsgId><CreDtTm>2026-01-16T08:00:00</CreDtTm></GrpHdr>
<Stmt><Id>E2E-STMT</Id><Acct><Id><IBAN>${iban}</IBAN></Id><Ccy>EUR</Ccy></Acct>
<Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-01-14</Dt></Dt></Bal>
<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">1121.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-01-15</Dt></Dt></Bal>
<Ntry><Amt Ccy="EUR">121.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>BOOK</Sts>
<BookgDt><Dt>2026-01-15</Dt></BookgDt><ValDt><Dt>2026-01-15</Dt></ValDt><AcctSvcrRef>E2E-REF-1</AcctSvcrRef>
<NtryDtls><TxDtls><Refs><EndToEndId>NOTPROVIDED</EndToEndId></Refs>
<RltdPties><Dbtr><Nm>E2E Klant BV</Nm></Dbtr></RltdPties>
<RmtInf><Ustrd>Betaling factuur E2E-0001</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
</Stmt></BkToCstmrStmt></Document>`;

test('contacts CSV: upload, preview, confirm', async ({ page }) => {
  page.on('dialog', (dialog) => dialog.accept());
  await signIn(page);
  await page.goto('/contacts');
  await page.getByRole('link', { name: 'Import CSV' }).click();
  await expect(page).toHaveURL('/contacts/import');

  await page.locator('input[type="file"][name="file"]').setInputFiles({
    name: 'contacts.csv', mimeType: 'text/csv', buffer: Buffer.from(CONTACTS),
  });
  await page.getByRole('button', { name: 'Preview' }).click();
  const preview = page.getByTestId('import-preview');
  await expect(preview.locator('tr[data-action="create"]')).toHaveCount(2);
  await expect(preview).toContainText('E2E Klant BV');
  await expect(preview.locator('tr[data-action="error"]')).toHaveCount(0);

  await page.getByTestId('import-confirm').click();
  await expect(page).toHaveURL('/contacts');
  await expect(page.locator('.flash-success')).toContainText('Imported 2 contacts');
  await expect(page.locator('body')).toContainText('E2E Klant BV');

  await page.goto('/contacts/import');
  await page.locator('input[type="file"][name="file"]').setInputFiles({
    name: 'contacts.csv', mimeType: 'text/csv', buffer: Buffer.from(CONTACTS),
  });
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.locator('tr[data-action="skip"]')).toHaveCount(2);
});

test('an opening item is matched by an imported bank payment and becomes paid', async ({ page }) => {
  page.on('dialog', (dialog) => dialog.accept());
  await signIn(page);

  await page.goto('/setup/opening-items');
  const form = page.getByTestId('opening-item-form');
  await form.locator('input[name="start_date"]').fill('2026-01-01');
  await form.locator('select[name="type"]').selectOption('sales');
  await form.locator('input[name="number"]').fill('E2E-0001');
  await form.locator('input[name="contact"]').fill('E2E Klant BV');
  await form.locator('input[name="invoice_date"]').fill('2025-12-10');
  await form.locator('input[name="due_date"]').fill('2026-01-10');
  await form.locator('input[name="total"]').fill('121,00');
  await form.locator('input[name="open_amount"]').fill('121,00');
  await form.getByRole('button', { name: 'Add opening item' }).click();

  await expect(page.locator('.flash-success').first()).toContainText('E2E-0001');
  await expect(page.getByTestId('opening-items')).toContainText('E2E-0001');
  await expect(page.getByTestId('balance-check')).toContainText('Assets = Liabilities + Equity on 01-01-2026');
  await expect(page.getByTestId('balance-check')).toContainText('balanced');

  await page.goto('/bank');
  const iban = (await page.locator('body').innerText()).match(/NL\d{2}[A-Z]{4}\d{10}/)![0];
  await page.locator('input[type="file"][name="file"]').first().setInputFiles({
    name: 'e2e.xml', mimeType: 'application/xml', buffer: Buffer.from(camt(iban)),
  });
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await expect(page.locator('.flash-success')).toBeVisible();

  await page.goto('/bank/reconcile');
  const card = page.locator('section.card').filter({ hasText: 'E2E Klant BV' });
  await expect(card).toContainText('E2E-0001');
  await expect(card).toContainText('exact amount');
  await card.getByRole('button', { name: 'Match selected' }).first().click();
  await expect(page.locator('.flash-success')).toContainText('Transaction matched');

  await page.goto('/sales');
  const row = page.locator('tr').filter({ hasText: 'E2E-0001' });
  await expect(row.locator('.badge-paid')).toBeVisible();
  await expect(row).toContainText('opening');
});
