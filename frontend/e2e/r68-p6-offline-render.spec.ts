import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';

/**
 * r68: the P6 qualification panel in a real browser, with zero supplier HTTP.
 *
 * Why this exists: r68 shipped the whole engine offline, so the only evidence
 * for the page was jsdom unit tests. Those cannot show whether the layout
 * survives long Chinese sentences, whether the tier badge is legible, or
 * whether the fee blocks line up. Here the real built page renders a payload
 * produced by the real contract models, with the API intercepted in-browser.
 *
 * What it is NOT: real supplier data. The payload is a fixture; the point is
 * the rendering and the wording, not the numbers. `r67-p5-live.spec.ts` is the
 * one that spends real HTTP.
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
    status: 200, contentType: 'application/json', body: JSON.stringify(payload('r68-quotes.json')),
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

test('r68 offline: three tiers, fee blocks and budget basis render legibly', async ({page}) => {
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

  const cards = page.locator('article.hotel-card');
  await expect(cards.first()).toBeVisible({timeout: 30 * 1000});
  await cards.first().getByRole('button', {name: /查看房型/}).click();

  const evidence = page.getByRole('region', {name: '报价资格与费用核对'});
  await expect(evidence).toBeVisible({timeout: 30 * 1000});

  // --- the summary must count from the server verdict, not from price order --
  await expect(evidence).toContainText('本轮4条报价中');
  await expect(evidence).toContainText('2条合格（含推定标注）');
  await expect(evidence).toContainText('最低合格报价为第3条');
  await expect(evidence).toContainText('不代表可订');

  // --- each tier has its own wording ---------------------------------------
  // Scoped to the quote list on purpose: the summary sentence above also
  // contains "合格（含推定标注）", so counting inside the panel would pass even
  // if no row rendered a badge at all.
  const rows = page.locator('.quote-row');
  await expect(rows.locator('.tier-heading strong', {hasText: '合格（含推定标注）'})).toHaveCount(2);
  await expect(rows.locator('.tier-heading strong', {hasText: '已排除'})).toHaveCount(2);
  await expect(rows.getByText('通过（推定）').first()).toBeVisible();
  await expect(rows.getByText('参考层未通过，不展示为参考价').first()).toBeVisible();

  // --- fee blocks stay apart and never sum an unknown ----------------------
  await expect(rows.getByText('已知费用小计：').first()).toBeVisible();
  await expect(rows.getByText(/未计入合计（1项，不当 0 也不当已付）：/).first()).toBeVisible();
  // The unknown fee is listed, and never folded into the subtotal.
  await expect(rows.getByText('到店清洁费').first()).toBeVisible();

  // --- the phrases that would be a lie ------------------------------------
  for (const forbidden of ['已核实可订', '库存充足', '实付总价已确认', '锁价成功', '逐夜达标']) {
    await expect(page.getByText(forbidden)).toHaveCount(0);
  }

  await evidence.screenshot({path: `${ART}/r68-qualification-panel.png`});
  // Row 1 is the 169 元 qualified twin room, row 0 the excluded dormitory bed;
  // screenshot the whole row so the price block and the panel are seen together.
  for (const [index, name] of [[0, 'excluded'], [2, 'qualified']] as const) {
    const row = rows.nth(index);
    await row.scrollIntoViewIfNeeded();
    await row.screenshot({path: `${ART}/r68-row-${name}.png`, animations: 'disabled'});
  }
  await page.screenshot({path: `${ART}/r68-quote-page.png`, fullPage: true});

  writeFileSync(`${ART}/r68-page-check.json`, JSON.stringify({
    ok: true,
    api_calls: calls,
    row_texts: await rows.allInnerTexts(),
  }, null, 2));
});
