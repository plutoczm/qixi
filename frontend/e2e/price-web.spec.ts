import {test,expect} from '@playwright/test';
import {searchFixture,quotesFixture} from '../src/test/fixtures';
test.beforeEach(async({page})=>{
 await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
});
test('search, room details, refresh and trusted booking handoff',async({page})=>{
 let details=0;
 await page.route('**/api/search',route=>route.fulfill({json:searchFixture()}));
 await page.route('**/api/hotels/101/quotes',route=>{details++;return route.fulfill({json:quotesFixture()});});
 await page.goto('/');await expect(page.getByText('从一个目的地开始')).toBeVisible();
 await page.getByRole('button',{name:'查询房价'}).click();
 await expect(page.getByText('界面测试住宿',{exact:true})).toBeVisible();
 await expect(page.getByText('测试数据 · 仅用于界面验证')).toBeVisible();
 await expect(page.getByRole('link',{name:/前往道旅/})).toHaveCount(0);
 await page.getByRole('button',{name:/查看房型/}).click();
 await expect(page.getByText('预订后不可退款')).toBeVisible();
 const evidence=page.getByRole('region',{name:'报价资格与费用核对'});
 await expect(evidence.getByText('未核实',{exact:true})).toHaveCount(4);
 await expect(evidence).toContainText('均价不能直接换算完整总价');
 await evidence.screenshot({path:'../runtime/artifacts/quote-evidence-desktop.png'});
 await expect(page.getByRole('link',{name:/前往道旅/})).toHaveAttribute('href',quotesFixture().booking_url!);
 await page.getByRole('button',{name:/刷新报价/}).click();
 await expect.poll(()=>details).toBe(2);
 await expect(page.getByText('测试大床房')).toBeVisible();
 await page.screenshot({path:'../runtime/artifacts/desktop-results.png',fullPage:true});
});
test('empty and provider failure remain different states',async({page})=>{
 let calls=0;
 await page.route('**/api/search',route=>{calls++;const data=searchFixture();data.hotels=[];data.context.budget_fen=calls===1?20000:null;if(calls>1){data.status='unavailable';data.error={code:'TIMEOUT',message:'查询超时，请稍后重试。'};}return route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'查询房价'}).click();
 await expect(page.getByText('这次条件下，暂未查到住宿')).toBeVisible();
 await page.getByRole('button',{name:'清除预算再查一次'}).click();
 await expect(page.getByRole('alert')).toContainText('查询超时');
});
test('mobile layout has no overflow and validates children',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/search',route=>route.fulfill({json:searchFixture()}));
 await page.route('**/api/hotels/101/quotes',route=>route.fulfill({json:quotesFixture()}));
 await page.goto('/');await page.getByLabel('每房儿童',{exact:true}).selectOption('1');
 await page.getByRole('button',{name:'查询房价'}).click();
 await expect(page.getByLabel('儿童1年龄')).toBeFocused();
 await page.getByLabel('儿童1年龄').fill('7');await page.getByRole('button',{name:'查询房价'}).click();
 await page.getByRole('button',{name:/查看房型/}).click();
 await expect(page.getByText('测试大床房')).toBeVisible();
 const evidence=page.getByRole('region',{name:'报价资格与费用核对'});
 await expect(evidence.getByText('未核实',{exact:true})).toHaveCount(4);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await evidence.screenshot({path:'../runtime/artifacts/quote-evidence-mobile.png'});
 await page.screenshot({path:'../runtime/artifacts/mobile-results.png',fullPage:true});
});

test('mismatched children or rooms never expose a booking action',async({page})=>{
 await page.route('**/api/search',route=>route.fulfill({json:{...searchFixture(),context:{...searchFixture().context,rooms:2,children_ages:[5]}}}));
 await page.route('**/api/hotels/101/quotes',route=>route.fulfill({json:{...quotesFixture(),booking_status:'mismatch',booking_url:null,context:{...quotesFixture().context,rooms:2,children_ages:[5]}}}));
 await page.goto('/');await page.getByLabel('房间数量').selectOption('2');await page.getByLabel('每房儿童',{exact:true}).selectOption('1');await page.getByLabel('儿童1年龄').fill('5');await page.getByRole('button',{name:'查询房价'}).click();
 await page.getByRole('button',{name:/查看房型/}).click();
 await expect(page.getByText(/来源预订链接与本次入住条件不一致/)).toBeVisible();
 await expect(page.getByRole('link',{name:/前往道旅/})).toHaveCount(0);
 await expect(page.getByText(/入住条件未核实，跳转已停用/)).toBeVisible();
 await page.screenshot({path:'../runtime/artifacts/booking-mismatch.png',fullPage:true});
});

test('r32 supplier rate plans show source-only budget/capacity/request flags without promising bookability',async({page})=>{
 const search=searchFixture();search.context.budget_fen=20000;
 const detail=quotesFixture();detail.context.budget_fen=20000;
 detail.occupancy_status='unverified';detail.booking_status='matched';
 const base=detail.quotes[0];
 detail.quotes=[
  {...base,id:'rate-172',amount_fen:17200,max_occupancy:4,on_request:true},
  {...base,id:'rate-174',amount_fen:17400,max_occupancy:4,on_request:false},
  {...base,id:'rate-unknown',amount_fen:null,max_occupancy:null,on_request:null},
 ];
 await page.route('**/api/search',route=>route.fulfill({json:search}));
 await page.route('**/api/hotels/101/quotes',route=>route.fulfill({json:detail}));
 await page.goto('/');
 await page.getByRole('button',{name:'查询房价'}).click();
 await page.getByRole('button',{name:/查看房型/}).click();
 const evidence=page.getByRole('region',{name:'报价资格与费用核对'});
 await expect(evidence).toContainText('道旅返回3条报价方案（不是可订房间数）');
 await expect(evidence).toContainText('需二次确认1条、未标记需申请1条、申请状态未知1条');
 await expect(evidence.getByText('未核实',{exact:true})).toHaveCount(4);
 const first=page.getByRole('group',{name:/测试大床房 · 报价方案1/});
 await expect(first).toContainText('¥172');
 await expect(first).toContainText('来源标记：需二次确认');
 await expect(first).toContainText('不代表最终含费价达标');
 const second=page.getByRole('group',{name:/测试大床房 · 报价方案2/});
 await expect(second).toContainText('来源未标记需申请，不代表库存已确认');
 const unknown=page.getByRole('group',{name:/测试大床房 · 报价方案3/});
 await expect(unknown).toContainText('参考价待核实');
 await expect(unknown).toContainText('容量待核实');
 await expect(page.getByRole('link',{name:/前往道旅核实条件/})).toBeVisible();
 await expect(page.getByRole('link',{name:/立即预订/})).toHaveCount(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:'../runtime/artifacts/p4-room-quote-evidence-desktop.png',fullPage:true});
});

test('r40 browsing evidence never becomes a verified-bookable claim, even for matched source inputs',async({page})=>{
  const search=searchFixture();search.data_mode='live';
  const detail=quotesFixture();detail.data_mode='live';
  detail.booking_status='matched';detail.occupancy_status='matched';
  detail.quotes[0].amount_fen=9900;
  detail.quotes[0].max_occupancy=4;
  detail.quotes[0].on_request=false;
  await page.route('**/api/search',route=>route.fulfill({json:search}));
  await page.route('**/api/hotels/101/quotes',route=>route.fulfill({json:detail}));
  await page.goto('/');
  await page.getByRole('button',{name:'查询房价'}).click();
  const stages=page.getByRole('region',{name:'两层产品能力与状态'});
  await expect(stages).toContainText('已取得来源候选');
  await expect(stages).toContainText('尚未证明符合全部预订条件');
  await page.getByRole('button',{name:/查看房型/}).click();
  const gate=page.getByRole('region',{name:'房型可订资格状态'});
  await expect(gate).toContainText('尚未核实通过');
  await expect(gate).toContainText('来源链接参数已核对（不是已确认可订）');
  await expect(gate).toContainText('逐夜及全部强制费用');
  await expect(page.getByRole('link',{name:/前往道旅核实条件/})).toBeVisible();
  await expect(page.getByRole('link',{name:/立即预订|确认有房|已核实可订/})).toHaveCount(0);
});

test('r40 mobile browsing keeps blocked supplier handoff and no overflow',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const search=searchFixture();search.data_mode='live';
  const detail=quotesFixture();detail.data_mode='live';
  detail.booking_status='mismatch';detail.booking_url=null;
  detail.occupancy_status='unverified';
  await page.route('**/api/search',route=>route.fulfill({json:search}));
  await page.route('**/api/hotels/101/quotes',route=>route.fulfill({json:detail}));
  await page.goto('/');
  await page.getByRole('button',{name:'查询房价'}).click();
  await expect(page.getByRole('region',{name:'两层产品能力与状态'})).toContainText('已取得来源候选');
  await page.getByRole('button',{name:/查看房型/}).click();
  await expect(page.getByRole('region',{name:'房型可订资格状态'})).toContainText('来源链接条件冲突，已停用');
  await expect(page.getByRole('link',{name:/前往道旅/})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});
