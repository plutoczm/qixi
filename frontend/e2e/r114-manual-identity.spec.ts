import {test,expect} from '@playwright/test';

// Isolated static UI, every API synthetic, all external traffic aborted.
for(const mobile of [false,true]){
 test(`R114 manual city-missing identity ${mobile?'mobile':'desktop'}`,async({page})=>{
  if(mobile)await page.setViewportSize({width:390,height:844});
  await page.clock.install({time:new Date('2026-11-17T02:00:00Z')});
  const ctx='a'.repeat(32),anchor='b'.repeat(32),hotel='c'.repeat(32);
  const counts={search:0,resolve:0,nearby:0,status:0,confirm:0,revoke:0};
  let revision=0,intent=0,binding:Record<string,unknown>|null=null;
  const pair={hotel_option_id:hotel,source_hotel_id:101,map_name:'离线样例酒店(一店)',
   map_address:'浦东新区测试路12号',map_city:'上海市',source_name:'离线样例酒店(一店)',
   source_address:'浦东新区测试路12号',source_city_status:'missing',source_observed_at:'2026-11-17T02:00:00Z'};
  const identity=()=>({context_id:ctx,anchor_id:anchor,request_id:'r114-destination',status:'complete',revision,intent_seq:intent,
   expires_in_seconds:120,bindings:binding?[binding]:[],eligible_pairs:[pair],reason:'仅本轮城市缺失例外',error:null});
  const unexpected:string[]=[];
  await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  await page.route('**/api/**',route=>{unexpected.push(route.request().url());return route.abort();});
  await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true,web_ready:true,
   today_shanghai:'2026-11-17',features:{maps:true,fare:true,llm:true,multi_branch_recall:true},data_mode:'live',dataset:null,channels:{}}}));
  await page.route('**/api/checkpoint',route=>route.fulfill({json:{connected:true,status:'connected',disconnected_at:null}}));
  await page.route('**/api/search',route=>{
   const context=route.request().postDataJSON();counts.search++;
   return route.fulfill({json:{request_id:context.poi_name?'r114-destination':'r114-city',context,status:'complete',queried_at:new Date().toISOString(),
    source:{id:'rollinggo',name:'道旅'},data_mode:'test',hotels:[{id:101,name:pair.source_name,address:pair.source_address,
     image_url:null,hotel_stars:null,guest_score:null,reference_price_fen:18800,currency:'CNY',booking_url:null,unverified:[]}],
    coverage:{returned_count:1,requested_limit:context.limit,limited:false},warnings:[],error:null,current_page:1,next_page:null}});
  });
  await page.route('**/api/places/resolve',route=>{counts.resolve++;return route.fulfill({json:{provider:'amap',context_id:ctx,queried_at:new Date().toISOString(),
   data_mode:'test',status:'complete',expires_in_seconds:120,options:[{id:anchor,name:'上海迪士尼乐园',city:'上海市',address:'浦东新区',category:'景点'}],coverage_note:'离线样例',error:null}});});
  await page.route('**/api/places/nearby',route=>{counts.nearby++;return route.fulfill({json:{context_id:ctx,anchor_id:anchor,provider:'amap',queried_at:new Date().toISOString(),data_mode:'test',status:'complete',
    coverage_note:'离线样例',ranking_status:'not_comparable',ranking_reason:'缺少评价证据',error:null,
    hotels:[{id:hotel,name:pair.map_name,city:pair.map_city,address:pair.map_address,straight_line_meters:340,
     source_hotel_id:null,source_match:'unverified',walking_distance_meters:null,walking_duration_seconds:null,rating_status:'unavailable',map_rating:null,review_count:null,stayed_count:null,guest_review:null}]}});});
  await page.route('**/api/places/identity/status',route=>{counts.status++;return route.fulfill({json:identity()});});
  await page.route('**/api/places/identity/confirm',route=>{
   const body=route.request().postDataJSON();expect(body.confirmed).toBe(true);expect(body.expected_revision).toBe(revision);
   counts.confirm++;revision++;intent=body.intent_seq;binding={hotel_option_id:hotel,source_hotel_id:101,operation_id:body.operation_id,identity_basis:'user_confirmed'};
   return route.fulfill({json:identity()});
  });
  await page.route('**/api/places/identity/revoke',route=>{
   const body=route.request().postDataJSON();expect(body.target_operation_id).toBe(binding?.operation_id);expect(body.expected_revision).toBe(revision);
   counts.revoke++;revision++;intent=body.intent_seq;binding=null;return route.fulfill({json:identity()});
  });
  await page.goto('/');
  await page.getByLabel('希望住在哪个地方附近？').fill('上海');
  await page.getByRole('button',{name:/查询房价/}).click();
  const panel=page.getByRole('region',{name:'目的地周边酒店地图核对'});
  await panel.getByLabel('具体目的地').fill('上海迪士尼乐园');
  await panel.getByRole('button',{name:/搜索具体地点/}).click();
  await panel.getByRole('radio').check();
  await panel.getByRole('button',{name:/按此目的地查住宿来源/}).click();
  await expect(panel.getByText(/已取得目的地住宿参考/)).toBeVisible();
  await panel.getByRole('button',{name:/查找此地点附近的酒店/}).click();
  await expect(panel.getByText(/0 家与本次酒店来源唯一同店匹配/)).toBeVisible();
  const local=panel.locator('.nearby-manual-identity');
  await local.getByRole('button',{name:/人工核对同店/}).click();
  await expect(local.getByRole('combobox')).toHaveValue('');
  await expect(local.getByRole('button',{name:'确认同店'})).toBeDisabled();
  await local.getByRole('combobox').selectOption('101');
  await expect(local.getByRole('checkbox')).not.toBeChecked();
  await local.getByRole('checkbox').check();
  await local.getByRole('button',{name:'确认同店'}).click();
  await expect(local.getByText('你已确认同店 · 仅本轮有效')).toBeVisible();
  await expect(local.getByText('¥188')).toBeVisible();
  await expect(local.getByRole('button',{name:/查看这家酒店房型/})).toBeVisible();
  await expect(panel.getByRole('option',{name:/住客评分优先|四因素综合参考/})).toHaveCount(0);
  await panel.getByRole('combobox',{name:'参考排序方式'}).selectOption('price');
  await expect(panel.getByText('同店参考价排序',{exact:true})).toBeVisible();
  expect(counts).toEqual({search:2,resolve:1,nearby:1,status:1,confirm:1,revoke:0});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../runtime/closeout-manual-20261119-${mobile?'mobile':'desktop'}.png`,fullPage:true});
  await local.getByRole('button',{name:'撤销本次同店确认'}).click();
  await expect(local.getByText('¥188')).toHaveCount(0);
  await expect(local.getByRole('button',{name:/查看这家酒店房型/})).toHaveCount(0);
  await expect.poll(()=>counts.revoke).toBe(1);
  await local.getByRole('combobox').selectOption('101');await local.getByRole('checkbox').check();
  await local.getByRole('button',{name:'确认同店'}).click();await expect(local.getByText('¥188')).toBeVisible();
  await page.clock.fastForward(121000);
  await expect(panel.locator('.manual-identity-confirmed')).toHaveCount(0);
  expect(counts.search).toBe(2);expect(counts.resolve).toBe(1);expect(counts.nearby).toBe(1);
  expect(unexpected).toEqual([]);
 });
}
