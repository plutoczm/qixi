import {test,expect} from '@playwright/test';
import {searchFixture} from '../src/test/fixtures';
import {placesFixture,routeFixture} from '../src/test/travel-fixtures';

test.beforeEach(async({page})=>{
 await page.route('**/*',route=>{
  const url=new URL(route.request().url());
  return url.hostname==='127.0.0.1'&&!url.pathname.startsWith('/api/')?route.continue():route.abort();
 });
 await page.route('**/api/health',r=>r.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
 await page.route('**/api/search',r=>r.fulfill({json:searchFixture()}));
 await page.goto('/');
 await page.getByRole('textbox',{name:'希望住在哪个地方附近？'}).fill('广州');
 await page.getByRole('button',{name:'查询房价'}).click();
 await page.getByRole('button',{name:'查交通',exact:true}).click();
 await page.getByLabel('目的地名称或地址').fill('海底捞');
});

test('strict identity and explicit restaurant preserve route direction on desktop',async({page})=>{
 let calls=0;
 await page.route('**/travel/places',r=>r.fulfill({json:placesFixture()}));
 await page.route('**/travel/route',r=>{calls++;expect(r.request().postDataJSON()).toMatchObject({hotel_option_id:'h1',restaurant_option_id:'r2',confirm_hotel:false});const d=routeFixture();d.restaurant_name='海底捞乙店';return r.fulfill({json:d});});
 await page.getByRole('button',{name:'检索地点',exact:true}).click();
 await expect(page.getByText('严格同店匹配通过')).toBeVisible();
 await expect(page.getByLabel('住宿地图地点')).toHaveValue('h1');
 await expect(page.getByLabel('确认具体目的地')).toHaveValue('');
 await expect(page.getByRole('button',{name:'查询路线'})).toBeDisabled();
 await page.getByLabel('确认具体目的地').selectOption('r2');
 await page.getByRole('button',{name:'查询路线'}).click();
 await expect(page.getByText('来源预计601秒',{exact:false})).toBeVisible();
 await expect(page.locator('.hotel-transport-summary')).toContainText('海底捞乙店 → 酒店 · 步行约 11 分钟 · 高德');
 await page.getByRole('button',{name:'收起交通'}).click();
 await expect(page.locator('.hotel-transport-summary')).toContainText('步行约 11 分钟');
 await page.getByRole('button',{name:'查交通',exact:true}).click();
 await page.getByLabel('目的地名称或地址').fill('海底捞');
 await page.getByRole('button',{name:'检索地点',exact:true}).click();
 await page.getByLabel('确认具体目的地').selectOption('r2');
 await page.getByRole('button',{name:'查询路线'}).click();
 await expect.poll(()=>calls).toBe(2);
 await page.screenshot({path:'../runtime/artifacts/hotel-transport-desktop.png',fullPage:true});
});

test('uncertain identity requires explicit confirmation; mobile current fare stays a reference',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/travel/places',r=>r.fulfill({json:placesFixture('didi',false)}));
 await page.route('**/travel/route',r=>{expect(r.request().postDataJSON().confirm_hotel).toBe(true);return r.fulfill({json:{...routeFixture('didi'),hotel_match_basis:'user_confirmed'}});});
 await page.getByRole('button',{name:'滴滴估价',exact:true}).click();
 await page.getByRole('button',{name:'检索地点',exact:true}).click();
 await expect(page.getByText('同店匹配尚未确认')).toBeVisible();
 await page.getByLabel('住宿地图地点').selectOption('h1');
 await page.getByLabel('确认具体目的地').selectOption('r1');
 await expect(page.getByRole('button',{name:'查询当前车费'})).toBeDisabled();
 await page.getByRole('checkbox',{name:/我已核对名称和地址/}).check();
 await page.getByRole('button',{name:'查询当前车费'}).click();
 await expect(page.getByText('¥12—18',{exact:true})).toBeVisible();
 await expect(page.getByText('住宿对应关系已由你核对确认')).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'../runtime/artifacts/hotel-transport-mobile.png',fullPage:true});
});

test('context expiry and source failure never display synthetic routes',async({page})=>{
 await page.route('**/travel/places',r=>r.fulfill({json:placesFixture()}));
 await page.route('**/travel/route',r=>r.fulfill({status:410,json:{error:{code:'TRAVEL_CONTEXT_EXPIRED',message:'地点选择已过期，请重新检索地点。'}}}));
 await page.getByRole('button',{name:'检索地点',exact:true}).click();
 await page.getByLabel('确认具体目的地').selectOption('r1');
 await page.getByRole('button',{name:'查询路线'}).click();
 await expect(page.getByRole('alert')).toContainText('过期');
 await expect(page.getByText('目的地到住宿（步行）',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'腾讯地图',exact:true}).click();
 await page.route('**/travel/places',r=>r.fulfill({json:{...placesFixture('tencent'),status:'unavailable',context_id:null,error:{code:'SOURCE_DAILY_LIMIT',message:'来源日额度已达上限。'}}}));
 await page.getByRole('button',{name:'检索地点',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('额度');
 await expect(page.getByLabel('住宿地图地点')).toHaveCount(0);
});
