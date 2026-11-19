// Four actual model-backed UI turns; all hotel/map/third-party page calls blocked.
const {chromium}=require('../../frontend/node_modules/playwright');
const fs=require('fs');
const path=require('path');
const dir=path.resolve(process.argv[2]);
const base='http://127.0.0.1:8016/';
const manifest=JSON.parse(fs.readFileSync(path.join(dir,'manifest.json'),'utf8'));
const day=manifest.corpus.input_cases[0].expected[0].check_in;
async function main(){
  if(fs.existsSync(path.join(dir,'browser.json')))throw Error('PHASE_ALREADY_EXECUTED');
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const results=[];
  try{
    for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
      const page=await browser.newPage({viewport:{width,height}});
      const errors=[];let blocked=0,external=0,posts=0;
      page.on('pageerror',()=>errors.push('PAGE_ERROR'));
      await page.route('**/*',route=>{
        const req=route.request(),url=new URL(req.url());
        if(!req.url().startsWith(base)){external++;return route.abort();}
        if(url.pathname.startsWith('/api/')){
          const allowed=['/api/health','/api/models'].includes(url.pathname)||url.pathname.startsWith('/api/conversations');
          if(!allowed){blocked++;return route.fulfill({status:403,json:{error:{message:'评测隔离'}}});}
          if(req.method()==='POST'&&url.pathname.endsWith('/operations'))posts++;
        }
        return route.continue();
      });
      const row={name,viewport:width,turns:[]};
      try{
        await page.goto(base,{waitUntil:'domcontentloaded'});
        await page.locator('#assistant-panel > summary').click();
        const full=`广州${day}入住住1晚，1间房每房2成人，无儿童，每房每晚300元以内，去海底捞吃火锅，住它附近。`;
        const texts=name==='mobile'?['想去海底捞吃火锅，住它附近，最好地铁方便。',full]:[full,'预算改成每房每晚200元以内，其它不变。'];
        for(const text of texts){
          await page.locator('#natural-message').fill(text);
          const wait=page.waitForResponse(async response=>{
            if(response.request().method()!=='GET'||!response.url().includes('/api/conversations/'))return false;
            try{return ['succeeded','failed','timed_out','cancelled'].includes((await response.json()).status);}catch{return false;}
          },{timeout:70000});
          const start=Date.now();
          await page.getByRole('button',{name:/发送给小助手|发送这条补充/}).click();
          const response=await wait;
          const state=await response.json();
          row.turns.push({status:state.status,error:state.error?.code??null,question:state.question,
            draft:state.draft,query:state.query,missing:state.missing,warnings:state.warnings,
            conversation_id:state.conversation_id,query_ready:!!state.query,seconds:(Date.now()-start)/1000});
          if(state.status!=='succeeded')break;
        }
        row.noHorizontalOverflow=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1);
        row.visibleText=(await page.locator('.natural-query').innerText()).slice(0,7000);
        await page.locator('.natural-query').screenshot({path:path.join(dir,`browser-${name}.png`)});
        if(row.turns.length===2&&row.turns.every(t=>t.status==='succeeded')){
          // Current UI calls this natural-stay-anchor; default keeps the lodging city.
          await page.locator('#natural-stay-anchor').selectOption('');
          const ack=page.getByRole('checkbox',{name:/我知道这些附加条件仍需另外核实/});
          if(await ack.count())await ack.check();
          await page.getByRole('button',{name:'核对后填入查询表单'}).click();
          row.handoff={place:await page.locator('#place').inputValue(),check_in:await page.locator('#check-in').inputValue()};
          row.handoffPassed=row.handoff.place==='广州'&&row.handoff.check_in===day;
        }
      }catch(error){row.driver_error=error.name;}
      row.modelPosts=posts;row.blockedOtherApi=blocked;row.external=external;row.pageErrors=errors.length;
      results.push(row);
      console.log('BROWSER_CASE',JSON.stringify({name,modelPosts:posts,statuses:row.turns.map(t=>t.status),handoff:row.handoffPassed,overflow:row.noHorizontalOverflow,error:row.driver_error}));
      fs.writeFileSync(path.join(dir,'browser.json'),JSON.stringify(results,null,2));
      await page.close();
    }
  }finally{await browser.close();}
}
main().catch(error=>{console.error('BROWSER_DRIVER_ERROR',error.name);process.exitCode=1;});
