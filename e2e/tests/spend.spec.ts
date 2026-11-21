import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { signIn } from './auth';

// Demo data (src/ledgerline/demo.py): three suppliers, three customers. The customers only ever
// appear on sales invoices, so they must never show up on the Spend page.
const CUSTOMER = 'Bakkerij De Zonnebloem';
const SUPPLIER = 'Cloudhuis Hosting';
const OTHER_SUPPLIER = 'Belbundel Mobiel';

const BOOKED_ROWS = 'table#spend-rows tbody tr[data-invoice-id]:not(.is-draft)';

/** "€ 1.234,56" or "-1,234.56" to integer cents: keep the digits and the sign. */
function cents(text: string | null): number {
  const digits = (text ?? '').replace(/[^\d]/g, '');
  const value = Number(digits);
  return /-|−/.test(text ?? '') ? -value : value;
}

async function total(page: Page, key: string): Promise<number> {
  return cents(await page.locator(`#spend-totals [data-total="${key}"]`).textContent());
}

async function footerCents(page: Page): Promise<number[]> {
  const cells = await page.locator('table#spend-rows tfoot tr').first().locator('td, th').allTextContents();
  return cells.filter((c) => /\d/.test(c)).map((c) => cents(c));
}

async function openSpend(page: Page, query = '') {
  const response = await page.goto(`/spend${query}`);
  expect(response?.status()).toBe(200);
  await expect(page.locator('#spend-totals')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test('the navigation leads to the spend overview', async ({ page }) => {
  await page.getByRole('link', { name: 'Spend', exact: true }).first().click();
  await expect(page).toHaveURL(/\/spend$/);
  await expect(page.locator('form#spend-filter')).toBeVisible();
  await expect(page.locator('table#spend-rows')).toBeVisible();
  for (const id of ['spend-by-supplier', 'spend-by-month', 'spend-by-category']) {
    await expect(page.locator(`section#${id}`)).toBeVisible();
  }
});

test('only purchase suppliers are listed, never sales customers', async ({ page }) => {
  await openSpend(page, '?preset=all');
  const body = page.locator('table#spend-rows tbody');
  await expect(body.locator('tr[data-invoice-id]').first()).toBeVisible();
  await expect(body).toContainText(SUPPLIER);
  await expect(body).toContainText(OTHER_SUPPLIER);
  await expect(page.locator('table#spend-rows')).not.toContainText(CUSTOMER);
  await expect(page.locator('#spend-by-supplier')).not.toContainText(CUSTOMER);
  await expect(page.locator('select[name="contact_id"] option', { hasText: CUSTOMER })).toHaveCount(0);
});

test('the totals tiles equal the totals row of the table', async ({ page }) => {
  await openSpend(page, '?preset=all');
  const rows = await page.locator(BOOKED_ROWS).count();
  expect(rows).toBeGreaterThan(0);
  expect(await total(page, 'count')).toBe(rows);

  const footer = await footerCents(page);
  for (const key of ['excl', 'vat', 'incl', 'open']) {
    expect(footer, `tfoot has the ${key} total`).toContain(await total(page, key));
  }
  expect(await total(page, 'excl') + (await total(page, 'vat'))).toBe(await total(page, 'incl'));
});

test('period presets split the bills: this year plus last year is all time', async ({ page }) => {
  const counts: Record<string, number> = {};
  for (const preset of ['year', 'last_year', 'all']) {
    await page.goto('/spend');
    await page.locator(`a[data-preset="${preset}"]`).click();
    await expect(page).toHaveURL(new RegExp(`preset=${preset}`));
    counts[preset] = await total(page, 'count');
    expect(await page.locator(BOOKED_ROWS).count()).toBe(counts[preset]);
  }
  expect(counts.all).toBeGreaterThan(0);
  expect(counts.all).toBeGreaterThanOrEqual(counts.year);
  expect(counts.year + counts.last_year).toBe(counts.all);
});

test('explicit dates narrow the list', async ({ page }) => {
  await openSpend(page, '?preset=all');
  const all = await total(page, 'count');
  const future = new Date().getFullYear() + 5;
  await page.locator('input[name="date_from"]').fill(`${future}-01-01`);
  await page.locator('input[name="date_to"]').fill(`${future}-12-31`);
  await page.locator('form#spend-filter').getByRole('button', { name: 'Apply' }).click();
  await expect(page.locator('#spend-totals')).toBeVisible();
  expect(await total(page, 'count')).toBe(0);
  expect(all).toBeGreaterThan(0);
});

test('the supplier filter narrows the rows and the totals', async ({ page }) => {
  await openSpend(page, '?preset=all');
  const allCount = await total(page, 'count');
  const allIncl = await total(page, 'incl');

  await page.locator('select[name="contact_id"]').selectOption({ label: SUPPLIER });
  await page.locator('form#spend-filter').getByRole('button', { name: 'Apply' }).click();
  await expect(page).toHaveURL(/contact_id=\d+/);

  const rows = page.locator(BOOKED_ROWS);
  const n = await rows.count();
  expect(n).toBeGreaterThan(0);
  expect(n).toBeLessThan(allCount);
  for (const text of await rows.allTextContents()) expect(text).toContain(SUPPLIER);
  expect(await total(page, 'count')).toBe(n);
  expect(await total(page, 'incl')).toBeLessThan(allIncl);
});

test('drafts are listed on request but never counted in the totals', async ({ page }) => {
  // Create a purchase draft; the demo data has none. It stays behind, which is harmless: the
  // reference is unique per run and drafts are excluded from every total.
  const reference = `E2E-DRAFT-${Date.now()}`;
  const today = new Date().toISOString().slice(0, 10);
  await page.goto('/purchases/new');
  await page.locator('select[name="contact_id"]').selectOption({ label: SUPPLIER });
  await page.locator('input[name="supplier_reference"]').fill(reference);
  await page.locator('input[name="invoice_date"]').fill(today);
  await page.locator('input[name="due_date"]').fill(today);
  await page.locator('input[name="line_description"]').first().fill('Spend page draft');
  await page.locator('input[name="line_amount"]').first().fill('10,00');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(page).toHaveURL(/\/purchases\/\d+/);

  await openSpend(page, '?preset=all');
  const countBefore = await total(page, 'count');
  const inclBefore = await total(page, 'incl');
  await expect(page.locator('table#spend-rows tbody tr.is-draft')).toHaveCount(0);
  await expect(page.locator('table#spend-rows')).not.toContainText(reference);

  await page.locator('input[name="drafts"]').check();
  await page.locator('form#spend-filter').getByRole('button', { name: 'Apply' }).click();
  await expect(page).toHaveURL(/drafts=/);
  const draftRow = page.locator('table#spend-rows tbody tr.is-draft', { hasText: reference });
  await expect(draftRow).toHaveCount(1);
  expect(await total(page, 'count')).toBe(countBefore);
  expect(await total(page, 'incl')).toBe(inclBefore);
});

test('the CSV export has a header and the same rows as the table', async ({ page }) => {
  await openSpend(page, '?preset=all');
  const expected = await page.locator(BOOKED_ROWS).count();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('a#spend-export').click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^spend.*\.csv$/);
  const file = await download.path();
  const lines = fs.readFileSync(file!, 'utf8').split(/\r?\n/).filter((l) => l.trim() !== '');
  expect(lines[0]).toMatch(/supplier/i);
  expect(lines[0]).toMatch(/invoice/i);
  expect(lines.length - 1).toBe(expected);
  expect(lines.slice(1).join('\n')).not.toContain(CUSTOMER);
});

test('a bulk upload lands in the inbox and shows up as waiting for review', async ({ page }) => {
  await openSpend(page);
  const before = (await page.locator('#spend-pending').count())
    ? Number(((await page.locator('#spend-pending').textContent()) ?? '').match(/\d+/)?.[0] ?? 0)
    : 0;

  // Three distinct files: the inbox stores each document once per content hash.
  const original = fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'supplier-invoice.pdf'));
  const stamp = Date.now();
  const files = [1, 2, 3].map((n) => ({
    name: `bulk-${stamp}-${n}.pdf`,
    mimeType: 'application/pdf',
    buffer: Buffer.concat([original, Buffer.from(`\n% bulk copy ${stamp} ${n}\n`)]),
  }));
  await page.locator('form#spend-upload input[type="file"][name="files"]').setInputFiles(files);
  await page.locator('form#spend-upload').locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/inbox/);
  for (const f of files) {
    await expect(page.locator('a[href^="/inbox/"]').filter({ hasText: f.name })).toBeVisible();
  }

  await openSpend(page);
  await expect(page.locator('#spend-pending')).toBeVisible();
  const after = Number(((await page.locator('#spend-pending').textContent()) ?? '').match(/\d+/)?.[0] ?? 0);
  expect(after).toBeGreaterThanOrEqual(before + 3);
  expect(after).toBeGreaterThanOrEqual(3);
});
