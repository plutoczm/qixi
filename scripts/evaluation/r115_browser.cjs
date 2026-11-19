// R115 real product UI. Human identity decisions are never synthesized.
const {chromium}=require('../../frontend/node_modules/playwright');
const fs=require('fs'),path=require('path');
const {execFileSync}=require('child_process');
const dir=path.resolve(process.argv[2]),base='http://127.0.0.1:8018/';
const manifest=JSON.parse(fs.readFileSync(path.join(dir,'manifest.json'),'utf8'));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const write=(name,value)=>fs.writeFileSync(path.join(dir,name),JSON.stringify(value,null,2));
const log=(kind,value)=>console.log(kind,JSON.stringify(value));
const meteredCounts=()=>{const c=JSON.parse(execFileSync('D:/Anaconda3/envs/xunqi/python.exe',[
 path.join(__dirname,'ledger.py'),'summary',path.join(dir,'budget.sqlite')],{encoding:'utf8'}));
 return {rollinggo:c.rollinggo.attempted,amap:c.amap.attempted};
};
const safeHotel=h=>({name:h.name,address:h.address,reference_price_fen:h.reference_price_fen,currency:h.currency});
async function decision(name,deadline){
 while(Date.now()<deadline){const file=path.join(dir,name);if(fs.existsSync(file))return JSON.parse(fs.readFileSync(file,'utf8'));await sleep(200);}
 throw Error('HUMAN_CONFIRMATION_EXPIRED');
}
async function main(){
 fs.writeFileSync(path.join(dir,'browser.started'),'started',{flag:'wx'});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const summary={status:'started',external_blocked:0,page_errors:0,requests:[]};
 const page=await browser.newPage({viewport:{width:1440,height:1080}});
 page.on('pageerror',()=>summary.page_errors++);
 const wait=part=>page.waitForResponse(r=>r.request().method()==='POST'&&r.url()===base+part,{timeout:70000});
 async function action(part,click){
  const safePath=part.replace(/^api\/hotels\/\d+\/quotes$/,'api/hotels/:id/quotes');
  summary.pending_action=safePath;
  const [r]=await Promise.all([wait(part),click()]);
  const data=await r.json();
  summary.requests.push({path:safePath,status:r.status(),business_status:data.status,error:data.error?.code??null});
  delete summary.pending_action;
  write('web-summary.json',summary);
  return data;
 }
 try{
  await page.route('**/*',route=>{if(!route.request().url().startsWith(base)){summary.external_blocked++;return route.abort();}return route.continue();});
  if(manifest.recovery){
   await page.route(base+'api/search',async route=>{
    const input=route.request().postDataJSON();
    if(input.poi_name||summary.bootstrap_fixture)return route.fallback();
    summary.bootstrap_fixture=true;
    return route.fulfill({json:{request_id:'r115-recovery-bootstrap',status:'complete',context:input,
      queried_at:new Date().toISOString(),source:{id:'rollinggo',name:'道旅'},data_mode:'test',hotels:[],
      coverage:{limited:false},warnings:[],error:null,current_page:1,next_page:null}});
   });
  }
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.locator('#place').fill('广州');
  await page.locator('#check-in').fill('2026-11-23');await page.locator('#check-out').fill('2026-11-24');
  await page.locator('#budget').fill('300');
  const initial=await action('api/search',()=>page.getByRole('button',{name:/查询房价/}).click());
  if(initial.error||initial.status==='unavailable')throw Error('INITIAL_SEARCH_FAILED');
  summary.initial_count=initial.hotels.length;
  const panel=page.getByRole('region',{name:'目的地周边酒店地图核对'});
  await panel.getByLabel('具体目的地').fill(manifest.keyword);
  const started=Date.now();
  const resolved=await action('api/places/resolve',()=>panel.getByRole('button',{name:/搜索具体地点/}).click());
  if(resolved.error||!resolved.options.length)throw Error('ANCHOR_NOT_RESOLVED');
  const deadline=started+resolved.expires_in_seconds*1000;
  summary.anchor_options=resolved.options.map((p,index)=>({index,name:p.name,city:p.city,address:p.address}));
  summary.status='awaiting_anchor';write('web-summary.json',summary);log('ANCHOR_CANDIDATES',summary.anchor_options);
  const anchorReply=await decision('anchor-decision.json',deadline);
  const matches=resolved.options.filter(p=>p.name===anchorReply.name&&p.address===anchorReply.address&&p.city===anchorReply.city);
  if(matches.length!==1||anchorReply.confirm!==true)throw Error('ANCHOR_NOT_CONFIRMED');
  const [chosen]=matches;
  await panel.getByRole('radio').nth(resolved.options.indexOf(chosen)).check();summary.anchor_confirmed_by_user=true;
  const source=await action('api/search',()=>panel.getByRole('button',{name:/按此目的地查住宿来源/}).click());
  if(source.error||source.status==='unavailable')throw Error('DESTINATION_SEARCH_FAILED');
  summary.supplier_candidates=source.hotels.map(safeHotel);
  log('SUPPLIER_CANDIDATES',summary.supplier_candidates);
  const nearby=await action('api/places/nearby',()=>panel.getByRole('button',{name:/查找此地点附近的酒店/}).click());
  if(nearby.error)throw Error('NEARBY_FAILED');
  summary.map_candidates=nearby.hotels.map(h=>({name:h.name,address:h.address,city:h.city,straight_line_meters:h.straight_line_meters,source_match:h.source_match}));
  log('MAP_CANDIDATES',summary.map_candidates);
  let eligible=[];
  summary.before_local_status=meteredCounts();
  const checked=nearby.hotels.filter(h=>!manifest.hotel_target||h.name===manifest.hotel_target);
  summary.target={name:manifest.hotel_target??null,source_count:source.hotels.filter(h=>h.name===manifest.hotel_target).length,
   map_states:checked.map(h=>h.source_match)};
  for(const point of checked.filter(h=>h.source_match==='unverified')){
   if(Date.now()>=deadline)throw Error('CONTEXT_EXPIRED');
   const row=panel.locator('.nearby-hotel-row').filter({has:page.getByText(point.name,{exact:true})}).filter({has:page.getByText(`${point.city} · ${point.address}`,{exact:true})});
   const local=row.locator('.nearby-manual-identity');
   const state=await action('api/places/identity/status',()=>local.getByRole('button',{name:/人工核对同店/}).click({timeout:8000}));
   summary.identity_state={status:state.status,reason:state.reason,eligible_count:state.eligible_pairs?.length};
   if(state.error)throw Error('IDENTITY_STATUS_FAILED');
   for(const pair of state.eligible_pairs.filter(p=>p.hotel_option_id===point.id))eligible.push({pair,point,local,row});
  }
  summary.manual_pairs=eligible.map(({pair})=>({map_name:pair.map_name,map_address:pair.map_address,map_city:pair.map_city,
   source_name:pair.source_name,source_address:pair.source_address,source_city_status:pair.source_city_status,source_observed_at:pair.source_observed_at}));
  summary.after_local_status=meteredCounts();
  log('MANUAL_CONFIRMATION_PAIRS',summary.manual_pairs);
  if(!eligible.length){summary.status='stopped_no_eligible_manual_pair';return;}
  summary.status='awaiting_identity';summary.decision_deadline=new Date(deadline).toISOString();write('web-summary.json',summary);
  const reply=await decision('identity-decision.json',deadline);
  const target=eligible.find(({pair})=>pair.map_name===reply.map_name&&pair.map_address===reply.map_address&&pair.source_name===reply.source_name&&pair.source_address===reply.source_address);
  if(!target||reply.confirm!==true)throw Error('IDENTITY_NOT_CONFIRMED');
  const {pair,point,local,row}=target;
  await local.getByRole('combobox',{name:'本轮住宿来源酒店'}).selectOption(String(pair.source_hotel_id));
  await local.getByRole('checkbox',{name:'我已核对两侧名称、地址和城市，确认是同一家酒店'}).check();
  const confirmation=await action('api/places/identity/confirm',()=>local.getByRole('button',{name:'确认同店'}).click());
  if(confirmation.error)throw Error('IDENTITY_CONFIRM_FAILED');
  summary.identity_confirmed_by_user=true;
  await local.screenshot({path:path.join(dir,'manual-confirmed.png')});
  // User authorized the real acceptance flow; revoke/reconfirm retain that exact pair.
  const revocation=await action('api/places/identity/revoke',()=>local.getByRole('button',{name:'撤销本次同店确认'}).click());
  if(revocation.error)throw Error('IDENTITY_REVOKE_FAILED');
  await local.getByText('你已确认同店 · 仅本轮有效').waitFor({state:'hidden'});
  summary.revoke_hid_price=await local.getByText(/道旅原搜索参考价/).count()===0;
  await local.getByRole('combobox').selectOption(String(pair.source_hotel_id));await local.getByRole('checkbox').check();
  const reconfirm=await action('api/places/identity/confirm',()=>local.getByRole('button',{name:'确认同店'}).click());
  if(reconfirm.error)throw Error('IDENTITY_RECONFIRM_FAILED');
  summary.after_local_confirmation=meteredCounts();
  const detail=await action(`api/hotels/${pair.source_hotel_id}/quotes`,()=>local.getByRole('button',{name:/查看这家酒店房型/}).click());
  summary.details={status:detail.status,error:detail.error?.code??null,count:detail.quotes?.length??0,occupancy_status:detail.occupancy_status,booking_status:detail.booking_status,bookable:detail.bookability?.ready??false};
  if(detail.error)throw Error('DETAIL_FAILED');
  const route=await action('api/places/walk',()=>row.getByRole('button',{name:'查步行路线'}).click({timeout:8000}));
  summary.walk={status:route.status,error:route.error?.code??null,distance_meters:route.distance_meters,duration_seconds:route.duration_seconds};
  summary.no_horizontal_overflow=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1);
  await panel.screenshot({path:path.join(dir,'live-panel.png')});
  summary.status=route.error?'stopped_walk_failed':'completed_reference_chain';
 }catch(error){summary.status='stopped';summary.reason=error.message?.match(/^[A-Z_]+$/)?error.message:'BROWSER_DRIVER_ERROR';}
 finally{write('web-summary.json',summary);log('FINAL_STATUS',{status:summary.status,reason:summary.reason,details:summary.details,walk:summary.walk});await browser.close();}
}
main().catch(()=>{console.error('BROWSER_SETUP_ERROR');process.exitCode=1;});
