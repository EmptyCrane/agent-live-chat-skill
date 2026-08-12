import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.LIVE_CHAT_PLAYWRIGHT_PATH || 'playwright');

async function main() {
  const [url, outputDir, docsDir] = process.argv.slice(2);
  if (!url || !outputDir) throw new Error('usage: visual_qa.js <url> <output-dir>');
  fs.mkdirSync(outputDir, { recursive: true });
  if (docsDir) fs.mkdirSync(docsDir, { recursive: true });

  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.LIVE_CHAT_BROWSER_PATH || process.env.CHROME_PATH || undefined,
  });
  const cases = [
    { name: '360-light', viewport: { width: 360, height: 760 }, colorScheme: 'light' },
    { name: '400-members', viewport: { width: 400, height: 760 }, colorScheme: 'light', openMembers: true },
    { name: '768-light', viewport: { width: 768, height: 900 }, colorScheme: 'light' },
    { name: '1200-light', viewport: { width: 1200, height: 800 }, colorScheme: 'light' },
    { name: '1200-dark', viewport: { width: 1200, height: 800 }, colorScheme: 'dark' },
    { name: '400-reduced', viewport: { width: 400, height: 760 }, colorScheme: 'light', reducedMotion: 'reduce' },
  ];
  const results = [];

  async function post(endpoint, data) {
    const response = await fetch(url + endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) throw new Error(endpoint + ' returned ' + response.status);
    return response.json();
  }

  function makeSession(status = 'running', language = 'zh-CN') {
    const terminal = ['waiting_user', 'completed', 'stopped', 'partial_failure'].includes(status);
    const english = language === 'en';
    return {
      status,
      background: english ? 'The live view is ready for an international release review' : '新版直播页面已完成名册和主题升级',
      objective: english ? 'Confirm the goal-driven review is clear and converges reliably' : '确认目标驱动式群聊能清晰展示进度并可靠收敛',
      deliverable: english ? 'An actionable UX and reliability recommendation' : '一份可执行的体验与可靠性改进结论',
      criteria: english
        ? ['Every role contributes', 'Key risks are explicit', 'Next steps are actionable']
        : ['角色观点完整', '关键风险明确', '下一步可以执行'],
      model_policy: {
        default: 'balanced-model',
        reasoning_effort: 'medium',
        fallback: 'ask',
      },
      roles: [
        {
          name: english ? 'Architect' : '成员 1', role: english ? 'Product architecture' : '产品体验',
          focus: english ? 'Information hierarchy and continuity' : '信息层级与观看连续性',
          tone: english ? 'Clear and constructive' : '清晰、友好',
          style: english ? 'Lead with the conclusion' : '先结论再举例',
          instructions: [english ? 'Represent the viewer' : '站在观看者角度'],
          model: { requested: 'default', effective: 'balanced-model', reasoning_effort: 'default', fallback_reason: '' },
        },
        {
          name: english ? 'Critic' : '成员 2', role: english ? 'Quality critic' : '前端工程',
          focus: english ? 'Responsive behavior and performance' : '响应式布局与性能',
          tone: english ? 'Direct but respectful' : '直接但克制',
          style: english ? 'Order findings by risk' : '按风险排序',
          instructions: [english ? 'State technical boundaries' : '明确技术边界'],
          model: {
            requested: 'quality-model', effective: 'balanced-model', reasoning_effort: 'high',
            fallback_reason: english ? 'Requested model unavailable; approved fallback' : '请求模型不可用，用户同意替代',
          },
        },
        {
          name: english ? 'Operator' : '成员 3', role: english ? 'Delivery operator' : '质量验证',
          focus: english ? 'Recovery paths and edge cases' : '恢复路径与边界条件',
          tone: '', style: '', instructions: [],
          model: { requested: 'default', effective: 'host-managed', reasoning_effort: 'default', fallback_reason: '' },
        },
      ],
      round: {
        current: terminal ? 3 : (status === 'paused' ? 2 : 1),
        max: 3,
        phase: terminal ? 'synthesis' : (status === 'paused' ? 'challenge' : 'independent'),
        completed_participants: status === 'paused' ? [english ? 'Architect' : '成员 1'] : [],
      },
      stop_reason: status === 'running' ? '' : (english ? 'Visual acceptance state' : '用于会话状态视觉验收'),
    };
  }

  async function seedFixture(language) {
    const english = language === 'en';
    const roster = english
      ? ['Architect', 'Critic', 'Operator', 'Researcher', 'Designer', 'Engineer', 'Analyst', 'Reviewer', 'Planner', 'Editor', 'Facilitator', 'Observer']
      : Array.from({ length: 12 }, (_, index) => '成员 ' + (index + 1));
    await post('/api/reset', {
      scene: english
        ? { title: 'Multi-agent design review', subtitle: '12 participants · 6 have spoken' }
        : { title: '多智能体设计评审', subtitle: '12 位参与者 · 6 位已发言' },
    });
    await post('/api/participants', { participants: roster });
    await post('/api/session', { session: makeSession('running', language) });
    for (let index = 0; index < 6; index += 1) {
      await post('/api/msg', {
        sender: roster[index],
        text: english
          ? ['The hierarchy is easy to scan and keeps the objective visible.', 'The primary risk is ambiguous host capability detection.', 'Use explicit host selection when detection is not conclusive.', 'The participant roster remains stable before anyone speaks.', 'Both locales should be captured from the real interface.', 'The release archive is isolated from runtime state.'][index]
          : '这是第 ' + (index + 1) + ' 位成员的验收消息。',
      });
    }
    for (let index = 0; index < 4; index += 1) {
      await post('/api/typing', { sender: roster[index], active: true });
    }
    return roster;
  }

  async function probeState(expectedMembers, expectedTyping, expectedWaiting = null) {
    const context = await browser.newContext({ viewport: { width: 360, height: 760 }, colorScheme: 'light' });
    try {
      const page = await context.newPage();
      await page.goto(url + '?lang=zh-CN', { waitUntil: 'networkidle' });
      await page.waitForFunction(
        expected => Number(document.getElementById('member-total').textContent) === expected,
        expectedMembers,
      );
      const value = await page.evaluate(() => ({
        members: Number(document.getElementById('member-total').textContent),
        typing: [...document.querySelectorAll('#desktop-member-list .member-role')]
          .filter(item => item.textContent.includes('正在输入')).length,
        waiting: [...document.querySelectorAll('#desktop-member-list .member-role')]
          .filter(item => item.textContent.includes('等待发言')).length,
        typingVisible: !document.getElementById('typing').hidden,
      }));
      if (value.members !== expectedMembers || value.typing !== expectedTyping) {
        throw new Error('state probe mismatch: ' + JSON.stringify(value));
      }
      if ((expectedTyping > 0) !== value.typingVisible) throw new Error('typing visibility mismatch');
      if (expectedWaiting !== null && value.waiting !== expectedWaiting) {
        throw new Error('waiting member mismatch: ' + JSON.stringify(value));
      }
      return value;
    } finally {
      await context.close();
    }
  }

  try {
    const roster = await seedFixture('zh-CN');

    for (const item of cases) {
      const context = await browser.newContext({
        viewport: item.viewport,
        colorScheme: item.colorScheme,
        reducedMotion: item.reducedMotion || 'no-preference',
      });
      const page = await context.newPage();
      await page.goto(url + '?lang=zh-CN', { waitUntil: 'networkidle' });
      await page.waitForFunction(() => document.querySelectorAll('.message-row').length >= 1);
      if (item.openMembers) {
        await page.locator('#member-trigger').click();
        await page.waitForTimeout(250);
      }
      const metrics = await page.evaluate(() => ({
        width: window.innerWidth,
        bodyScrollWidth: document.body.scrollWidth,
        messages: document.querySelectorAll('.message-row, .system-message').length,
        members: document.querySelectorAll('#desktop-member-list .member-item').length,
        sheetOpen: !document.getElementById('sheet-backdrop').hidden,
        railVisible: getComputedStyle(document.querySelector('.participant-rail')).display !== 'none',
        typingVisible: !document.getElementById('typing').hidden,
        theme: document.documentElement.getAttribute('data-theme') || 'auto',
        sessionVisible: !document.getElementById('session-bar').hidden,
        sessionStatus: document.getElementById('session-bar').dataset.status,
        sessionObjective: document.getElementById('session-objective').textContent,
        roleModel: document.querySelector('#rail-session .session-role-meta')?.textContent || '',
        roleTone: document.querySelector('#rail-session .session-role-setting')?.textContent || '',
        allDesktopMembersVisible: (() => {
          const list = document.getElementById('desktop-member-list');
          const members = [...list.querySelectorAll('.member-item')];
          if (!members.length || getComputedStyle(list).display === 'none') return null;
          const bounds = list.getBoundingClientRect();
          const last = members[members.length - 1].getBoundingClientRect();
          return last.bottom <= bounds.bottom + 1;
        })(),
      }));
      if (metrics.bodyScrollWidth > metrics.width) throw new Error(item.name + ' has horizontal overflow');
      if (item.viewport.width >= 820 && metrics.members !== 12) throw new Error(item.name + ' roster mismatch');
      if (item.viewport.width >= 1200 && metrics.allDesktopMembersVisible !== true) {
        throw new Error(item.name + ' does not show all 12 participants without scrolling');
      }
      if (!metrics.sessionVisible || metrics.sessionStatus !== 'running') throw new Error(item.name + ' session is hidden');
      if (!metrics.sessionObjective.includes('目标驱动式群聊')) throw new Error(item.name + ' objective mismatch');
      if (!metrics.roleModel.includes('balanced-model') || !metrics.roleTone.includes('清晰、友好')) {
        throw new Error(item.name + ' role runtime settings are missing');
      }
      const screenshot = path.join(outputDir, 'live-chat-' + item.name + '.png');
      await page.screenshot({ path: screenshot, fullPage: false });
      results.push({ name: item.name, screenshot, ...metrics });
      await context.close();
    }

    const stateChecks = [];
    await post('/api/reset', { scene: { title: '状态验收', subtitle: '0 / 1 / 6 / 12 成员' } });
    await post('/api/participants', { participants: [] });
    stateChecks.push({ case: '0-members-0-typing', ...await probeState(0, 0) });

    await post('/api/msg', { sender: '成员 1', text: '单成员状态' });
    await post('/api/typing', { sender: '成员 1', active: true });
    stateChecks.push({ case: '1-member-1-typing', ...await probeState(1, 1) });

    await post('/api/reset', { scene: { title: '状态验收', subtitle: '6 名成员' } });
    for (let index = 1; index <= 6; index += 1) {
      await post('/api/msg', { sender: '成员 ' + index, text: '第 ' + index + ' 位成员' });
    }
    await post('/api/typing', { sender: '成员 1', active: true });
    await post('/api/typing', { sender: '成员 2', active: true });
    stateChecks.push({ case: '6-members-2-typing', ...await probeState(6, 2) });

    await post('/api/participants', { participants: roster });
    await post('/api/typing', { sender: '成员 3', active: true });
    await post('/api/typing', { sender: '成员 4', active: true });
    stateChecks.push({ case: '12-members-4-typing-6-waiting', ...await probeState(12, 4, 6) });

    const sessionChecks = [];
    for (const status of ['paused', 'waiting_user', 'completed', 'stopped', 'partial_failure']) {
      await post('/api/session', { session: makeSession(status) });
      for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({
          viewport: { width: 1200, height: 800 },
          colorScheme,
        });
        const page = await context.newPage();
        await page.goto(url + '?lang=zh-CN', { waitUntil: 'networkidle' });
        await page.waitForFunction(expected => document.getElementById('session-bar').dataset.status === expected, status);
        const value = await page.evaluate(() => ({
          status: document.getElementById('session-bar').dataset.status,
          progress: document.getElementById('session-progress').textContent,
          badge: document.querySelector('#rail-session .session-badge').textContent,
          reason: document.querySelector('#rail-session .session-reason').textContent,
          bodyScrollWidth: document.body.scrollWidth,
          width: window.innerWidth,
        }));
        if (value.bodyScrollWidth > value.width || !value.reason) {
          throw new Error('session status layout mismatch: ' + JSON.stringify(value));
        }
        const screenshot = path.join(outputDir, `live-chat-session-${status}-${colorScheme}.png`);
        await page.screenshot({ path: screenshot, fullPage: false });
        sessionChecks.push({ status, colorScheme, screenshot, ...value });
        await context.close();
      }
    }

    const themeContext = await browser.newContext({ viewport: { width: 400, height: 760 }, colorScheme: 'dark' });
    const themePage = await themeContext.newPage();
    await themePage.goto(url + '?lang=en', { waitUntil: 'networkidle' });
    await themePage.locator('#theme-toggle').click();
    await themePage.locator('#theme-toggle').click();
    await themePage.reload({ waitUntil: 'networkidle' });
    const rememberedTheme = await themePage.evaluate(() => ({
      attribute: document.documentElement.getAttribute('data-theme'),
      stored: localStorage.getItem('live-chat-theme'),
      label: document.getElementById('theme-toggle').getAttribute('aria-label'),
    }));
    await themeContext.close();
    if (rememberedTheme.attribute !== 'dark' || rememberedTheme.stored !== 'dark') {
      throw new Error('manual theme was not remembered: ' + JSON.stringify(rememberedTheme));
    }

    const blockedStorage = await browser.newContext({ viewport: { width: 400, height: 760 } });
    await blockedStorage.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('blocked'); } });
    });
    const blockedPage = await blockedStorage.newPage();
    await blockedPage.goto(url + '?lang=zh-CN', { waitUntil: 'networkidle' });
    const storageFallback = await blockedPage.evaluate(() => ({
      attribute: document.documentElement.getAttribute('data-theme'),
      label: document.getElementById('theme-toggle').getAttribute('aria-label'),
    }));
    await blockedStorage.close();
    if (storageFallback.attribute !== null || !storageFallback.label.includes('自动')) {
      throw new Error('blocked storage did not fall back to auto');
    }
    async function captureDocumentation(language, filename) {
      await seedFixture(language);
      const context = await browser.newContext({
        viewport: { width: 1200, height: 800 },
        colorScheme: 'light',
        locale: language === 'en' ? 'en-US' : 'zh-CN',
      });
      try {
        const page = await context.newPage();
        await page.goto(url + '?lang=' + encodeURIComponent(language), { waitUntil: 'networkidle' });
        await page.waitForFunction(() => document.querySelectorAll('.message-row').length >= 6);
        const value = await page.evaluate(() => ({
          language: document.documentElement.lang,
          membersTitle: document.getElementById('sheet-title').textContent,
          progress: document.getElementById('session-progress').textContent,
          count: document.getElementById('rail-count').textContent,
          roleMeta: document.querySelector('#rail-session .session-role-meta')?.textContent || '',
          horizontalOverflow: document.body.scrollWidth > window.innerWidth,
        }));
        const english = language === 'en';
        if (value.language !== language || value.horizontalOverflow) throw new Error('documentation locale mismatch: ' + JSON.stringify(value));
        if (english && (!value.membersTitle.includes('Participants') || !value.progress.includes('Round 1/3') || !value.roleMeta.includes('Model:'))) {
          throw new Error('English UI is incomplete: ' + JSON.stringify(value));
        }
        if (!english && (!value.membersTitle.includes('群聊成员') || !value.progress.includes('第 1/3 轮') || !value.roleMeta.includes('模型：'))) {
          throw new Error('Chinese UI is incomplete: ' + JSON.stringify(value));
        }
        const screenshot = path.join(docsDir || outputDir, filename);
        await page.screenshot({ path: screenshot, fullPage: false });
        return { screenshot, ...value };
      } finally {
        await context.close();
      }
    }

    const documentation = [
      await captureDocumentation('en', 'live-chat-en.png'),
      await captureDocumentation('zh-CN', 'live-chat-zh-CN.png'),
    ];
    results.push({ stateChecks, sessionChecks, rememberedTheme, storageFallback, documentation });
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(results, null, 2));
}

main().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
