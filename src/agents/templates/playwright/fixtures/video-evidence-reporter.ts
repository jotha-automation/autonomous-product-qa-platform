import * as fs from 'fs';
import * as path from 'path';
import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from '@playwright/test/reporter';

function safeSegment(value: string): string {
  return (value || 'test')
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 96) || 'test';
}

class VideoEvidenceReporter implements Reporter {
  onTestEnd(test: TestCase, result: TestResult) {
    const videoAttachment = result.attachments.find(
      (attachment) => attachment.name === 'video' && attachment.path && fs.existsSync(attachment.path),
    );
    if (!videoAttachment?.path) return;

    const dir = path.join('evidence', safeSegment(test.title));
    fs.mkdirSync(dir, { recursive: true });
    const dest = path.join(dir, 'run.webm');
    fs.copyFileSync(videoAttachment.path, dest);

    result.annotations.push({
      type: 'evidence-video',
      description: JSON.stringify({
        label: 'Full test run video',
        path: dest,
        capturedAt: new Date().toISOString(),
      }),
    });
  }

  onBegin(_config: FullConfig, _suite: Suite) {}
  onEnd(_result: FullResult) {}
}

export default VideoEvidenceReporter;
