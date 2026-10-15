import * as fs from 'fs';
import * as path from 'path';
import { Page, TestInfo } from '@playwright/test';

function safeSegment(value: string): string {
  return (value || 'step')
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 96) || 'step';
}

export async function captureStepEvidence(
  page: Page,
  testInfo: TestInfo,
  stepId: string,
  label?: string,
): Promise<string> {
  const testKey = safeSegment(testInfo.title);
  const stepKey = safeSegment(stepId);
  const displayLabel = label || stepKey;
  const dir = path.join('evidence', testKey);
  fs.mkdirSync(dir, { recursive: true });
  const filePath = path.join(dir, `${stepKey}.png`);
  await page.screenshot({ path: filePath, fullPage: true });
  testInfo.annotations.push({
    type: 'evidence',
    description: JSON.stringify({
      stepId: stepKey,
      label: displayLabel,
      path: filePath,
      capturedAt: new Date().toISOString(),
    }),
  });
  return filePath;
}
