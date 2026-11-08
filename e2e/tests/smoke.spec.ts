import { expect, test } from '@playwright/test';
import { signIn } from './auth';

const PAGES = [
  '/', '/sales', '/purchases', '/bank', '/ledger', '/accounts', '/vat', '/vat/icp',
  '/reports', '/contacts', '/settings', '/settings/privacy', '/inbox', '/capture',
  '/settings/devices', '/setup', '/help',
];

test('every main page renders without errors', async ({ page }) => {
  const problems: string[] = [];
  page.on('console', (msg) => msg.type() === 'error' && problems.push(msg.text()));
  page.on('pageerror', (err) => problems.push(err.message));
  await signIn(page);
  for (const path of PAGES) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(200);
    await expect(page.locator('h1.page-title'), path).not.toBeEmpty();
    await page.screenshot({ path: `test-results/pages${path.replace(/\//g, '_') || '_home'}.png`, fullPage: true });
  }
  expect(problems).toEqual([]);
});

test('legal pages are public', async ({ page }) => {
  for (const path of ['/legal/privacy', '/legal/terms', '/legal/cookies', '/legal/disclaimer']) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(200);
  }
});
