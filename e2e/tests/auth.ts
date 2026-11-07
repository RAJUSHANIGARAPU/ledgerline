import { expect, type Page } from '@playwright/test';

export const OWNER = {
  email: process.env.LEDGERLINE_EMAIL ?? 'demo@ledgerline.example',
  password: process.env.LEDGERLINE_PASSWORD ?? 'demo-password-123',
};

export async function signIn(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Email').fill(OWNER.email);
  await page.getByLabel('Password').fill(OWNER.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL('/');
}
