import { expect, test } from '@playwright/test';
import { signIn } from './auth';

function receipt(name: string) {
  return {
    name,
    mimeType: 'application/pdf',
    buffer: Buffer.from(`%PDF-1.4\n% ${name}\n%%EOF`),
  };
}

test('a document captured offline is queued, then lands in the inbox exactly once', async ({ page, context }) => {
  const name = `offline-${Date.now()}.pdf`;
  await signIn(page);
  await page.goto('/capture');
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));

  await context.setOffline(true);
  await page.locator('input[type="file"][name="files"][multiple]').setInputFiles(receipt(name));
  await page.locator('input[name="note"]').fill('Fuel');
  await page.getByRole('button', { name: 'Upload to inbox' }).click();
  await expect(page.locator('#capture-queue')).toContainText('1 waiting to upload');
  await page.screenshot({ path: 'test-results/offline-queued.png', fullPage: true });

  await context.setOffline(false);
  await expect(page.locator('#capture-queue')).not.toContainText('waiting to upload', { timeout: 20_000 });
  await expect(page.locator('#capture-queue')).toContainText('1 queued upload sent');
  await page.screenshot({ path: 'test-results/offline-synced.png', fullPage: true });

  await page.goto('/inbox?tab=new');
  await expect(page.locator('a[href^="/inbox/"]').filter({ hasText: name })).toHaveCount(1);
});

test('a queued upload survives a reload and is not stored twice when sent again', async ({ page, context }) => {
  const name = `reload-${Date.now()}.pdf`;
  await signIn(page);
  await page.goto('/capture');
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));

  await context.setOffline(true);
  await page.locator('input[type="file"][name="files"][multiple]').setInputFiles(receipt(name));
  await page.getByRole('button', { name: 'Upload to inbox' }).click();
  await expect(page.locator('#capture-queue')).toContainText('1 waiting to upload');

  await context.setOffline(false);
  await page.reload();
  await expect(page.locator('#capture-queue')).toContainText('1 queued upload sent', { timeout: 20_000 });

  await page.locator('input[type="file"][name="files"][multiple]').setInputFiles(receipt(name));
  await page.getByRole('button', { name: 'Upload to inbox' }).click();
  await expect(page.locator('body')).toContainText('already in your inbox');

  await page.goto('/inbox?tab=new');
  await expect(page.locator('a[href^="/inbox/"]').filter({ hasText: name })).toHaveCount(1);
});
