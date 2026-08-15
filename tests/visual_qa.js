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
    { name: '390-members', viewport: { width: 390, height: 760 }, colorScheme: 'light', openMembers: true },
    { name: '768-light', viewport: { width: 768, height: 900 }, colorScheme: 'light' },
    { name: '1200-light', viewport: { width: 1200, height: 800 }, colorScheme: 'light' },
    { name: '1200-dark', viewport: { width: 1200, height: 800 }, colorScheme: 'dark' },
    { name: '390-reduced', viewport: { width: 390, height: 760 }, colorScheme: 'light', reducedMotion: 'reduce' },
  ];
  const results = [];

  async function post(endpoint, data) {
    const response = await fetch(url + endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(endpoint + ' returned ' + response.status + ': ' + JSON.stringify(value));
    return value;
  }

  async function waitForTemplates(page) {
    await page.waitForFunction(
      () => document.documentElement.dataset.templatesReady === 'ready',
    );
  }

  function makeSession(status = 'running', language = 'zh-CN') {
    const terminal = ['waiting_user', 'completed', 'stopped', 'partial_failure'].includes(status);
    const english = language === 'en';
    const criteria = english
      ? ['Every role contributes', 'Key risks are explicit', 'Next steps are actionable']
      : ['角色观点完整', '关键风险明确', '下一步可以执行'];
    const names = english
      ? ['Architect', 'Critic', 'Operator', 'Researcher', 'Designer', 'Engineer', 'Analyst', 'Reviewer', 'Planner', 'Editor', 'Facilitator', 'Observer']
      : Array.from({ length: 12 }, (_, index) => '成员 ' + (index + 1));
    const session = {
      status,
      background: english ? 'The live view is ready for an international release review' : '新版直播页面已完成名册和主题升级',
      objective: english ? 'Confirm the goal-driven review is clear and converges reliably' : '确认目标驱动式群聊能清晰展示进度并可靠收敛',
      deliverable: english ? 'An actionable UX and reliability recommendation' : '一份可执行的体验与可靠性改进结论',
      criteria,
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
      workflow: {
        strategy: 'parallel_panel',
        approval: status === 'waiting_user' ? 'required' : 'approved',
        limits: { max_rounds: 3, max_participants: 12, max_retries: 1, wall_time_seconds: 900 },
        template: { id: 'worldbuilding_council', version: 1 },
        dispatch: { max_concurrent: 4, source: 'user_configured', mode: 'waves' },
      },
      pending_decision: status === 'waiting_user' ? {
        id: 'a'.repeat(32),
        kind: 'checkpoint',
        prompt: english ? 'Continue with one focused round?' : '是否继续一轮聚焦评审？',
        options: [
          { id: 'continue', label: english ? 'Continue' : '继续', description: '' },
          { id: 'stop', label: english ? 'Stop' : '停止', description: '' },
        ],
        created_at: '2026-08-15T00:00:00+00:00',
      } : null,
      run: {
        id: 'visual-run',
        started_at: '2026-08-15T00:00:00+00:00',
        updated_at: '2026-08-15T00:00:02+00:00',
        participants: names.map((name, index) => ({
          name,
          status: status === 'partial_failure' && index === 1 ? 'failed' : (terminal ? 'completed' : 'pending'),
          attempt: 1,
          started_at: '',
          ended_at: '',
          duration_ms: null,
          error_code: status === 'partial_failure' && index === 1 ? 'host_failure' : '',
        })),
        round_summaries: [],
      },
      result: status === 'completed' ? {
        summary: english ? 'All review criteria are met.' : '全部评审条件已满足。',
        criteria: criteria.map((text, index) => ({ text, status: 'met', evidence: [`message:${index + 1}`] })),
        disagreements: [],
        next_actions: [english ? 'Proceed to implementation.' : '进入实现阶段。'],
      } : null,
      stop_reason: status === 'running' ? '' : (english ? 'Visual acceptance state' : '用于会话状态视觉验收'),
    };
    for (let index = session.roles.length; index < names.length; index += 1) {
      session.roles.push({
        name: names[index],
        role: english ? 'Worldbuilding specialist' : '世界构建专家',
        focus: english ? `Distinct worldbuilding responsibility ${index + 1}` : `独立世界构建职责 ${index + 1}`,
        tone: '', style: '', instructions: [],
        model: { requested: 'default', effective: 'host-managed', reasoning_effort: 'default', fallback_reason: '' },
      });
    }
    return session;
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
      await page.goto(url + '?lang=zh-CN', { waitUntil: 'domcontentloaded' });
      await waitForTemplates(page);
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

  async function probeMobileSheetScroll({ width, language, colorScheme, input }) {
    const context = await browser.newContext({
      viewport: { width, height: 760 },
      colorScheme,
      isMobile: input === 'touch',
      hasTouch: input === 'touch',
      locale: language === 'en' ? 'en-US' : 'zh-CN',
    });
    try {
      const page = await context.newPage();
      const nonGet = [];
      page.on('request', request => {
        if (request.method() !== 'GET') nonGet.push(request.method() + ' ' + request.url());
      });
      await page.goto(url + '?lang=' + encodeURIComponent(language), { waitUntil: 'domcontentloaded' });
      await waitForTemplates(page);
      await page.waitForFunction(() => document.querySelectorAll('#sheet-member-list .member-item').length === 12);
      await page.locator('#member-trigger').click();
      await page.waitForFunction(() => {
        const backdrop = document.getElementById('sheet-backdrop');
        const scroll = document.getElementById('sheet-scroll');
        return !backdrop.hidden && scroll.scrollHeight > scroll.clientHeight;
      });

      const before = await page.evaluate(() => {
        const scroll = document.getElementById('sheet-scroll');
        return {
          scrollTop: scroll.scrollTop,
          scrollHeight: scroll.scrollHeight,
          clientHeight: scroll.clientHeight,
          chatScrollTop: document.getElementById('chat-scroll').scrollTop,
        };
      });

      async function touchSwipe() {
        const box = await page.locator('#sheet-scroll').boundingBox();
        if (!box || box.height < 80) throw new Error('mobile sheet scroll region is not visible');
        const session = await context.newCDPSession(page);
        const x = box.x + box.width / 2;
        const startY = box.y + Math.min(box.height - 18, 330);
        const endY = Math.max(box.y + 18, startY - 220);
        await session.send('Input.dispatchTouchEvent', {
          type: 'touchStart', touchPoints: [{ x, y: startY }],
        });
        for (const fraction of [0.25, 0.5, 0.75, 1]) {
          await session.send('Input.dispatchTouchEvent', {
            type: 'touchMove',
            touchPoints: [{ x, y: startY + (endY - startY) * fraction }],
          });
        }
        await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await session.detach();
      }

      async function advance() {
        if (input === 'touch') {
          await touchSwipe();
        } else {
          await page.locator('#sheet-scroll').hover();
          await page.mouse.wheel(0, 420);
        }
      }

      await advance();
      await page.waitForFunction(() => document.getElementById('sheet-scroll').scrollTop > 0);
      for (let attempt = 0; attempt < 12; attempt += 1) {
        const lastVisible = await page.evaluate(() => {
          const scroll = document.getElementById('sheet-scroll');
          const last = document.querySelector('#sheet-member-list .member-item:last-child');
          return last.getBoundingClientRect().bottom <= scroll.getBoundingClientRect().bottom + 1;
        });
        if (lastVisible) break;
        await advance();
      }

      const after = await page.evaluate(() => {
        const scroll = document.getElementById('sheet-scroll');
        const sheet = document.getElementById('member-sheet').getBoundingClientRect();
        const header = document.querySelector('.sheet-header').getBoundingClientRect();
        const close = document.getElementById('sheet-close').getBoundingClientRect();
        const last = document.querySelector('#sheet-member-list .member-item:last-child').getBoundingClientRect();
        return {
          scrollTop: scroll.scrollTop,
          lastVisible: last.bottom <= scroll.getBoundingClientRect().bottom + 1,
          headerVisible: header.top >= sheet.top - 1 && header.bottom <= sheet.bottom + 1,
          closeVisible: close.top >= sheet.top - 1 && close.bottom <= sheet.bottom + 1,
          chatScrollTop: document.getElementById('chat-scroll').scrollTop,
          horizontalOverflow: document.body.scrollWidth > window.innerWidth,
          safeAreaPadding: parseFloat(getComputedStyle(document.getElementById('sheet-member-list')).paddingBottom),
          touchAction: getComputedStyle(scroll).touchAction,
        };
      });
      if (after.scrollTop <= before.scrollTop || !after.lastVisible || !after.headerVisible || !after.closeVisible) {
        throw new Error('mobile member sheet did not scroll correctly: ' + JSON.stringify({ before, after }));
      }
      if (after.chatScrollTop !== before.chatScrollTop || after.horizontalOverflow || after.safeAreaPadding < 12) {
        throw new Error('mobile member sheet containment failed: ' + JSON.stringify({ before, after }));
      }
      if (input === 'touch' && after.touchAction !== 'pan-y') {
        throw new Error('mobile member sheet does not expose vertical touch panning');
      }
      if (nonGet.length) throw new Error('mobile member sheet issued a write request: ' + JSON.stringify(nonGet));
      return { width, language, colorScheme, input, before, after };
    } finally {
      await context.close();
    }
  }

  async function probeMobileRoster(expectedMembers, language) {
    const context = await browser.newContext({ viewport: { width: 360, height: 640 } });
    try {
      const page = await context.newPage();
      const nonGet = [];
      page.on('request', request => {
        if (request.method() !== 'GET') nonGet.push(request.method() + ' ' + request.url());
      });
      await page.goto(url + '?lang=' + encodeURIComponent(language), { waitUntil: 'domcontentloaded' });
      await waitForTemplates(page);
      await page.waitForFunction(
        expected => Number(document.getElementById('member-total').textContent) === expected,
        expectedMembers,
      );
      await page.locator('#member-trigger').click();
      const value = await page.evaluate(() => ({
        members: document.querySelectorAll('#sheet-member-list .member-item').length,
        sheetOpen: !document.getElementById('sheet-backdrop').hidden,
        horizontalOverflow: document.body.scrollWidth > window.innerWidth,
      }));
      if (value.members !== expectedMembers || !value.sheetOpen || value.horizontalOverflow || nonGet.length) {
        throw new Error('mobile roster state mismatch: ' + JSON.stringify({ value, nonGet }));
      }
      return { expectedMembers, language, ...value };
    } finally {
      await context.close();
    }
  }

  try {
    const roster = await seedFixture('zh-CN');
    const initialCatalog = await fetch(url + '/api/sessions').then(response => response.json());
    const liveSessionId = initialCatalog.active_session_id;
    const archivedCreated = await post('/api/sessions', {
      title: 'Archived visual history',
      subtitle: 'Read-only selector check',
    });
    const archivedSessionId = archivedCreated.session.session_id;
    await post('/api/participants', {
      participants: ['Architect', 'Critic', 'Operator', 'Researcher', 'Designer', 'Engineer', 'Analyst', 'Reviewer', 'Planner', 'Editor', 'Facilitator', 'Observer'],
    });
    await post('/api/session', { session: makeSession('completed', 'en') });
    await post('/api/msg', { sender: 'History agent', text: 'Archived message remains readable.' });
    await post('/api/sessions/select', { session_id: liveSessionId });
    await post('/api/sessions/archive', { session_id: archivedSessionId });

    const historyContext = await browser.newContext({ viewport: { width: 1200, height: 800 } });
    const historyPage = await historyContext.newPage();
    const pagePosts = [];
    historyPage.on('request', request => {
      if (request.method() !== 'GET') pagePosts.push(request.method() + ' ' + request.url());
    });
    await historyPage.goto(url + '?lang=en', { waitUntil: 'domcontentloaded' });
    await waitForTemplates(historyPage);
    await historyPage.waitForFunction(() => document.querySelectorAll('#rail-session-select option').length >= 2);
    await historyPage.locator('#rail-session-select').selectOption(archivedSessionId);
    await historyPage.waitForFunction(
      () => [...document.querySelectorAll('.message-text')].some(item => item.textContent.includes('Archived message')),
    );
    const historyCheck = await historyPage.evaluate(() => ({
      selected: document.getElementById('rail-session-select').value,
      urlSession: new URLSearchParams(window.location.search).get('session'),
      archivedLabel: document.getElementById('rail-session-select').selectedOptions[0].textContent,
    }));
    if (historyCheck.selected !== archivedSessionId || historyCheck.urlSession !== archivedSessionId) {
      throw new Error('history selector did not switch the read-only view');
    }
    if (!historyCheck.archivedLabel.includes('archived') || pagePosts.length) {
      throw new Error('history selector is not read-only: ' + JSON.stringify({ historyCheck, pagePosts }));
    }
    await historyPage.locator('#message-search').fill('not-present');
    if (await historyPage.locator('.message-row:not([hidden])').count()) {
      throw new Error('message search did not filter the history');
    }
    await historyPage.locator('#message-search').fill('Archived');
    await historyPage.locator('#message-participant').selectOption('History agent');
    if (await historyPage.locator('.message-row:not([hidden])').count() !== 1) {
      throw new Error('participant filter did not preserve the matching message');
    }
    await historyPage.locator('#rail-compare').click();
    await historyPage.waitForFunction(() => !document.getElementById('rail-comparison').hidden);
    historyCheck.comparison = await historyPage.locator('#rail-comparison').textContent();
    if (!historyCheck.comparison.includes('All review criteria are met.')
        || !historyCheck.comparison.includes('3/3 criteria met')
        || pagePosts.length) {
      throw new Error('session comparison is unavailable or not read-only');
    }
    await historyContext.close();

    const mobileHistoryContext = await browser.newContext({ viewport: { width: 390, height: 760 }, hasTouch: true });
    const mobileHistoryPage = await mobileHistoryContext.newPage();
    const mobileHistoryWrites = [];
    mobileHistoryPage.on('request', request => {
      if (request.method() !== 'GET') mobileHistoryWrites.push(request.method() + ' ' + request.url());
    });
    await mobileHistoryPage.goto(url + '?lang=en', { waitUntil: 'domcontentloaded' });
    await waitForTemplates(mobileHistoryPage);
    await mobileHistoryPage.locator('#member-trigger').click();
    await mobileHistoryPage.waitForFunction(() => document.querySelectorAll('#sheet-session-select option').length >= 2);
    await mobileHistoryPage.locator('#sheet-session-select').selectOption(archivedSessionId);
    await mobileHistoryPage.waitForFunction(
      expected => new URLSearchParams(window.location.search).get('session') === expected,
      archivedSessionId,
    );
    const mobileHistoryCheck = await mobileHistoryPage.evaluate(() => ({
      selected: document.getElementById('sheet-session-select').value,
      archivedLabel: document.getElementById('sheet-session-select').selectedOptions[0].textContent,
      scrollContained: document.getElementById('sheet-scroll').scrollHeight > document.getElementById('sheet-scroll').clientHeight,
      horizontalOverflow: document.body.scrollWidth > window.innerWidth,
    }));
    await mobileHistoryContext.close();
    if (mobileHistoryCheck.selected !== archivedSessionId
        || !mobileHistoryCheck.archivedLabel.includes('archived')
        || !mobileHistoryCheck.scrollContained
        || mobileHistoryCheck.horizontalOverflow
        || mobileHistoryWrites.length) {
      throw new Error('mobile history sheet mismatch: ' + JSON.stringify({ mobileHistoryCheck, mobileHistoryWrites }));
    }

    const mobileSheetChecks = [
      await probeMobileSheetScroll({ width: 360, language: 'en', colorScheme: 'dark', input: 'wheel' }),
      await probeMobileSheetScroll({ width: 360, language: 'zh-CN', colorScheme: 'light', input: 'touch' }),
      await probeMobileSheetScroll({ width: 390, language: 'zh-CN', colorScheme: 'light', input: 'wheel' }),
      await probeMobileSheetScroll({ width: 390, language: 'en', colorScheme: 'dark', input: 'touch' }),
    ];

    for (const item of cases) {
      const context = await browser.newContext({
        viewport: item.viewport,
        colorScheme: item.colorScheme,
        reducedMotion: item.reducedMotion || 'no-preference',
      });
      const page = await context.newPage();
      await page.goto(url + '?lang=zh-CN', { waitUntil: 'domcontentloaded' });
      await waitForTemplates(page);
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
        templateDetail: document.getElementById('rail-session').textContent,
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
      if (!metrics.templateDetail.includes('世界观共创 · v1')
          || !metrics.templateDetail.includes('12 个角色 · 4 个并发 · 3 个批次')) {
        throw new Error(item.name + ' template dispatch summary is missing');
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
    stateChecks.push({ case: 'mobile-0-members', ...await probeMobileRoster(0, 'zh-CN') });

    await post('/api/msg', { sender: '成员 1', text: '单成员状态' });
    await post('/api/typing', { sender: '成员 1', active: true });
    stateChecks.push({ case: '1-member-1-typing', ...await probeState(1, 1) });
    stateChecks.push({ case: 'mobile-1-member', ...await probeMobileRoster(1, 'en') });

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
        await page.goto(url + '?lang=zh-CN', { waitUntil: 'domcontentloaded' });
        await waitForTemplates(page);
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

    const themeContext = await browser.newContext({ viewport: { width: 390, height: 760 }, colorScheme: 'dark' });
    const themePage = await themeContext.newPage();
    await themePage.goto(url + '?lang=en', { waitUntil: 'domcontentloaded' });
    await waitForTemplates(themePage);
    await themePage.locator('#theme-toggle').click();
    await themePage.locator('#theme-toggle').click();
    await themePage.reload({ waitUntil: 'domcontentloaded' });
    const rememberedTheme = await themePage.evaluate(() => ({
      attribute: document.documentElement.getAttribute('data-theme'),
      stored: localStorage.getItem('live-chat-theme'),
      label: document.getElementById('theme-toggle').getAttribute('aria-label'),
    }));
    await themeContext.close();
    if (rememberedTheme.attribute !== 'dark' || rememberedTheme.stored !== 'dark') {
      throw new Error('manual theme was not remembered: ' + JSON.stringify(rememberedTheme));
    }

    const blockedStorage = await browser.newContext({ viewport: { width: 390, height: 760 } });
    await blockedStorage.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('blocked'); } });
    });
    const blockedPage = await blockedStorage.newPage();
    await blockedPage.goto(url + '?lang=zh-CN', { waitUntil: 'domcontentloaded' });
    await waitForTemplates(blockedPage);
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
      const expectedTemplate = language === 'en'
        ? 'Worldbuilding council · v1'
        : '世界观共创 · v1';
      for (let attempt = 0; attempt < 10; attempt += 1) {
        const probeContext = await browser.newContext({
          viewport: { width: 1200, height: 800 },
          locale: language === 'en' ? 'en-US' : 'zh-CN',
        });
        try {
          const probe = await probeContext.newPage();
          const nonGet = [];
          probe.on('request', request => {
            if (request.method() !== 'GET') nonGet.push(request.method() + ' ' + request.url());
          });
          await probe.goto(url + '?lang=' + encodeURIComponent(language), { waitUntil: 'domcontentloaded' });
          await waitForTemplates(probe);
          await probe.waitForFunction(() => document.querySelectorAll('.message-row').length >= 6);
          const detail = await probe.locator('#rail-session').textContent();
          if (!detail.includes(expectedTemplate) || detail.includes('worldbuilding_council') || nonGet.length) {
            throw new Error('localized template cold-load mismatch: ' + JSON.stringify({
              language, attempt, detail, nonGet,
            }));
          }
        } finally {
          await probeContext.close();
        }
      }
      const context = await browser.newContext({
        viewport: { width: 1200, height: 800 },
        colorScheme: 'light',
        locale: language === 'en' ? 'en-US' : 'zh-CN',
      });
      try {
        const page = await context.newPage();
        await page.goto(url + '?lang=' + encodeURIComponent(language), { waitUntil: 'domcontentloaded' });
        await waitForTemplates(page);
        await page.waitForFunction(() => document.querySelectorAll('.message-row').length >= 6);
        const value = await page.evaluate(() => ({
          language: document.documentElement.lang,
          membersTitle: document.getElementById('sheet-title').textContent,
          progress: document.getElementById('session-progress').textContent,
          count: document.getElementById('rail-count').textContent,
          roleMeta: document.querySelector('#rail-session .session-role-meta')?.textContent || '',
          templateDetail: document.getElementById('rail-session').textContent,
          horizontalOverflow: document.body.scrollWidth > window.innerWidth,
        }));
        const english = language === 'en';
        if (value.language !== language || value.horizontalOverflow) throw new Error('documentation locale mismatch: ' + JSON.stringify(value));
        if (english && (!value.membersTitle.includes('Participants') || !value.progress.includes('Round 1/3') || !value.roleMeta.includes('Model:')
            || !value.templateDetail.includes('Worldbuilding council · v1') || !value.templateDetail.includes('12 roles · 4 concurrent · 3 waves'))) {
          throw new Error('English UI is incomplete: ' + JSON.stringify(value));
        }
        if (!english && (!value.membersTitle.includes('群聊成员') || !value.progress.includes('第 1/3 轮') || !value.roleMeta.includes('模型：')
            || !value.templateDetail.includes('世界观共创 · v1') || !value.templateDetail.includes('12 个角色 · 4 个并发 · 3 个批次'))) {
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
    results.push({
      historyCheck, mobileHistoryCheck, mobileSheetChecks, stateChecks,
      sessionChecks, rememberedTheme, storageFallback, documentation,
    });
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(results, null, 2));
}

main().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
