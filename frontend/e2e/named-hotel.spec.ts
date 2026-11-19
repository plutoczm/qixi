import {test,expect,type Page} from '@playwright/test';
import type {SearchInput} from '../src/types';
import {fixture} from '../src/test/recall-fixtures';

const home='如家酒店·neo（广州芳村花地湾地铁站店）';
const address='广州市荔湾区东漖北路566-568号';
const restaurant='海底捞火锅（荔胜广场店）';
const restaurantAddress='花地大道中66号荔胜广百广场6楼';

async function fillNamedForm(page:Page){
 await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
 await page.goto('/');
 await page.getByLabel('比较多家餐厅周边住宿').check();
 await page.getByLabel('候选查找方式').selectOption('named_hotel');
 await expect(page.getByRole('group',{name:'指定酒店严格核验'})).toBeVisible();
 await page.locator('#restaurant-keyword').fill(restaurant);
 await page.locator('#restaurant-address').fill(restaurantAddress);
 await page.locator('#named-hotel-name').fill(home);
 await page.locator('#named-hotel-address').fill(address);
 await page.locator('#budget').fill('200');
}

function namedFixture(context:SearchInput){
 const output=fixture();
 output.context=context;
 output.strategy='named_hotel';
 output.status='complete';
 output.hotels=[{...output.hotels[0],name:home,address,reference_price_fen:17200}];
 output.branches=[
  {...output.branches[0],role:'restaurant_body',name:restaurant,
   address:restaurantAddress,city:'广州市',status:'complete',hotel_ids:[101],
   hotel_targets:[{id:'strict-target',name:home,address,city:'广州市',status:'retained',
     reason:'source_city_unverified',source_count:4,hotel_id:101,match_status:'unverified'}],
   nearby_status:'complete',
  },
 ];
 output.recall_coverage={...output.recall_coverage,pages_requested:1,unique_places:1,
  role_restaurant_bodies:1,role_auxiliary:0,role_unknown:0,nearby_branches_attempted:0,
  named_hotel_map_lookups:1,named_hotel_map_matches:1,hotel_lookups_attempted:1,
  total_hotel_lookups:1,quote_http_requests:4,total_quote_http_requests:4,
  queried_branches:1,unqueried_targets:0,discovery_complete:true,
 };
 output.hotel_observations=[{hotel_id:101,queried_at:output.queried_at}];
 output.attempted_hotel_names=[home];
 output.continuation_cursor=null;
 output.continuation_expires_at=null;
 output.retained_total=1;
 output.walk_check_available=true;
 return output;
}

test('one user-named hotel bypasses neighborhood rank but keeps supplier city unknown',async({page})=>{
 let calls=0;
 await page.route('**/api/recall',route=>{
  calls++;
  const body=route.request().postDataJSON();
  expect(body.strategy).toBe('named_hotel');
  expect(body.provider).toBe('amap');
  expect(body.named_hotel).toEqual({name:home,address});
  expect(body.restaurant_keyword).toBe(restaurant);
  expect(body.restaurant_address).toBe(restaurantAddress);
  expect(body.query.place).toBe('广州');
  expect(body.query.adults).toBe(2);
  expect(body.query.rooms).toBe(1);
  expect(body.query.budget_fen).toBe(20000);
  return route.fulfill({json:namedFixture(body.query)});
 });
 await fillNamedForm(page);
 await page.getByRole('button',{name:'查询房价'}).click();
 const coverage=page.getByRole('region',{name:'多门店覆盖记录'});
 await expect(coverage).toContainText('用户点名酒店核验');
 await expect(coverage).toContainText('酒店独立地图检索1次');
 await expect(coverage).toContainText('严格地图同店结果1条');
 await expect(coverage).toContainText('不受周边前10条排序限制');
 await expect(page.getByRole('heading',{name:home})).toBeVisible();
 await coverage.getByText('地图酒店与查名记录（1处）').click();
 await expect(coverage).toContainText('酒店名址一致，来源城市未独立核实，待人工确认');
 await expect(coverage.getByRole('button',{name:/继续查下一轮/})).toHaveCount(0);
 await expect(page.getByText('指定酒店核验 · 来源参考价')).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:'../runtime/artifacts/p4-named-hotel-offline-mobile.png',fullPage:true});
 expect(calls).toBe(1);
});

test('incomplete or mismatched named hotel is not auto-filled or retried',async({page})=>{
 let calls=0;
 await page.route('**/api/recall',route=>{
  calls++;
  const body=route.request().postDataJSON();
  const output=namedFixture(body.query);
  output.hotels=[];
  output.branches[0].hotel_ids=[];
  output.branches[0].hotel_targets[0].status='excluded';
  output.branches[0].hotel_targets[0].reason='named_supplier_identity_mismatch';
  output.branches[0].hotel_targets[0].hotel_id=null;
  output.branches[0].hotel_targets[0].match_status=null;
  output.walk_check_available=false;
  return route.fulfill({json:output});
 });
 await fillNamedForm(page);
 await page.locator('#named-hotel-address').fill('');
 await page.getByRole('button',{name:'查询房价'}).click();
 expect(calls).toBe(0);
 await page.locator('#named-hotel-address').fill(address);
 await page.getByRole('button',{name:'查询房价'}).click();
 const coverage=page.getByRole('region',{name:'多门店覆盖记录'});
 await expect(coverage).toContainText('道旅结果缺少完整同名同址酒店，其他分店已排除');
 await expect(page.getByRole('heading',{name:'这次条件下，暂未查到住宿'})).toBeVisible();
 await expect(page.getByRole('button',{name:'清除预算再查一次'})).toHaveCount(0);
 await expect(page.getByRole('button',{name:/继续查下一轮/})).toHaveCount(0);
 expect(calls).toBe(1);
});

test('switching back to map-first does not leak named hotel details to unrelated lookup',async({page})=>{
 let calls=0;
 await page.route('**/api/recall',route=>{
  calls++;
  const body=route.request().postDataJSON();
  expect(body.strategy).toBe('map_hotels');
  expect(body.named_hotel).toBeNull();
  expect(body.restaurant_address).toBeNull();
  return route.fulfill({json:namedFixture(body.query)});
 });
 await fillNamedForm(page);
 await page.getByLabel('候选查找方式').selectOption('map_hotels');
 await page.getByRole('button',{name:'查询房价'}).click();
 await expect(page.getByRole('region',{name:'多门店覆盖记录'})).toBeVisible();
 expect(calls).toBe(1);
});
