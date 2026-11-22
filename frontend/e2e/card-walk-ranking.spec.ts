import {test,expect} from '@playwright/test';
import {searchFixture} from '../src/test/fixtures';
import {placesFixture,routeFixture} from '../src/test/travel-fixtures';
import type {SearchInput} from '../src/types';

test('ranks hotels by fresh card routes from the confirmed search destination',async({page})=>{
  test.setTimeout(75000);
  await page.clock.install({time:new Date()});
  const browserTime=()=>page.evaluate(()=>Date.now());
  const origin={id:'park',name:'同名公园',address:'广州市越秀区中山路1号',city:'广州市'};
  const original=searchFixture();
  const hotelNames=['长路线酒店','短路线酒店','起点不同酒店','路线过期酒店'];
  const hotels=hotelNames.map((name,index)=>({...original.hotels[0],id:101+index,name,
    reference_price_fen:index===1?9000:18000}));
  let businessRequests=0;
  page.on('request',request=>{
    if(new URL(request.url()).pathname.startsWith('/api/')&&!request.url().endsWith('/api/health'))businessRequests++;
  });
  let searches=0,routeCalls=0,operationId='';
  await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    return url.hostname==='127.0.0.1'&&!url.pathname.startsWith('/api/')?route.continue():route.abort();
  });
  await page.route('**/api/health',route=>route.fulfill({json:{
    status:'ok',provider:'rollinggo',credential_configured:true,features:{parallel_search:true},
  }}));
  await page.route('**/api/search/parallel',route=>{
    const {operation_id,query}=route.request().postDataJSON() as {operation_id:string;query:SearchInput};
    searches++;
    operationId=operation_id;
    return route.fulfill({json:{operation_id,context:query,status:'partial',sources:[{
      id:'rollinggo',status:'complete',http_used:1,observed_at:original.queried_at,unavailable_reason:null,candidates:[],
      search:{...original,context:query,hotels},
    },{
      id:'tuniu',status:'complete',http_used:1,observed_at:original.queried_at,unavailable_reason:null,candidates:[],
      search:{...original,context:query,request_id:'tuniu-filter-test',source:{id:'tuniu',name:'途牛'},hotels:[hotels[1]]},
    }]}});
  });
  await page.route('**/api/places/resolve',async route=>route.fulfill({json:{
    provider:'amap',context_id:'confirmed-search-origin',queried_at:new Date(await browserTime()).toISOString(),
    data_mode:'test',status:'complete',options:[{...origin,category:'景点'}],expires_in_seconds:120,
    error:null,warnings:[],
  }}));
  await page.route('**/travel/places',async route=>{
    const hotel=hotels.find(item=>route.request().url().includes('/hotels/'+item.id+'/'))!;
    const fixture=placesFixture();
    return route.fulfill({json:{
      ...fixture,hotel_id:hotel.id,hotel_name:hotel.name,queried_at:new Date(await browserTime()).toISOString(),
      hotels:[{...fixture.hotels[0],name:hotel.name,address:hotel.address}],
      restaurants:[{...fixture.restaurants[0],...origin,address:hotel.id===103?'广州市越秀区中山路2号':origin.address}],
    }});
  });
  await page.route('**/travel/route',async route=>{
    const hotel=hotels.find(item=>route.request().url().includes('/hotels/'+item.id+'/'))!;
    routeCalls++;
    const fixture=routeFixture();
    const distances=[1800,300,10,20];
    return route.fulfill({json:{
      ...fixture,hotel_id:hotel.id,hotel_name:hotel.name,restaurant_name:origin.name,
      queried_at:new Date((await browserTime())-(hotel.id===104?121000:0)).toISOString(),
      return_walk:{...fixture.return_walk,origin_name:origin.name,destination_name:hotel.name,
        distance_meters:distances[hotel.id-101]},
    }});
  });

  await page.goto('/');
  await page.getByRole('textbox',{name:'希望住在哪个地方附近？'}).fill(origin.name);
  await page.getByLabel('地点类型').selectOption('景点');
  await page.getByRole('button',{name:'查询房价'}).click();
  const shortlist=page.locator('.parallel-recommendations');
  const links=shortlist.getByRole('link');
  await expect(links).toHaveText(hotelNames);

  async function confirmDestination(){
    await page.getByLabel('所在城市',{exact:true}).fill('广州市');
    await page.getByRole('button',{name:/搜索具体地点/}).click();
    await page.getByRole('radio',{name:/同名公园.*中山路1号/}).check();
  }

  async function checkRoute(hotel:(typeof hotels)[number]){
    const card=page.getByRole('region',{name:'道旅搜索结果'}).locator('.hotel-card')
      .filter({has:page.getByRole('heading',{name:hotel.name,exact:true})});
    await card.getByRole('button',{name:'查交通',exact:true}).click();
    await expect(card.getByLabel('目的地名称或地址')).toHaveValue(origin.name);
    await card.getByRole('button',{name:'检索地点',exact:true}).click();
    await card.getByLabel('确认具体目的地').selectOption(origin.id);
    await card.getByRole('button',{name:'查询路线',exact:true}).click();
    await expect(card.getByText('目的地到住宿（步行）',{exact:true})).toBeVisible();
    await card.getByRole('button',{name:'收起交通',exact:true}).click();
  }

  await confirmDestination();
  expect(routeCalls).toBe(0);
  for(const hotel of hotels)await checkRoute(hotel);
  expect(routeCalls).toBe(4);
  await expect(links).toHaveText([hotelNames[1],hotelNames[0],hotelNames[2],hotelNames[3]]);
  await expect(shortlist.getByText(/已核实步行 300 米/)).toBeVisible();
  await expect(shortlist.getByText(/已核实步行 1800 米/)).toBeVisible();
  await expect(shortlist.getByText(/已核实步行/)).toHaveCount(2);

  const requestsBeforeFilters=businessRequests;
  const rollinggo=page.getByRole('region',{name:'道旅搜索结果'});
  const tuniu=page.getByRole('region',{name:'途牛搜索结果'});
  await rollinggo.getByRole('searchbox',{name:'筛选名称或地址'}).fill('长路线');
  await expect(links).toHaveText([hotelNames[0],hotelNames[1]]);
  const targetId='parallel-hotel-'+operationId+'-tuniu-filter-test:102';
  await expect(links.nth(1)).toHaveAttribute('href','#'+targetId);

  await rollinggo.getByRole('textbox',{name:'人民币参考价上限（元）'}).fill('100');
  await expect(links).toHaveText([hotelNames[1]]);
  const link=links.first();
  await link.press('Enter');
  const target=page.locator('[id="'+targetId+'"]');
  await expect(target).toBeFocused();
  await expect(target.getByRole('heading',{name:hotelNames[1]})).toBeInViewport();

  await tuniu.getByRole('searchbox',{name:'筛选名称或地址'}).fill('不匹配');
  await expect(links).toHaveCount(0);
  await expect(shortlist).toContainText('当前筛选下没有可推荐的候选');
  await rollinggo.getByRole('button',{name:'清除本地筛选'}).click();
  await tuniu.getByRole('button',{name:'清除本地筛选'}).click();
  await expect(links).toHaveText([hotelNames[1],hotelNames[0],hotelNames[2],hotelNames[3]]);
  expect(businessRequests).toBe(requestsBeforeFilters);

  await page.clock.fastForward(121000);
  await expect(links).toHaveText(hotelNames);
  await expect(shortlist.getByText(/已核实步行/)).toHaveCount(0);

  await page.getByRole('button',{name:'重新选地点'}).click();
  await confirmDestination();
  await checkRoute(hotels[1]);
  await expect(shortlist.getByText(/已核实步行 300 米/)).toBeVisible();

  await page.getByRole('button',{name:'查询房价'}).click();
  await expect.poll(()=>searches).toBe(2);
  await expect(links).toHaveText(hotelNames);
  await confirmDestination();
  await expect(shortlist.getByText(/已核实步行/)).toHaveCount(0);
  expect(routeCalls).toBe(5);
});
