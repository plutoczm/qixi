import {expect,test} from '@playwright/test';

/** Offline browser acceptance. All API calls are intercepted; no supplier/map account is used. */
test.beforeEach(async({page})=>{
  await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'
    ?route.continue():route.abort());
  await page.route('**/api/**',route=>route.abort());
  await page.route('**/api/health',route=>route.fulfill({json:{
    status:'ok',provider:'rollinggo',credential_configured:true,web_ready:true,
    today_shanghai:'2026-11-12',features:{maps:true,fare:true,llm:true,multi_branch_recall:true},
    data_mode:'live',dataset:null,channels:{},
  }}));
  await page.route('**/api/search',route=>{
    const context=route.request().postDataJSON();
    return route.fulfill({json:{
      request_id:'r102-browser-mocked',status:'complete',context,queried_at:new Date().toISOString(),
      source:{id:'rollinggo',name:'道旅'},data_mode:'test',
      hotels:[{id:101,name:'花园酒店上海迪士尼店',address:'浦东新区繁荣路12号',
        image_url:null,hotel_stars:null,guest_score:null,reference_price_fen:32800,
        currency:'CNY',booking_url:null,unverified:['费用待核实']}],
      coverage:{returned_count:1,requested_limit:20,limited:false},
      warnings:[],error:null,current_page:1,next_page:null,
    }});
  });
  await page.route('**/api/places/resolve',route=>route.fulfill({json:{
    provider:'amap',context_id:'a'.repeat(32),queried_at:new Date().toISOString(),
    data_mode:'test',status:'complete',expires_in_seconds:120,
    options:[{id:'b'.repeat(32),name:'上海迪士尼乐园',address:'浦东新区川沙镇',city:'上海市',category:'景点'}],
    coverage_note:'离线浏览器测试',error:null,
  }}));
  await page.route('**/api/places/nearby',route=>route.fulfill({json:{
    context_id:'a'.repeat(32),anchor_id:'b'.repeat(32),provider:'amap',
    queried_at:new Date().toISOString(),data_mode:'test',status:'complete',
    coverage_note:'浏览器样例范围',ranking_status:'not_comparable',
    ranking_reason:'评分与评价人数缺失，不可综合排名。',
    hotels:[{
      id:'c'.repeat(32),name:'花园酒店上海迪士尼店',address:'浦东新区繁荣路12号',city:'上海市',
      straight_line_meters:340,source_hotel_id:101,source_match:'matched',
      walking_distance_meters:null,walking_duration_seconds:null,rating_status:'unavailable',
      review_count:null,stayed_count:null,
    },{
      id:'d'.repeat(32),name:'仅地图候选酒店',address:'浦东新区某街道',city:'上海市',
      straight_line_meters:740,source_hotel_id:null,source_match:'unverified',
      walking_distance_meters:null,walking_duration_seconds:null,rating_status:'unavailable',
      review_count:null,stayed_count:null,
    }],
    error:null,
  }}));
  await page.route('**/api/places/walk',route=>route.fulfill({json:{
    context_id:'a'.repeat(32),hotel_option_id:'c'.repeat(32),provider:'amap',
    queried_at:new Date().toISOString(),data_mode:'test',status:'complete',
    distance_meters:700,duration_seconds:600,error:null,
  }}));
});

test('real browser: explicit map lookup, source identity, walk and gate',async({page})=>{
  const calls:string[]=[];
  page.on('request',req=>{if(req.url().includes('/api/places/'))calls.push(new URL(req.url()).pathname);});
  await page.goto('/');
  await page.getByLabel('希望住在哪个地方附近？').fill('上海');
  await page.getByRole('button',{name:/查询房价/}).click();
  const panel=page.getByRole('region',{name:'目的地周边酒店地图核对'});
  await expect(panel).toBeVisible();
  expect(calls).toEqual([]);
  await panel.getByLabel('具体目的地').fill('上海迪士尼乐园');
  await expect(panel.getByText('中国大陆',{exact:true})).toBeVisible();
  await panel.getByRole('button',{name:/搜索具体地点/}).click();
  await expect(panel.getByRole('radio')).toBeVisible();
  expect(calls).toEqual(['/api/places/resolve']);
  await panel.getByRole('radio').check();
  await panel.getByRole('button',{name:/查找此地点附近的酒店/}).click();
  await expect(panel.getByText('仅地图候选酒店')).toBeVisible();
  await expect(panel.getByText('¥328')).toBeVisible();
  await expect(panel.getByRole('option',{name:/住客评分优先|四因素综合参考/})).toHaveCount(0);
  await panel.getByLabel('参考排序方式').selectOption('price');
  await expect(panel.getByText('同店参考价排序',{exact:true})).toBeVisible();
  expect(calls).toEqual(['/api/places/resolve','/api/places/nearby']);
  await panel.getByRole('button',{name:'查步行路线'}).first().click();
  await expect(panel.getByText(/目的地→酒店实测步行 700 米/)).toBeVisible();
  await panel.getByRole('checkbox',{name:/只看已核实步行/}).check();
  await expect(panel.getByText('仅地图候选酒店')).toHaveCount(0);
  expect(calls).toEqual(['/api/places/resolve','/api/places/nearby','/api/places/walk']);
  await page.screenshot({path:'../runtime/closeout-nearby-desktop-20261119.png',fullPage:true});
});

test('mobile layout keeps the map panel within viewport without automatic API access',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const calls:string[]=[];
  page.on('request',req=>{if(req.url().includes('/api/places/'))calls.push(req.url());});
  await page.goto('/');
  await page.getByLabel('希望住在哪个地方附近？').fill('上海');
  await page.getByRole('button',{name:/查询房价/}).click();
  const panel=page.getByRole('region',{name:'目的地周边酒店地图核对'});
  await expect(panel).toBeVisible();
  const box=await panel.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeLessThanOrEqual(392);
  expect(calls).toEqual([]);
  await page.screenshot({path:'../runtime/closeout-nearby-mobile-20261119.png',fullPage:true});
});
