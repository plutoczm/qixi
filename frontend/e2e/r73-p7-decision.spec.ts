import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';
import {join} from 'node:path';

/**
 * r73: the P7 decision panel in a real browser, zero supplier HTTP.
 *
 * The quote payload is the existing r68 fixture produced by the real Pydantic
 * contract, with the *production* decision builder applied to it by
 * scripts/export_r73_decision_fixture.py. Because no live source supplies a
 * guest score or a review count, the only honest verdict is
 * ``not_comparable``. A browser fixture showing a ranking would be invented
 * data; the ranked rendering itself is covered by DecisionPanel.test.tsx.
 */

const ART = '../runtime/artifacts';
const FIXTURES = join(import.meta.dirname, 'fixtures');

function payload(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

async function serveOffline(page: Page) {
  await page.route('**/api/search', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(payload('r68-search.json')),
  }));
  await page.route('**/api/hotels/*/quotes', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(payload('r73-quotes.json')),
  }));
}

async function fillForm(page: Page) {
  await page.goto('/');
  await expect(page.getByText('从一个目的地开始')).toBeVisible();
  await page.getByLabel('目的地').fill('广州');
  await page.getByLabel('地点类型').selectOption('城市');
  await page.getByLabel('入住日期').fill('2026-11-13');
  await page.getByLabel('退房日期').fill('2026-11-14');
  await page.getByLabel('每房成人').selectOption({label: '2 位成人'});
  await page.getByLabel('房间数量').selectOption({label: '1 间房'});
  await page.getByLabel(/每晚预算参考/).fill('200');
}

test('r73 offline: P7 panel shows not-comparable with no invented ranking', async ({page}) => {
  test.setTimeout(3 * 60 * 1000);
  mkdirSync(ART, {recursive: true});
  const calls: string[] = [];
  page.on('request', request => {
    const url = request.url();
    if (url.includes('/api/')) calls.push(url.replace(/^https?:\/\/[^/]+/, ''));
  });
  await serveOffline(page);
  await fillForm(page);
  await page.getByRole('button', {name: '查询房价'}).click();

  const card = page.locator('article.hotel-card').first();
  await expect(card).toBeVisible({timeout: 30 * 1000});
  await card.getByRole('button', {name: /查看房型/}).click();

  const panel = page.locator('section.decision-panel');
  await expect(panel).toBeVisible({timeout: 30 * 1000});
  await expect(panel).toContainText('本轮不可比较，未提供排名');
  await expect(panel).toContainText('住客评分 → 评价人数 → 已知费用小计 → 输入顺序');
  await expect(panel).toContainText('不跨响应拼接');
  await expect(panel).toContainText('当前估价不等于未来深夜实价');
  await expect(panel).toContainText('测试数据 · 仅用于界面验证');
  await expect(panel.locator('.decision-order')).toHaveCount(0);

  const text = (await panel.textContent()) ?? '';
  for (const label of ['通过', '未核实', '不通过']) expect(text).toContain(label);
  for (const forbidden of ['已核实可订', '库存充足', '实付总价已确认', '锁价成功',
    '逐夜达标', '全网最低', '多源比价', '订单成功']) {
    expect(text).not.toContain(forbidden);
  }

  const dims = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(dims.scrollWidth).toBe(dims.clientWidth);
  await page.screenshot({path: join(ART, 'r73-p7-decision.png'), fullPage: true});
  await panel.screenshot({path: join(ART, 'r73-p7-decision-panel.png')});

  // /api/health is a local metadata read at page start, not a supplier call.
  const local = ['/api/health', '/api/search', '/api/hotels/'];
  const unexpected = [...new Set(calls)].filter(
    path => !local.some(prefix => path.startsWith(prefix)),
  );
  expect(unexpected).toEqual([]);
});
