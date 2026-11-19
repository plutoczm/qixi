import {test,expect} from '@playwright/test';

// Entire browser flow is synthetic; unknown API and external requests abort.
for(const mobile of [false,true]){
 test(`R107 destination-first ${mobile?'mobile':'desktop'}`,async({page})=>{
  if(mobile)await page.setViewportSize({width:390,height:844});
  const searches:Record<string,unknown>[]=[];
  const mapSessions:unknown[]=[];
  await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  await page.route('**/api/**',route=>route.abort());
  await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true,web_ready:true,
   today_shanghai:'2026-11-16',features:{maps:true,fare:true,llm:true,multi_branch_recall:true},data_mode:'live',dataset:null,channels:{}}}));
  await page.route('**/api/search',route=>{
   const context=route.request().postDataJSON();searches.push(context);
   return route.fulfill({json:{request_id:context.poi_name?'r107-destination':'r107-city',context,status:'complete',queried_at:new Date().toISOString(),
    source:{id:'rollinggo',name:'道旅'},data_mode:'test',hotels:[{id:101,name:'离线样例酒店',address:'上海市浦东新区测试路12号',
     image_url:null,hotel_stars:null,guest_score:null,reference_price_fen:18800,currency:'CNY',booking_url:null,unverified:[]}],
    coverage:{returned_count:1,requested_limit:context.limit,limited:false},warnings:[],error:null,current_page:1,next_page:null}});
  });
  await page.route('**/api/places/resolve',route=>route.fulfill({json:{provider:'amap',context_id:'a'.repeat(32),queried_at:new Date().toISOString(),
   data_mode:'test',status:'complete',expires_in_seconds:120,options:[{id:'b'.repeat(32),name:'上海迪士尼乐园',city:'上海市',address:'浦东新区',category:'景点'}],coverage_note:'离线样例',error:null}}));
  await page.route('**/api/places/nearby',route=>{
   mapSessions.push(route.request().postDataJSON().request_id);
   return route.fulfill({json:{context_id:'a'.repeat(32),anchor_id:'b'.repeat(32),provider:'amap',queried_at:new Date().toISOString(),data_mode:'test',status:'complete',
    coverage_note:'离线样例',ranking_status:'not_comparable',ranking_reason:'缺少评价证据',error:null,
    hotels:[{id:'c'.repeat(32),name:'离线样例酒店',city:'上海市',address:'上海市浦东新区测试路12号',straight_line_meters:340,
     source_hotel_id:101,source_match:'matched',walking_distance_meters:null,walking_duration_seconds:null,rating_status:'unavailable',map_rating:null,review_count:null,stayed_count:null,guest_review:null}]}});
  });
  await page.goto('/');
  await page.getByLabel('希望住在哪个地方附近？').fill('上海');
  await page.getByRole('button',{name:/查询房价/}).click();
  const panel=page.getByRole('region',{name:'目的地周边酒店地图核对'});
  await expect(panel).toBeVisible();
  expect(searches).toHaveLength(1);
  await panel.getByLabel('具体目的地').fill('上海迪士尼乐园');
  await panel.getByRole('button',{name:/搜索具体地点/}).click();
  await panel.getByRole('radio').check();
  await panel.getByRole('button',{name:/按此目的地查住宿来源/}).click();
  await expect(panel.getByText(/已取得目的地住宿参考/)).toBeVisible();
  expect(searches).toHaveLength(2);
  expect(searches[1]).toMatchObject({place:'上海',poi_name:'上海迪士尼乐园',limit:10});
  await panel.getByRole('button',{name:/查找此地点附近的酒店/}).click();
  await expect(panel.getByText(/1 家与本次酒店来源唯一同店匹配/)).toBeVisible();
  expect(mapSessions).toEqual(['r107-destination']);
  await expect(panel.locator('.nearby-source-price')).toHaveText(/188/);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../runtime/r107-${mobile?'mobile':'desktop'}.png`,fullPage:true});
 });
}
