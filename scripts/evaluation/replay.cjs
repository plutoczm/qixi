// Replay captured real-model output to inspect the handoff without spending more tokens.
const {chromium}=require('../../frontend/node_modules/playwright');
const fs=require('fs'),path=require('path');
const dir=path.resolve(process.argv[2]);
const real=JSON.parse(fs.readFileSync(path.join(dir,'browser.json'),'utf8'));
const states=JSON.parse(fs.readFileSync(path.join(dir,'browser-validated-states.json'),'utf8'));
const base='http://127.0.0.1:8016/';
async function main(){
 const browser=await chromium.launch({channel:'msedge',headless:true});const results=[];
 try{
  for(const record of real){
   const page=await browser.newPage({viewport:{width:record.viewport,height:record.name==='mobile'?844:1000}});
   let posts=0,searches=0;
   const state=states[record.name],draft=state.draft;
   if(state.status!=='succeeded'||!state.query)throw Error('REAL_STATE_NOT_SUBMITTABLE');
   await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(!route.request().url().startsWith(base))return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    if(url.pathname==='/api/health')return route.fulfill({json:{status:'ok',provider:'rollinggo',credential_configured:true,web_ready:true,
      today_shanghai:'2026-11-16',features:{maps:true,fare:false,llm:true,multi_branch_recall:true},data_mode:'live',dataset:null,channels:{}}});
    if(url.pathname==='/api/conversations')return route.fulfill({json:{...state,status:'idle',revision:0,query:null}});
    if(url.pathname.endsWith('/operations')){posts++;return route.fulfill({json:state});}
    if(url.pathname==='/api/search')searches++;
    return route.fulfill({json:{connected:true,status:'connected',disconnected_at:null}});
   });
   await page.goto(base);
   await page.locator('#assistant-panel > summary').click();
   await page.locator('#natural-message').fill('回放已保存的真实模型结果用于界面检查');
   await page.getByRole('button',{name:'发送给小助手'}).click();
   await page.locator('#natural-stay-anchor').waitFor();
   await page.locator('#natural-stay-anchor').selectOption('');
   const ack=page.getByRole('checkbox',{name:/我知道这些附加条件仍需另外核实/});
   if(await ack.count())await ack.check();
   await page.locator('.natural-query').screenshot({path:path.join(dir,`replay-${record.name}.png`)});
   await page.getByRole('button',{name:'核对后填入查询表单'}).click();
   const handoff={place:await page.locator('#place').inputValue(),check_in:await page.locator('#check-in').inputValue()};
   const row={name:record.name,evidence:'captured_real_model_output_offline_replay',mockOperations:posts,sourceSearches:searches,
     handoff,passed:handoff.place===draft.place&&handoff.check_in===draft.check_in,
     noHorizontalOverflow:await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)};
   results.push(row);console.log('REPLAY_CASE',JSON.stringify(row));await page.close();
  }
 }finally{await browser.close();}
 fs.writeFileSync(path.join(dir,'browser-replay.json'),JSON.stringify(results,null,2));
}
main().catch(error=>{console.error('REPLAY_ERROR',error.name);process.exitCode=1;});
