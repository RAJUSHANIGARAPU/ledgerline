import { expect, test } from '@playwright/test';
import { signIn } from './auth';

test('create, issue and download an invoice', async ({ page }) => {
  page.on('dialog', (dialog) => dialog.accept());
  await signIn(page);
  await page.goto('/sales/new');
  await page.locator('select[name="contact_id"]').selectOption({ label: 'Fietsenmaker Van Dijk' });
  await page.locator('input[name="line_description"]').first().fill('Workshop, half day');
  await page.locator('input[name="line_qty"]').first().fill('2');
  await page.locator('input[name="line_price"]').first().fill('250,00');
  await page.locator('select[name="line_vat_code"]').first().selectOption('V21');
  await expect(page.locator('#lines')).toContainText('605,00');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await page.getByRole('button', { name: 'Issue invoice' }).click();
  await expect(page.locator('.flash-success')).toBeVisible();
  await expect(page.locator('.badge-open').first()).toBeVisible();

  for (const kind of ['pdf', 'ubl']) {
    const href = await page.locator(`a[href$="/${kind}"]`).first().getAttribute('href');
    const response = await page.request.get(href!);
    expect(response.status(), kind).toBe(200);
    const body = await response.body();
    if (kind === 'pdf') expect(body.subarray(0, 5).toString()).toBe('%PDF-');
    else expect(body.toString()).toContain('<cbc:PayableAmount currencyID="EUR">605.00</cbc:PayableAmount>');
  }
});

test('dashboard chart and stats render with demo data', async ({ page }) => {
  await signIn(page);
  await expect(page.locator('.stat').first()).toBeVisible();
  const box = await page.locator('canvas').first().boundingBox();
  expect(box!.height).toBeGreaterThan(100);
});
