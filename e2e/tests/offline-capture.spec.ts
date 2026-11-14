import { expect, test, type Page } from '@playwright/test';
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

const queued = (page: Page) =>
  page.evaluate(async () => (await (window as any).captureQueue.list()).map((i: any) => i.name));

async function seed(page: Page, names: string[], who: { userId: number; tenantId: number }) {
  await page.evaluate(
    async ({ names, who }) => {
      const files = names.map((n) => new File([`%PDF-1.4\n% ${n}\n%%EOF`], n, { type: 'application/pdf' }));
      const admin = { id: Number(document.querySelector<HTMLFormElement>('#capture-form')!.dataset.adminId), name: 'Demo' };
      await (window as any).captureQueue.addMany(files, '', admin, who);
    },
    { names, who },
  );
}

const ownIdentity = (page: Page) =>
  page.evaluate(async () => {
    const { userId, tenantId } = await (await fetch('/capture/context')).json();
    return { userId, tenantId };
  });

test('signing out removes queued uploads and another user\'s items are never sent or shown', async ({ page, context }) => {
  const mine = `mine-${Date.now()}.pdf`;
  const theirs = `theirs-${Date.now()}.pdf`;
  await signIn(page);
  await page.goto('/capture');
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  const who = await ownIdentity(page);

  await seed(page, [theirs], { userId: who.userId + 1000, tenantId: who.tenantId });
  await page.reload();
  await expect(page.locator('#capture-queue')).not.toContainText(theirs);
  await expect.poll(() => queued(page)).toEqual([]);

  await context.setOffline(true);
  await page.locator('input[type="file"][name="files"][multiple]').setInputFiles(receipt(mine));
  await page.getByRole('button', { name: 'Upload to inbox' }).click();
  await expect(page.locator('#capture-queue')).toContainText('1 waiting to upload');

  await page.getByRole('button', { name: 'Sign out' }).click({ noWaitAfter: true });
  await context.setOffline(false);
  await page.waitForURL(/\/login/);
  const left = await page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        const open = indexedDB.open('ledgerline-capture');
        open.onupgradeneeded = () => {
          open.transaction!.abort();
          resolve(0);
        };
        open.onsuccess = () => {
          const names = [...open.result.objectStoreNames];
          if (!names.includes('uploads')) return resolve(0);
          const count = open.result.transaction('uploads').objectStore('uploads').count();
          count.onsuccess = () => resolve(count.result);
        };
      }),
  );
  expect(left).toBe(0);

  await signIn(page);
  await page.goto('/inbox?tab=new');
  await expect(page.locator('a[href^="/inbox/"]').filter({ hasText: mine })).toHaveCount(0);
  await expect(page.locator('a[href^="/inbox/"]').filter({ hasText: theirs })).toHaveCount(0);
});

test('a permanently refused item is marked failed and does not block the next one', async ({ page }) => {
  const bad = `bad-${Date.now()}.pdf`;
  const good = `good-${Date.now()}.pdf`;
  await signIn(page);
  await page.goto('/capture');
  const who = await ownIdentity(page);
  await page.route('**/capture', async (route) => {
    if (route.request().method() === 'POST' && (route.request().postData() ?? '').includes(bad)) {
      return route.fulfill({ status: 415, contentType: 'application/json', body: '{"detail":"nope"}' });
    }
    return route.continue();
  });

  await seed(page, [bad, good], who);
  await page.evaluate(() => (window as any).captureQueue.flush());
  await page.reload();
  await expect(page.locator('#capture-queue')).toContainText(bad);
  await expect(page.locator('#capture-queue')).toContainText('not supported');
  await expect(page.getByRole('button', { name: 'Discard' })).toBeVisible();
  await page.screenshot({ path: 'test-results/offline-failed.png', fullPage: true });

  await page.goto('/inbox?tab=new');
  await expect(page.locator('a[href^="/inbox/"]').filter({ hasText: good })).toHaveCount(1);
  await page.goto('/capture');
  await page.getByRole('button', { name: 'Discard' }).click();
  await expect.poll(() => queued(page)).toEqual([]);
});

test('a multi-file submission is queued whole or not at all when storage fails', async ({ page, context }) => {
  const names = [`a-${Date.now()}.pdf`, `b-${Date.now()}.pdf`];
  await signIn(page);
  await page.goto('/capture');
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  await context.setOffline(true);

  await page.evaluate(() => {
    const original = IDBObjectStore.prototype.add;
    let calls = 0;
    IDBObjectStore.prototype.add = function (...args) {
      if (++calls === 2) throw new DOMException('full', 'QuotaExceededError');
      return original.apply(this, args);
    };
    (window as any).restoreAdd = () => (IDBObjectStore.prototype.add = original);
  });
  await page.locator('input[type="file"][name="files"][multiple]').setInputFiles(names.map(receipt));
  await page.getByRole('button', { name: 'Upload to inbox' }).click();
  await expect(page.locator('#capture-previews')).toContainText('out of storage');
  expect(await queued(page)).toEqual([]);

  await page.evaluate(() => (window as any).restoreAdd());
  await page.getByRole('button', { name: 'Upload to inbox' }).click();
  await expect(page.locator('#capture-queue')).toContainText('2 waiting to upload');
});
