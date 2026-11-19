import {test,expect} from '@playwright/test';
import {mapFixture,continuationFixture} from '../src/test/recall-fixtures';
import type {RecallResponse} from '../src/types';

async function begin(page:import('@playwright/test').Page){
 await page.goto('/');await page.getByLabel('比较多家餐厅周边住宿').check();
 await page.getByLabel('候选查找方式').selectOption('map_hotels');
 await page.getByRole('button',{name:'查询房价'}).click();
 await expect(page.getByText('第二门店住宿',{exact:true})).toBeVisible();
}
test('continuation is explicit, appends a delta and leaves old price timestamps unchanged',async({page})=>{
 let calls=0;let first:RecallResponse;
 await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
 await page.route('**/api/recall',route=>{
  first=mapFixture();first.context=route.request().postDataJSON().query;
  return route.fulfill({json:first});
 });
 await page.route('**/api/recall/continue',route=>{
  calls++;const input=route.request().postDataJSON();
  expect(Object.keys(input).sort()).toEqual(['continuation_cursor','operation_id','request_id']);
  expect(input.continuation_cursor).toBe(first.continuation_cursor);
  return route.fulfill({json:continuationFixture(first,input.operation_id)});
 });
 await begin(page);expect(calls).toBe(0);
 const old=page.locator('.hotel-card').filter({has:page.getByRole('heading',{name:'界面测试住宿',exact:true})});
 const stamp=await old.locator('.query-time').innerText();const price=await old.locator('.price').innerText();
 await page.getByRole('button',{name:'继续查下一轮（最多5次）'}).click();
 await expect(page.getByText('续查新增住宿',{exact:true})).toBeVisible();
 await expect(old.locator('.query-time')).toHaveText(stamp);await expect(old.locator('.price')).toHaveText(price);
 await expect(page.getByText(/累计10次（第2轮）/)).toBeVisible();
 await expect(page.getByRole('button',{name:'继续查下一轮（最多5次）'})).toHaveCount(0);
 const coverage=page.getByRole('region',{name:'多门店覆盖记录'});
 await coverage.screenshot({path:'../runtime/artifacts/recall-continue-desktop-test.png'});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await coverage.screenshot({path:'../runtime/artifacts/recall-continue-mobile-test.png'});
 expect(calls).toBe(1);
});
test('stop preserves the current union and a late delta cannot overwrite it',async({page})=>{
 let first:RecallResponse;let release!:()=>void;let cancels=0;
 const pending=new Promise<void>(resolve=>{release=resolve;});
 await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
 await page.route('**/api/recall',route=>{first=mapFixture();first.context=route.request().postDataJSON().query;return route.fulfill({json:first});});
 await page.route('**/api/recall/continue',async route=>{
  const input=route.request().postDataJSON();await pending;
  try{await route.fulfill({json:continuationFixture(first,input.operation_id)});}catch{/* request explicitly stopped */}
 });
 await page.route('**/api/recall/*/cancel',route=>{cancels++;return route.fulfill({json:{accepted:true}});});
 await begin(page);await page.getByRole('button',{name:'继续查下一轮（最多5次）'}).click();
 await expect(page.getByText('第二门店住宿',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'停止查询'}).click();release();
 await expect.poll(()=>cancels).toBe(1);
 await expect(page.getByText(/已停止续查，原结果保留/)).toBeVisible();
 await expect(page.getByText('续查新增住宿',{exact:true})).toHaveCount(0);
});
