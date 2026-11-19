import {test,expect} from '@playwright/test';
import {searchFixture} from '../src/test/fixtures';
import {fixture as recallFixture,mapFixture,continuationFixture} from '../src/test/recall-fixtures';

test.beforeEach(async({page})=>{
  await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
});

function hotelsFixture() {
  const base=searchFixture();
  base.hotels=Array.from({length:4},(_,index)=>({
    ...base.hotels[0],
    id:101+index,
    name:'模拟候选酒店'+(index+1),
    address:'广州 · 模拟路'+(index+1)+'号',
    currency:'CNY',
    reference_price_fen:16000+index*1500,
  }));
  return base;
}

test('r41 manual candidate comparison is bounded, reference-only and sends zero detail calls',async({page})=>{
  let searches=0;let details=0;
  await page.route('**/api/search',route=>{searches++;return route.fulfill({json:hotelsFixture()});});
  await page.route('**/api/hotels/*/quotes',route=>{details++;return route.fulfill({status:500,json:{}});});
  await page.goto('/');
  await page.getByRole('button',{name:'查询房价'}).click();
  await expect(page.getByRole('region',{name:'同轮酒店参考候选'})).toBeVisible();
  await expect(page.getByRole('region',{name:'酒店参考候选对比'})).toHaveCount(0);
  await page.getByRole('button',{name:'加入对比：模拟候选酒店1'}).click();
  await page.getByRole('button',{name:'加入对比：模拟候选酒店2'}).click();
  const panel=page.getByRole('region',{name:'酒店参考候选对比'});
  await expect(panel).toContainText('参考候选对比 · 2家');
  await expect(panel).toContainText('模拟数据 · 本次比较不能作为真实房价证据');
  await expect(panel.getByRole('row',{name:/相对所选最低参考价/})).toContainText('+15元（仅参考差额）');
  await expect(panel.getByRole('row',{name:/预订资格/})).toContainText('未核实');
  await page.getByRole('button',{name:'加入对比：模拟候选酒店3'}).click();
  await expect(page.getByRole('button',{name:'加入对比：模拟候选酒店4'})).toBeDisabled();
  await panel.getByRole('button',{name:'移出对比：模拟候选酒店2'}).click();
  await page.getByRole('button',{name:'加入对比：模拟候选酒店4'}).click();
  await expect(panel).toContainText('模拟候选酒店4');
  expect(searches).toBe(1);expect(details).toBe(0);
  await panel.getByRole('button',{name:'清空对比'}).click();
  await expect(panel).toHaveCount(0);
  await expect(page.getByText('已选 0/3 家')).toBeVisible();
});

test('r41 new search invalidates comparison selection without replay or extra source calls',async({page})=>{
  let searches=0;let details=0;
  await page.route('**/api/search',route=>{
    searches++;
    const data=hotelsFixture();
    data.request_id='offline-query-'+searches;
    return route.fulfill({json:data});
  });
  await page.route('**/api/hotels/*/quotes',route=>{details++;return route.fulfill({status:500,json:{}});});
  await page.goto('/');
  await page.getByRole('button',{name:'查询房价'}).click();
  await page.getByRole('button',{name:'加入对比：模拟候选酒店1'}).click();
  await page.getByRole('button',{name:'加入对比：模拟候选酒店2'}).click();
  await expect(page.getByRole('region',{name:'酒店参考候选对比'})).toBeVisible();
  await page.getByRole('button',{name:'查询房价'}).click();
  await expect(page.getByRole('region',{name:'酒店参考候选对比'})).toHaveCount(0);
  await expect(page.getByText('已选 0/3 家')).toBeVisible();
  expect(searches).toBe(2);expect(details).toBe(0);
});

test('r41 changing branch filter clears hidden comparisons in the same recall',async({page})=>{
  let recalls=0;
  await page.route('**/api/recall',route=>{recalls++;return route.fulfill({json:recallFixture()});});
  await page.goto('/');
  await page.getByLabel('比较多家餐厅周边住宿').check();
  await page.getByRole('button',{name:'查询房价'}).click();
  await page.getByRole('button',{name:/加入对比：界面测试住宿/}).click();
  await page.getByRole('button',{name:/加入对比：第二门店住宿/}).click();
  await expect(page.getByRole('region',{name:'酒店参考候选对比'})).toBeVisible();
  await page.getByRole('region',{name:'多门店覆盖记录'}).getByRole('button',{name:'查看该门店候选'}).first().click();
  await expect(page.getByRole('region',{name:'酒店参考候选对比'})).toHaveCount(0);
  await expect(page.getByText('已选 0/3 家')).toBeVisible();
  expect(recalls).toBe(1);
});

test('r41 mobile comparison scrolls within its panel and never overflows the document',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.route('**/api/search',route=>route.fulfill({json:hotelsFixture()}));
  await page.goto('/');
  await page.getByRole('button',{name:'查询房价'}).click();
  await page.getByRole('button',{name:'加入对比：模拟候选酒店1'}).click();
  await page.getByRole('button',{name:'加入对比：模拟候选酒店2'}).click();
  await page.getByRole('button',{name:'加入对比：模拟候选酒店3'}).click();
  const panel=page.getByRole('region',{name:'酒店参考候选对比'});
  await expect(panel).toContainText('参考候选对比 · 3家');
  await expect(panel.getByRole('row',{name:/酒店地址/})).toContainText('模拟路');
  const sizes=await page.evaluate(()=>{
    const scroller=document.querySelector('.candidate-comparison-scroll');
    return {
      documentWidth:document.documentElement.scrollWidth,
      viewWidth:window.innerWidth,
      tableScroll:scroller?.scrollWidth??0,
      tableClient:scroller?.clientWidth??0,
    };
  });
  expect(sizes.documentWidth).toBeLessThanOrEqual(sizes.viewWidth);
  expect(sizes.tableScroll).toBeGreaterThan(sizes.tableClient);
});

test('r41 explicit recall continuation invalidates old comparison on revision change',async({page})=>{
  let first=mapFixture();let continued=0;
  await page.route('**/api/recall',route=>{
    first=mapFixture();first.context=route.request().postDataJSON().query;
    return route.fulfill({json:first});
  });
  await page.route('**/api/recall/continue',route=>{
    continued++;
    return route.fulfill({json:continuationFixture(first,route.request().postDataJSON().operation_id)});
  });
  await page.goto('/');
  await page.getByLabel('比较多家餐厅周边住宿').check();
  await page.getByLabel('候选查找方式').selectOption('map_hotels');
  await page.getByRole('button',{name:'查询房价'}).click();
  await page.getByRole('button',{name:/加入对比：界面测试住宿/}).click();
  await page.getByRole('button',{name:/加入对比：第二门店住宿/}).click();
  await expect(page.getByRole('region',{name:'酒店参考候选对比'})).toBeVisible();
  await page.getByRole('button',{name:'继续查下一轮（最多5次）'}).click();
  await expect(page.getByText('续查新增住宿',{exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'酒店参考候选对比'})).toHaveCount(0);
  await expect(page.getByText('已选 0/3 家')).toBeVisible();
  expect(continued).toBe(1);
});
