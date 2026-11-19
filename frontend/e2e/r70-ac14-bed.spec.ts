import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';

/**
 * r70: the AC14 bed-place relaxation in a real browser, zero supplier HTTP.
 *
 * The payload is produced by `scripts/export_r70_bed_fixture.py` through the
 * real contract models, so what is checked here is the page: whether "接受后
 * 通过" fits the status column, whether the relaxed sentence reads as the user's
 * choice rather than as our presumption, and whether a per-person bed price is
 * labelled as such instead of being shown as a room price.
 */

const ART = '../runtime/artifacts';
const FIXTURES = join(import.meta.dirname, 'fixtures');

function payload(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

async function serveOffline(page: Page) {
  await page.route('**/api/search', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(payload('r70-bed-search.json')),
  }));
  await page.route('**/api/hotels/*/quotes', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(payload('r70-bed-quotes.json')),
  }));
}

test('r70 offline: accepting a bed relaxes two rows and never reads as a presumption', async ({page}) => {
  test.setTimeout(3 * 60 * 1000);
  mkdirSync(ART, {recursive: true});
  const calls: string[] = [];
  page.on('request', request => {
    const url = request.url();
    if (url.includes('/api/')) calls.push(url.replace(/^https?:\/\/[^/]+/, ''));
  });
  await serveOffline(page);
  await page.goto('/');

  const bedToggle = page.getByLabel(/床位/);
  await expect(bedToggle).toBeVisible();
  // AC14's first half: off by default, and the page says what it would change.
  await expect(bedToggle).not.toBeChecked();
  await expect(page.getByText(/只放宽两项：整间客房、房内独卫/)).toBeVisible();
  await expect(page.getByText(/不勾选时床位房一律排除/)).toBeVisible();

  await page.getByLabel('目的地').fill('广州');
  await page.getByLabel('地点类型').selectOption('城市');
  await page.getByLabel('入住日期').fill('2026-11-13');
  await page.getByLabel('退房日期').fill('2026-11-14');
  await page.getByLabel(/每晚预算参考/).fill('200');
  await bedToggle.check();
  await expect(bedToggle).toBeChecked();
  await page.getByRole('button', {name: '查询房价'}).click();

  const cards = page.locator('article.hotel-card');
  await expect(cards.first()).toBeVisible({timeout: 30 * 1000});
  await cards.first().getByRole('button', {name: /查看房型/}).click();
  const evidence = page.getByRole('region', {name: '报价资格与费用核对'});
  await expect(evidence).toBeVisible({timeout: 30 * 1000});

  const rows = page.locator('.quote-row');
  const bedRow = rows.first();

  // --- the relaxed rows are marked as the user's decision -------------------
  await expect(bedRow.getByText('接受后通过')).toHaveCount(2);
  await expect(bedRow.getByText(/2项因你接受床位而放宽：整间客房、房内独立卫浴/)).toBeVisible();
  await expect(bedRow.getByText(/这不是来源核实，也不是我们的推定/)).toBeVisible();
  // A relaxed row must never be printed as our presumption.
  await expect(bedRow.getByText('通过（推定）')).toHaveCount(0);

  // --- a per-person bed price is labelled per person -----------------------
  await expect(bedRow.getByText(/床位单价 · 每人每晚 · 非最终总价/)).toBeVisible();
  await expect(bedRow.getByText(/你已接受床位：按 6000 分 × 1 晚 × 2 人/)).toBeVisible();
  await expect(bedRow.getByText(/已知费用小计：/)).toBeVisible();

  // --- the phrases that would be a lie ------------------------------------
  for (const forbidden of ['已核实可订', '库存充足', '实付总价已确认', '锁价成功', '逐夜达标']) {
    await expect(page.getByText(forbidden)).toHaveCount(0);
  }

  // --- layout: nothing overflows, the status column does not wrap ----------
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.scrollWidth).toBe(overflow.clientWidth);
  const statusBox = await bedRow.locator('.tier-conditions em').first().boundingBox();
  expect(statusBox?.height ?? 0).toBeLessThan(30);
  const priceBox = await bedRow.locator('.quote-price').evaluate(el => ({
    width: el.getBoundingClientRect().width,
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
    children: [...el.querySelectorAll('span,small')].map(child => ({
      text: (child.textContent ?? '').slice(0, 40),
      width: child.getBoundingClientRect().width,
      scrollWidth: child.scrollWidth,
      right: child.getBoundingClientRect().right,
    })),
  }));

  await bedRow.scrollIntoViewIfNeeded();
  await bedRow.screenshot({path: `${ART}/r70-bed-row.png`, animations: 'disabled'});
  await bedRow.locator('.quote-price').screenshot({path: `${ART}/r70-price-block.png`});
  await evidence.screenshot({path: `${ART}/r70-bed-panel.png`});
  await page.screenshot({path: `${ART}/r70-bed-page.png`, fullPage: true});

  writeFileSync(`${ART}/r70-page-check.json`, JSON.stringify({
    ok: true,
    api_calls: calls,
    overflow,
    status_box_height: statusBox?.height ?? null,
    first_row_text: await bedRow.innerText(),
    price_box: priceBox,
  }, null, 2));
});
