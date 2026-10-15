import { Page } from '@playwright/test';
import { getAssetField } from '../fixtures/dynamic-auth';

export class LoginPage {
  constructor(protected page: Page) {}

  async login(): Promise<void> {
    const cliente = getAssetField('login_user', 'cliente');
    const documento = getAssetField('login_user', 'documento');
    const clave = getAssetField('login_user', 'clave');
    const token = getAssetField('login_user', 'token');
    const readyTimeout = 60000;

    await this.page.goto('/login', { waitUntil: 'domcontentloaded', timeout: readyTimeout });
    await this.page.getByTestId('login-page').waitFor({ state: 'visible', timeout: readyTimeout });
    await this.page
      .waitForFunction(() => Boolean(window.__aptpDemo?.login), { timeout: readyTimeout })
      .catch(() => undefined);

    await this.page.evaluate(() => {
      window.__aptpDemo?.resetDemoData?.();
    });

    const hookReady = await this.page.evaluate(() => Boolean(window.__aptpDemo?.login));
    if (hookReady) {
      const error = await this.page.evaluate(
        ({ cliente, documento, clave, token }) => {
          const hook = window.__aptpDemo?.login;
          if (!hook) return 'APT login hook missing';
          const res = hook(cliente, 'Cédula de Ciudadanía', documento, clave, token || undefined);
          return res.ok ? null : res.error || 'Login failed';
        },
        { cliente, documento, clave, token },
      );
      if (error) {
        throw new Error(error);
      }
    } else {
      await this.page.getByTestId('login-cliente').fill(cliente);
      await this.page.getByTestId('login-documento').fill(documento);
      await this.page.getByTestId('login-clave').fill(clave);
      await this.page.getByTestId('login-token').fill(token);
      await this.page.getByTestId('login-submit').click();
      await this.page.waitForURL(/\/(inscripciones|consultas|pagos|dashboard)/, { timeout: readyTimeout });
    }

    if (!/\/(inscripciones|consultas|pagos|dashboard)/.test(this.page.url())) {
      await this.page.goto('/dashboard', { waitUntil: 'domcontentloaded', timeout: readyTimeout });
    }
    await this.page
      .locator('[data-testid="app-shell"], [data-testid="reset-demo-data"]')
      .first()
      .waitFor({ state: 'attached', timeout: readyTimeout });
  }
}
