import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

async function main() {
  const [url, expectedValue] = process.argv.slice(2);
  if (!url || !expectedValue) {
    throw new Error('usage: forward_probe.js <url> <sender1,sender2,...>');
  }
  const expected = expectedValue.split(',');
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || undefined,
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => {
      const bar = document.getElementById('session-bar');
      return bar && bar.dataset.status === 'completed';
    });
    const actual = await page.locator('.sender-name').allTextContents();
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      throw new Error(`sender order mismatch: ${JSON.stringify(actual)}`);
    }
    const result = await page.evaluate(async () => {
      const state = await fetch('/api/state').then((response) => response.json());
      return {
        status: document.getElementById('session-bar').dataset.status,
        objective: document.getElementById('session-objective').textContent,
        completedParticipants: state.session.round.completed_participants,
        typing: state.typing,
      };
    });
    process.stdout.write(JSON.stringify({ senders: actual, ...result }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
