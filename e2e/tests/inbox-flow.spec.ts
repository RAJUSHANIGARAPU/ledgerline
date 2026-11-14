import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { signIn } from './auth';

// Needs a freshly seeded database: the supplier reference may only be booked once.
test('a supplier PDF uploaded to the inbox becomes a purchase draft', async ({ page }) => {
  await signIn(page);
  await page.goto('/inbox');
  await page.locator('input[type="file"][name="files"]').setInputFiles(
    path.join(__dirname, '..', 'fixtures', 'supplier-invoice.pdf'),
  );
  await page.getByRole('button', { name: 'Upload' }).click();

  await page.goto('/inbox');
  await page.locator('a[href^="/inbox/"]').filter({ hasText: /supplier-invoice/ }).first().click();
  await expect(async () => {
    await page.reload();
    await expect(page.locator('input[name="line_amount"]').first()).toHaveValue(/120[,.]00/);
  }).toPass({ timeout: 20_000 });

  await expect(page.locator('input[name="line_vat"]').first()).toHaveValue(/25[,.]20/);
  await expect(page.getByText('DV-2026-0412').or(page.locator('input[value="DV-2026-0412"]')).first()).toBeVisible();

  await page.locator('#contact-select').selectOption('new');
  await page.getByRole('button', { name: 'Create purchase draft' }).click();
  await expect(page).toHaveURL(/\/purchases\/\d+/);
  await expect(page.locator('body')).toContainText('145,20');
});

test('an inbox with a long file name does not scroll the page sideways', async ({ page }) => {
  await signIn(page);
  await page.goto('/inbox');
  const original = fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'supplier-invoice.pdf'));
  await page.locator('input[type="file"][name="files"]').setInputFiles({
    name: `${'Invoice_with_a_very_long_descriptive_file_name_'.repeat(4)}copy.pdf`,
    mimeType: 'application/pdf',
    buffer: Buffer.concat([original, Buffer.from('\n% another copy\n')]),
  });
  await page.getByRole('button', { name: 'Upload' }).click();
  for (const width of [1280, 400]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/inbox?tab=review');
    await expect(page.locator('a[href^="/inbox/"]').filter({ hasText: /very_long/ })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `page wider than the ${width}px viewport`).toBeLessThanOrEqual(0);
  }
});

test('phone capture page and devices page render', async ({ page }) => {
  await signIn(page);
  for (const url of ['/capture', '/settings/devices', '/setup', '/help']) {
    const response = await page.goto(url);
    expect(response?.status(), url).toBe(200);
  }
  await page.goto('/capture');
  await expect(page.locator('input[type="file"][capture="environment"]')).toHaveCount(1);
});
