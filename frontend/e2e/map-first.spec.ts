import {test,expect} from '@playwright/test';
import {mapFixture} from '../src/test/recall-fixtures';

test('map-first mode keeps coverage, rejected identities and unqueried hotels explicit',async({page})=>{
 let calls=0;
 await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
 await page.route('**/api/recall',route=>{
  calls++;const input=route.request().postDataJSON();
  expect(input.strategy).toBe('map_hotels');expect(input.branch_limit).toBe(3);
  const data=mapFixture();data.operation_id=input.operation_id;data.context=input.query;
  return route.fulfill({json:data});
 });
 await page.goto('/');
 await page.getByLabel('比较多家餐厅周边住宿').check();
 await expect(page.getByLabel('候选查找方式')).toHaveValue('branch_place');
 await page.getByLabel('候选查找方式').selectOption('map_hotels');
 await page.getByRole('button',{name:'查询房价'}).click();
 const coverage=page.getByRole('region',{name:'多门店覆盖记录'});
 await expect(coverage).toContainText('本轮查名5/5次');
 await coverage.getByText('地图酒店与查名记录（3处）').click();
 await expect(coverage).toContainText('其他酒店已排除');
 await expect(coverage).toContainText('本轮5次查名预算已用完');
 await expect(page.getByText('测试数据 · 仅用于界面验证',{exact:true})).toBeVisible();
 await coverage.screenshot({path:'../runtime/artifacts/map-first-web-desktop-test.png'});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await coverage.screenshot({path:'../runtime/artifacts/map-first-web-mobile-test.png'});
 expect(calls).toBe(1);
});
