import {test,expect} from '@playwright/test';
import {fixture} from '../src/test/recall-fixtures';
test.beforeEach(async({page})=>{
 await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
});
test('automatic recall preserves branch coverage, filters a branch and shows synthetic data',async({page})=>{
 let calls=0;
 await page.route('**/api/recall',async route=>{calls++;const input=route.request().postDataJSON();expect(input.branch_limit).toBe(3);expect(input.query.place).toBe('广州');await route.fulfill({json:fixture()});});
 await page.goto('/');await page.getByLabel('比较多家餐厅周边住宿').check();
 await page.getByRole('button',{name:'查询房价'}).click();
 const coverage=page.getByRole('region',{name:'多门店覆盖记录'});
 await expect(coverage).toContainText('测试餐厅2店');await expect(coverage).toContainText('测试餐厅3店');
 await expect(coverage).toContainText('达到查询截止');
 await expect(page.getByText('测试数据 · 仅用于界面验证',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'查看该门店候选'}).nth(1).click();
 await expect(page.getByText('第二门店住宿',{exact:true})).toBeVisible();
 await expect(page.getByText('界面测试住宿',{exact:true})).toHaveCount(0);
 expect(calls).toBe(1);
 await coverage.screenshot({path:'../runtime/artifacts/recall-desktop-test.png'});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await coverage.screenshot({path:'../runtime/artifacts/recall-mobile-test.png'});
});
test('stop reaches backend and a late recall cannot replace the next ordinary query',async({page})=>{
 let release!:()=>void;const pending=new Promise<void>(resolve=>{release=resolve;});let cancelled=0;
 await page.route('**/api/recall',async route=>{await pending;try{await route.fulfill({json:fixture()});}catch{/* browser aborted old request */}});
 await page.route('**/api/recall/*/cancel',async route=>{cancelled++;await route.fulfill({json:{accepted:true}});});
 await page.goto('/');await page.getByLabel('比较多家餐厅周边住宿').check();
 await page.getByRole('button',{name:'查询房价'}).click();
 await page.getByRole('button',{name:'停止查询'}).click();release();
 await expect.poll(()=>cancelled).toBe(1);
 await expect(page.getByText(/已请求停止/)).toBeVisible();
 await expect(page.getByText('第二门店住宿')).toHaveCount(0);
});
