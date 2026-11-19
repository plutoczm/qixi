import {test,expect} from '@playwright/test';
import {conversationFixture} from '../src/test/conversation-fixtures';
import {searchFixture} from '../src/test/fixtures';

test.beforeEach(async({page})=>{
 await page.route('**/api/health',r=>r.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true}}));
 await page.route('**/api/conversations',r=>r.fulfill({json:conversationFixture()}));
});

test('natural draft hands off only after review, then searches real contract fields',async({page})=>{
 let searches=0;
 await page.route('**/api/conversations/test-conversation/operations',r=>r.fulfill({json:conversationFixture('succeeded')}));
 await page.route('**/api/search',r=>{searches++;expect(r.request().postDataJSON()).toMatchObject({place:'广州',budget_fen:20000,adults:2,rooms:1});return r.fulfill({json:searchFixture()});});
 await page.goto('/');
 await page.getByLabel('用一句话描述住宿需求').fill('广州，明天住1晚，每房2成人，无儿童，1间房，每房每晚200元。');
 await page.getByRole('button',{name:'理解条件',exact:true}).click();
 await expect(page.getByText('条件草稿',{exact:true})).toBeVisible();
 expect(searches).toBe(0);
 await expect(page.getByRole('button',{name:'GPT · 暂缓'})).toBeDisabled();
 await page.getByRole('button',{name:'核对后填入查询表单'}).click();
 await expect(page.getByLabel('目的地',{exact:true})).toHaveValue('广州');
 await page.getByRole('button',{name:'查询房价'}).click();
 await expect.poll(()=>searches).toBe(1);
 await expect(page.getByText('界面测试住宿',{exact:true})).toBeVisible();
 await page.screenshot({path:'../runtime/artifacts/natural-desktop-test.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'../runtime/artifacts/natural-mobile-test.png',fullPage:true});
});

test('running conditions can be replaced and late older polling cannot overwrite them',async({page})=>{
 let sends=0;let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve;});
 await page.route('**/api/conversations/test-conversation/operations',r=>{
  sends++;
  return r.fulfill({json:sends===1?{...conversationFixture('queued'),client_sequence:1,operation_id:'old'}:conversationFixture('succeeded','杭州')});
 });
 await page.route('**/api/conversations/test-conversation',async r=>{await gate;await r.fulfill({json:conversationFixture('succeeded','广州')});});
 await page.goto('/');await page.getByLabel('用一句话描述住宿需求').fill('广州住宿条件');
 await page.getByRole('button',{name:'理解条件',exact:true}).click();
 await expect(page.getByRole('button',{name:'停止理解'})).toBeVisible();
 await page.getByLabel('用一句话描述住宿需求').fill('改去杭州');
 await page.getByRole('button',{name:'用新条件替换'}).click();
 await expect(page.getByText('杭州 · 城市',{exact:true})).toBeVisible();
 release();
 await expect(page.getByText('广州 · 城市',{exact:true})).toHaveCount(0);
});

test('stop response leaves the last committed conditions and never starts a quote',async({page})=>{
 await page.route('**/api/conversations/test-conversation/operations',r=>r.fulfill({json:{...conversationFixture('running'),operation_id:'running'}}));
 await page.route('**/api/conversations/test-conversation/cancel',r=>r.fulfill({json:conversationFixture('cancelled')}));
 await page.route('**/api/conversations/test-conversation',r=>r.fulfill({json:{...conversationFixture('running'),operation_id:'running'}}));
 await page.goto('/');await page.getByLabel('用一句话描述住宿需求').fill('广州住宿条件');
 await page.getByRole('button',{name:'理解条件',exact:true}).click();
 await page.getByRole('button',{name:'停止理解'}).click();
 await expect(page.getByText('已停止；以下保留最后已提交的条件。')).toBeVisible();
 await expect(page.getByRole('button',{name:'核对后填入查询表单'})).toBeDisabled();
});
