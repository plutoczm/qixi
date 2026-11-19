import {test,expect} from '@playwright/test';
import {walkRecallFixture,walkBatchFixture} from '../src/test/walk-fixtures';
test.beforeEach(async({page})=>{
 await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
 await page.route('**/api/recall',route=>route.fulfill({json:walkRecallFixture()}));
});
async function recall(page:import('@playwright/test').Page){
 await page.goto('/');await page.getByLabel('比较多家餐厅周边住宿').check();
 await page.getByRole('button',{name:'查询房价'}).click();
 await page.getByRole('button',{name:'批量核对位置与交通（最多3组）'}).click();
}
test('both identities are reviewed before walking; direction and uncertainty remain visible',async({page})=>{
 let routeCalls=0;
 await page.route('**/api/branch-walk/batch',route=>route.fulfill({json:walkBatchFixture(route.request().postDataJSON())}));
 await page.route('**/api/branch-walk/route',route=>{
  routeCalls++;const input=route.request().postDataJSON();expect(input.confirm_hotel).toBe(true);expect(input.confirm_restaurant).toBe(true);
  const pair=walkBatchFixture({request_id:'ui-test-search',operation_id:'00000000000000000000000000000000',offset:0},false).pairs[0];
  pair.context_id=input.context_id;pair.status='route_verified';pair.walk_limit_status='within_1500';pair.transport_status='pass_walk_or_drive';pair.source_http_requests=7;pair.walk={origin_name:'地图测试餐厅1店',destination_name:'地图测试住宿1',distance_meters:900,duration_seconds:600,point_precision:'poi_point',night_access_verified:false};pair.hotel_match_basis='user_confirmed';pair.restaurant_match_basis='user_confirmed';
  return route.fulfill({json:pair});
 });
 await recall(page);
 const pair=page.getByRole('article',{name:'测试餐厅1店与测试住宿1的交通核对'});
 await pair.getByLabel('住宿地图地点').selectOption('h101');
 await pair.getByLabel('召回门店地图地点').selectOption('r101');
 await pair.getByLabel('我已核对所选地点对应原道旅住宿').check();
 await expect(pair.getByRole('button',{name:'确认并查询交通路线'})).toBeDisabled();expect(routeCalls).toBe(0);
 await pair.getByLabel('我已核对所选地点对应原召回门店').check();
 await pair.getByRole('button',{name:'确认并查询交通路线'}).click();
 await expect(pair.getByText(/符合步行1500米或驾车15分钟任一条件/)).toBeVisible();
 await expect(pair.getByText(/步行路线：地图测试餐厅1店 → 地图测试住宿1 · 900米/)).toBeVisible();
 await expect(pair.getByText(/不包含打车接驾\/候车/)).toBeVisible();
 await pair.screenshot({path:'../runtime/artifacts/branch-walk-desktop-test.png'});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await pair.screenshot({path:'../runtime/artifacts/branch-walk-mobile-test.png'});
});
test('expired points require a fresh batch and reset both confirmations',async({page})=>{
 let batches=0;
 await page.route('**/api/branch-walk/batch',route=>{batches++;const data=walkBatchFixture(route.request().postDataJSON());if(batches===1)data.pairs.forEach(p=>{p.expires_in_seconds=0;});return route.fulfill({json:data});});
 await recall(page);await expect(page.getByText(/本组点位已过期/)).toHaveCount(3);
 await page.getByRole('button',{name:'重新核对本组点位'}).click();
 await expect(page.getByText(/本组点位已过期/)).toHaveCount(0);
 await expect(page.getByLabel('住宿地图地点',{exact:true}).first()).toHaveValue('');
 expect(batches).toBe(2);
});
test('stop reaches the batch operation and late routes never appear',async({page})=>{
 let release!:()=>void;const pending=new Promise<void>(resolve=>{release=resolve;});let cancels=0;
 await page.route('**/api/branch-walk/batch',async route=>{const input=route.request().postDataJSON();await pending;try{await route.fulfill({json:walkBatchFixture(input,true)});}catch{/* aborted browser request */}});
 await page.route('**/api/branch-walk/*/cancel',route=>{cancels++;return route.fulfill({json:{accepted:true}});});
 await recall(page);await page.getByRole('button',{name:'停止本轮核对'}).click();release();
 await expect.poll(()=>cancels).toBe(1);
 await expect(page.getByText(/已请求停止；正在进行的请求/)).toBeVisible();
 await expect(page.getByText(/餐厅→住宿交通子指标/)).toHaveCount(0);
});

test('new transport OR states show driving at 900s and reject only when both limits are exceeded',async({page})=>{
 await page.route('**/api/branch-walk/batch',route=>route.fulfill({
  json:walkBatchFixture(route.request().postDataJSON(),true),
 }));
 await recall(page);
 const first=page.getByRole('article',{name:'测试餐厅1店与测试住宿1的交通核对'});
 const second=page.getByRole('article',{name:'测试餐厅2店与测试住宿2的交通核对'});
 const third=page.getByRole('article',{name:'测试餐厅3店与测试住宿3的交通核对'});
 await expect(first).toContainText('900米 / 1200秒');
 await expect(first).toContainText('符合步行1500米或驾车15分钟任一条件');
 await expect(first).toContainText('步行已达标，本轮无需追加驾车查询');
 await expect(second).toContainText('1600米 / 600秒');
 await expect(second).toContainText('3200米 / 900秒');
 await expect(second).toContainText('预计≤15分钟');
 await expect(second).toContainText('符合步行1500米或驾车15分钟任一条件');
 await expect(third).toContainText('1900米');
 await expect(third).toContainText('3200米 / 901秒');
 await expect(third).toContainText('步行与驾车均超出限制');
 await expect(third).toContainText('不包含打车接驾/候车');
 await expect(page.getByText(/路线不是夜间可达或全部住宿条件的保证/)).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'../runtime/artifacts/p4-transport-or-mobile-test.png',fullPage:true});
});
