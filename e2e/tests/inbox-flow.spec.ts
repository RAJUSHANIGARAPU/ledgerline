import { expect, test } from '@playwright/test';
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

test('phone capture page and devices page render', async ({ page }) => {
  await signIn(page);
  for (const url of ['/capture', '/settings/devices', '/setup', '/help']) {
    const response = await page.goto(url);
    expect(response?.status(), url).toBe(200);
  }
  await page.goto('/capture');
  await expect(page.locator('input[type="file"][capture="environment"]')).toHaveCount(1);
});
