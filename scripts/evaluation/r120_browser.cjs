// One public-form submission using the production frontend, with safe evidence.
const {chromium}=require('../../frontend/node_modules/playwright');
const fs=require('fs'),path=require('path');
const [directory,port]=process.argv.slice(2);
const origin='http://127.0.0.1:'+port;
const {query,anchor}=JSON.parse(fs.readFileSync(path.join(directory,'manifest.json'),'utf8'));
const summary={status:'started',page_errors:0,external_blocked:0};
const save=()=>fs.writeFileSync(path.join(directory,'web-summary.json'),JSON.stringify(summary,null,2));
const sourceCounts=batch=>batch.sources.map(source=>{
  const quotes=source.search?.hotels??[];
  const prices=quotes.filter(h=>h.currency==='CNY'&&Number.isInteger(h.reference_price_fen))
    .map(h=>h.reference_price_fen);
  return {id:source.id,status:source.status,reason:source.unavailable_reason,http_used:source.http_used,
    count:source.search?quotes.length:source.candidates.length,priced_count:prices.length,
    lowest_reference_fen:prices.length?Math.min(...prices):null};
});

async function main(){
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--disable-background-networking']});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    page.on('pageerror',()=>summary.page_errors++);
    await page.route('**/*',route=>{
      if(new URL(route.request().url()).origin===origin)return route.continue();
      summary.external_blocked++;return route.abort();
    });
    const response=(pathname,method='POST')=>page.waitForResponse(
      r=>r.url()===origin+pathname&&r.request().method()===method,{timeout:69000});
    async function action(pathname,click){
      const [reply]=await Promise.all([response(pathname),click()]);
      const data=await reply.json();
      if(!reply.ok())throw Error('LOCAL_ROUTE_FAILED');
      return data;
    }
    const [health]=await Promise.all([response('/api/health','GET'),page.goto(origin)]);
    if(!(await health.json()).features?.parallel_search)throw Error('PRODUCTION_PARALLEL_UNAVAILABLE');
    await page.locator('#place').fill(query.place);
    await page.locator('#check-in').fill(query.check_in);
    await page.locator('#check-out').fill(query.check_out);
    await page.locator('#budget').fill(String(query.budget_fen/100));
    const [search]=await Promise.all([
      response('/api/search/parallel'),page.getByRole('button',{name:/查询房价/}).click(),
    ]);
    summary.search_http=search.status();
    const batch=await search.json();
    if(!search.ok()||!Array.isArray(batch.sources))throw Error('PARALLEL_SEARCH_FAILED');
    summary.search_status=batch.status;
    summary.sources=sourceCounts(batch);
    const panel=page.getByRole('region',{name:'三家住宿来源结果'});
    await panel.getByRole('heading',{name:'寻栖自动推荐'}).waitFor();
    summary.visible_sources=await panel.locator('.parallel-source').count();
    summary.labels_rendered=await Promise.all(['道旅','途牛','飞猪'].map(label=>
      panel.getByRole('region',{name:label+'搜索结果'}).isVisible()));
    const recommendations=panel.locator('.parallel-recommendations');
    const links=await recommendations.getByRole('link').evaluateAll(rows=>rows.map(row=>row.getAttribute('href')));
    summary.recommendation_count=links.length;
    summary.pending_basis=(await recommendations.innerText()).includes('暂无已核实满足条件');
    summary.status='search_observed';save();
    await recommendations.screenshot({path:path.join(directory,'recommendations.png')});

    if(anchor){
      // Start the map TTL only after the Tuniu account minute window clears.
      if(summary.sources.find(source=>source.id==='tuniu')?.http_used>0)await page.waitForTimeout(61000);
      const nearby=page.getByRole('region',{name:'目的地周边酒店地图核对'});
      await nearby.getByLabel('具体目的地').fill(anchor.name);
      const resolved=await action('/api/places/resolve',()=>nearby.getByRole('button',{name:/搜索具体地点/}).click());
      const indices=(resolved.options??[]).flatMap((point,index)=>
        ['name','city','address'].every(key=>point[key]===anchor[key])?[index]:[]);
      summary.resolve={status:resolved.status,error:resolved.error?.code??null,exact_matches:indices.length};
      save();
      if(indices.length!==1)throw Error('ANCHOR_IDENTITY_NOT_REPRODUCED');
      await nearby.getByRole('radio').nth(indices[0]).check();
      const destination=await action('/api/search/parallel',()=>nearby.getByRole('button',{name:/按此目的地并行查三家/}).click());
      summary.destination={status:destination.status,sources:sourceCounts(destination)};
      save();
      const local=await action('/api/places/nearby/parallel',()=>nearby.getByRole('button',{name:/查找此地点附近的酒店/}).click());
      const linked=local.recommendation_links??[];
      summary.nearby={status:local.status,error:local.error?.code??null,points:local.hotels.length,linked:linked.length};
      save();
      if(local.status==='unavailable')throw Error('MAP_NEARBY_UNAVAILABLE');
      const point=local.hotels.find(row=>linked.some(link=>link.hotel_option_id===row.id))
        ??local.hotels.find(row=>row.name==='怡佳公寓(北京路步行街公园前地铁站店)'&&row.address.startsWith('中山五路昌兴街26号'));
      if(point){
        const row=nearby.locator('.nearby-hotel-row').filter({has:page.getByText(point.name,{exact:true})})
          .filter({has:page.getByText(point.city+' · '+point.address,{exact:true})});
        const walk=await action('/api/places/walk',()=>row.getByRole('button',{name:'查步行路线'}).click());
        summary.walk={name:point.name,status:walk.status,error:walk.error?.code??null,
          supplier_bound:linked.some(link=>link.hotel_option_id===point.id),
          metres:walk.distance_meters,seconds:walk.duration_seconds};
      }
      summary.status='destination_observed';save();
      return;
    }

    const quoteTarget=batch.sources.flatMap(source=>(source.search?.hotels??[]).map(hotel=>{
      const id='parallel-hotel-'+source.search.request_id+':'+hotel.id;
      return {source:source.id,id,hotel_id:hotel.id,request_id:source.search.request_id};
    }));
    const selected=links.map(link=>quoteTarget.find(item=>'#'+item.id===link)).find(Boolean);
    if(selected){
      const card=page.locator('[id='+JSON.stringify(selected.id)+']');
      const [detail]=await Promise.all([
        response('/api/hotels/'+selected.hotel_id+'/quotes'),
        card.getByRole('button',{name:/查看房型/}).click(),
      ]);
      const result=await detail.json();
      summary.detail={expected_source:selected.source,source:result.source?.id??null,
        bindings_match:result.source?.id===selected.source&&result.hotel_id===selected.hotel_id&&result.request_id===selected.request_id,
        http:detail.status(),status:result.status??null,error:result.error?.code??null,
        quotes:result.quotes?.length??0,occupancy_status:result.occupancy_status??null,
        bookable:result.bookability?.ready??false};
    }
    save();
  }catch(error){
    summary.driver_error=/^[A-Z_]+$/.test(error.message)?error.message:'DRIVER_ERROR';
    process.exitCode=1;
  }finally{
    save();
    await browser.close();
  }
}
main().catch(()=>{summary.driver_error='BROWSER_SETUP_ERROR';save();process.exitCode=1;});
