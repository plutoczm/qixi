import {test,expect} from '@playwright/test';
import {searchFixture,quotesFixture} from '../src/test/fixtures';

test.beforeEach(async({page})=>{
  await page.route('**/api/health',route=>route.fulfill({json:{
    status:'ok',provider:'tuniu',credential_configured:false,web_ready:true,
    today_shanghai:'2026-11-02',
    features:{maps:true,fare:true,llm:true,multi_branch_recall:false},
  }}));
});

test('Tuniu is the formal hotel source and unavailable never falls back to RollingGo',async({page})=>{
  let searches=0;
  await page.route('**/api/search',route=>{
    searches++;
    const result=searchFixture();
    return route.fulfill({json:{
      ...result,source:{id:'tuniu',name:'途牛'},
      status:'unavailable',hotels:[],error:{
        code:'CONFIGURATION_MISSING',
        message:'途牛尚未授权；请先完成合法的本机认证配置。',
      },
    }});
  });
  await page.goto('/');
  await expect(page.getByText('来源：途牛',{exact:true})).toBeVisible();
  await expect(page.getByText(/途牛多门店召回尚未适配/)).toBeVisible();
  await expect(page.getByLabel('比较多家餐厅周边住宿')).toHaveCount(0);
  await page.getByRole('button',{name:'查询房价'}).click();
  await expect(page.getByRole('alert')).toContainText('途牛尚未授权');
  await expect(page.getByText('界面测试住宿')).toHaveCount(0);
  expect(searches).toBe(1);
});

test('Tuniu room reference on mobile cannot open a RollingGo handoff',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const result=searchFixture();
  result.source={id:'tuniu',name:'途牛'};
  result.request_id='tuniu-e2e';
  const quote=quotesFixture();
  quote.source={id:'tuniu',name:'途牛'};
  quote.request_id='tuniu-e2e';
  quote.booking_status='matched';
  quote.occupancy_status='matched';
  quote.booking_url='https://rollinggo.cn/not-the-source';
  quote.quotes[0].price_basis='supplier_reference';
  quote.quotes[0].source_inventory_observed=2;
  await page.route('**/api/search',route=>route.fulfill({json:result}));
  await page.route('**/api/hotels/101/quotes',route=>route.fulfill({json:quote}));
  await page.goto('/');
  await page.getByRole('button',{name:'查询房价'}).click();
  await expect(page.getByText('途牛 · 住宿')).toBeVisible();
  await page.getByRole('button',{name:/查看房型/}).click();
  await expect(page.getByText('途牛房型参考价 · 非最终总价')).toBeVisible();
  await expect(page.getByText(/来源本次库存观察：2/)).toBeVisible();
  await expect(page.getByRole('link',{name:/前往.*核实条件/})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});


test('manual page two is requested once and queryId stays out of browser contracts',async({page})=>{
  let firstCalls=0;
  let manualCalls=0;
  const first=searchFixture();
  first.source={id:'tuniu',name:'途牛'};
  first.request_id='tuniu-e2e-paged';
  first.hotels=[];
  first.status='partial';
  first.coverage.returned_count=0;
  first.coverage.limited=true;
  first.current_page=1;
  first.next_page=2;
  const second=searchFixture();
  second.source={id:'tuniu',name:'途牛'};
  second.request_id=first.request_id;
  second.status='partial';
  second.current_page=2;
  second.next_page=null;
  await page.route('**/api/search',route=>{
    firstCalls++;
    return route.fulfill({json:first});
  });
  await page.route('**/api/search/next',async route=>{
    manualCalls++;
    const input=route.request().postDataJSON();
    expect(input).toEqual({request_id:first.request_id,expected_page:2});
    await route.fulfill({json:second});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'查询房价'}).click();
  const button=page.getByRole('button',{name:/手动查看途牛第 2 页/});
  await expect(button).toBeVisible();
  expect(manualCalls).toBe(0);
  await button.click();
  await expect(page.getByText('界面测试住宿')).toBeVisible();
  await expect(page.getByRole('button',{name:/手动查看途牛第/})).toHaveCount(0);
  expect(firstCalls).toBe(1);
  expect(manualCalls).toBe(1);
});
