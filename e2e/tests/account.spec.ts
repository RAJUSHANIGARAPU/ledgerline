import { execSync } from 'node:child_process';
import { createHmac } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { signIn } from './auth';

// Development mode prints invitation links to the app log when SMTP is off.
const LOG_COMMAND = process.env.LEDGERLINE_LOG_COMMAND ?? 'docker compose logs --no-color app';
const PASSWORD = 'e2e-fresh-passphrase-1';

function base32Decode(input: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of input.replace(/=+$/, '').toUpperCase()) {
    bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

function totp(secret: string, offsetSteps = 0): string {
  const counter = Math.floor(Date.now() / 1000 / 30) + offsetSteps;
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const hmac = createHmac('sha1', base32Decode(secret)).update(message).digest();
  const offset = hmac[hmac.length - 1] & 0xf;
  const value = hmac.readUInt32BE(offset) & 0x7fffffff;
  return (value % 1_000_000).toString().padStart(6, '0');
}

async function inviteLink(email: string): Promise<string> {
  const pattern = new RegExp(`development mail, link for ${email}: (\\S+/invite/\\S+)`);
  for (let attempt = 0; attempt < 20; attempt++) {
    const log = execSync(LOG_COMMAND, { cwd: '..', encoding: 'utf8' });
    const match = log.match(pattern);
    if (match) return new URL(match[1]).pathname;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('invitation link not found in the app log');
}

test('invite, accept, enable two-factor login and sign in with a code', async ({ page, browser }) => {
  const email = `invitee-${Date.now()}@example.com`;

  await signIn(page);
  await page.goto('/settings');
  const form = page.locator('form[action="/settings/users"]');
  await form.getByLabel('Email').fill(email);
  await form.getByLabel('Role').selectOption('bookkeeper');
  await form.getByRole('button', { name: 'Send invitation' }).click();
  await expect(page.getByText(`If ${email} can be invited`)).toBeVisible();
  await expect(page.getByRole('row', { name: new RegExp(`${email}.*invited`) })).toBeVisible();

  const path = await inviteLink(email);
  const invitee = await browser.newContext();
  const guest = await invitee.newPage();
  await guest.goto(path);
  await guest.getByLabel('Choose a password').fill(PASSWORD);
  await guest.getByLabel('Repeat password').fill(PASSWORD);
  await guest.getByRole('button', { name: 'Accept invitation' }).click();
  await expect(guest).toHaveURL(/\/login$/);
  await expect(guest.getByText('Invitation accepted')).toBeVisible();

  await guest.goto(path);
  await expect(guest.getByText('This link is not valid')).toBeVisible();

  await guest.goto('/login');
  await guest.getByLabel('Email').fill(email);
  await guest.getByLabel('Password').fill(PASSWORD);
  await guest.getByRole('button', { name: 'Sign in' }).click();
  await expect(guest).toHaveURL(/\/$/);

  await guest.goto('/account/security');
  await guest.getByRole('button', { name: 'Set up' }).click();
  await expect(guest.locator('img[alt^="QR code"]')).toBeVisible();
  const secret = (await guest.locator('#totp-secret').textContent())!.trim();
  await guest.getByLabel('Code from the app').fill(totp(secret));
  await guest.getByRole('button', { name: 'Turn on two-factor login' }).click();
  await expect(guest.locator('#recovery-codes li')).toHaveCount(10);

  await guest.goto('/account');
  await guest.getByRole('button', { name: 'Sign out' }).first().click();
  await expect(guest).toHaveURL(/\/login$/);
  await guest.getByLabel('Email').fill(email);
  await guest.getByLabel('Password').fill(PASSWORD);
  await guest.getByRole('button', { name: 'Sign in' }).click();
  await expect(guest).toHaveURL(/\/login\/totp$/);
  // The enrolment code is spent; the next 30 second step is inside the accepted drift.
  await guest.getByLabel('Code').fill(totp(secret, 1));
  await guest.getByRole('button', { name: 'Verify' }).click();
  await expect(guest).toHaveURL(/\/$/);
  await expect(guest.locator('h1.page-title')).not.toBeEmpty();
  await invitee.close();
});
