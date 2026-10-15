/**
 * Dynamic auth fixture — reads secrets from APTP_SECRET_* env vars at runtime.
 * Never hardcode credentials in generated specs.
 *
 * Bank-portal demo fallbacks apply only when APTP_ALLOW_DEMO_SECRETS is enabled
 * (default true for local/golden runs). Production pipelines inject APTP_SECRET_* via .env.
 */
const BANK_DEMO_SECRETS: Record<string, string> = {
  'login_user.cliente': '312',
  'login_user.tipo_id': 'Cédula de Ciudadanía',
  'login_user.documento': '20242587',
  'login_user.clave': '123456',
  'login_user.token': '63866019',
  'no_token_client.cliente': '201319',
  'no_token_client.tipo_id': 'Cédula de Ciudadanía',
  'no_token_client.documento': '1233896904',
  'no_token_client.clave': '2023',
  'no_token_client.token': '',
};

function demoSecretsAllowed(): boolean {
  const flag = (process.env.APTP_ALLOW_DEMO_SECRETS ?? 'true').trim().toLowerCase();
  return flag === '1' || flag === 'true' || flag === 'yes' || flag === 'on';
}

export function getRuntimeSecret(key: string): string {
  const envKey = `APTP_SECRET_${key.replace(/[^a-zA-Z0-9_]/g, '_').toUpperCase()}`;
  const value = process.env[envKey] || process.env[key] || '';
  if (value) {
    return value;
  }
  if (demoSecretsAllowed() && BANK_DEMO_SECRETS[key]) {
    return BANK_DEMO_SECRETS[key];
  }
  throw new Error(`Missing runtime secret for key: ${key} (expected ${envKey})`);
}

export function getAssetField(assetKey: string, field: string): string {
  return getRuntimeSecret(`${assetKey}.${field}`);
}

export async function loginWithAsset(page: import('@playwright/test').Page, assetKey: string = 'login_user') {
  const user = getAssetField(assetKey, 'usuario') || getAssetField(assetKey, 'user') || getRuntimeSecret(assetKey);
  const pass =
    getAssetField(assetKey, 'clave') ||
    getAssetField(assetKey, 'password') ||
    getRuntimeSecret(`${assetKey}.password`);
  // Generic login — selectors resolved per project via architect plan
  const userInput = page.getByRole('textbox', { name: /user|usuario|email/i }).first();
  const passInput = page.getByRole('textbox', { name: /pass|clave|password/i }).first();
  if (await userInput.isVisible().catch(() => false)) {
    await userInput.fill(user);
  }
  if (await passInput.isVisible().catch(() => false)) {
    await passInput.fill(pass);
  }
  await page.getByRole('button', { name: /login|ingresar|entrar/i }).first().click();
}
