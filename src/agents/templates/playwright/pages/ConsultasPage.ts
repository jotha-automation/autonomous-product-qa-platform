import { Page } from '@playwright/test';

export class ConsultasPage {
  constructor(protected page: Page) {}

  async navigate(): Promise<void> {
    await this.page.goto('/consultas');
    await this.page.waitForLoadState('domcontentloaded');
  }

  async gotoConsultas(): Promise<void> {
    await this.page.goto('/consultas');
    await this.page.getByTestId('consultas-page').waitFor({ state: 'visible', timeout: 15000 });
  }
}
