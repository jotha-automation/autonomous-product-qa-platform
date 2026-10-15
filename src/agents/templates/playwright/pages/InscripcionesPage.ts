import { Page } from '@playwright/test';

export class InscripcionesPage {
  constructor(protected page: Page) {}

  async navigate(): Promise<void> {
    await this.page.goto('/inscripciones/crear');
    await this.page.waitForLoadState('domcontentloaded');
  }

  async gotoCrear(): Promise<void> {
    await this.page.goto('/inscripciones/crear');
    await this.page.getByTestId('crear-inscripcion-page').waitFor({ state: 'visible', timeout: 15000 });
  }

  async validarProceso(numProceso: string): Promise<void> {
    await this.page.getByTestId(`validar-${numProceso}`).click();
  }

  async actualizarProceso(numProceso: string): Promise<void> {
    await this.page.getByTestId(`actualizar-${numProceso}`).click();
  }
}
