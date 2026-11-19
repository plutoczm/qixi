import {describe,expect,it} from 'vitest';
import {recommendParallel,rememberParallelQuote,shortlistParallel} from '../parallelRecommendations';
import type {Hotel,ParallelSearchResponse,QuotesResponse,SearchResponse} from '../types';
import {quotesFixture} from './fixtures';

const now=Date.parse('2026-11-18T02:00:00Z');
const hotel={id:1,name:'同名住宿',address:'越秀区中山五路4号',reference_price_fen:12000,currency:'CNY'} as Hotel;
const query={budget_fen:15000};
function batch():ParallelSearchResponse {
  const search={request_id:'a',hotels:[hotel],context:query,queried_at:new Date(now).toISOString(),data_mode:'live',
    source:{id:'rollinggo',name:'道旅'}} as SearchResponse;
  const tuniu={...search,request_id:'b',source:{id:'tuniu',name:'途牛'}} as SearchResponse;
  return {operation_id:'op',status:'complete',context:search.context,sources:[
    {id:'rollinggo',status:'complete',search,candidates:[],http_used:4,observed_at:search.queried_at,unavailable_reason:null},
    {id:'tuniu',status:'complete',search:tuniu,candidates:[],http_used:4,observed_at:search.queried_at,unavailable_reason:null},
    {id:'flyai',status:'complete',search:null,candidates:[],http_used:1,observed_at:search.queried_at,unavailable_reason:null},
  ]};
}

describe('三源自动推荐',()=>{
  it('相同数字 ID 的两家来源独立，步行过限只排除有对应证据的候选',()=>{
    const result=recommendParallel(batch(),{},{
      'a:1':{metres:1501,expiresAt:now+1000},
      'b:1':{metres:120,expiresAt:now+1000},
    },true,now);
    expect(result.map(row=>[row.key,row.tier])).toEqual([['b:1','pending']]);
    expect(result[0].reasons).toContain('已核实步行 120 米');
  });
  it('有效证据过期后重新给出步行待核实，不采用旧路线',()=>{
    const result=recommendParallel(batch(),{},{
      'a:1':{metres:1501,expiresAt:now},
    },true,now);
    expect(result.map(row=>row.key)).toEqual(['a:1','b:1']);
    expect(result.every(row=>row.reasons.includes('步行距离待核实'))).toBe(true);
  });
  it('只采纳同源同轮房型，按真实返回的预算判定升降级',()=>{
    const room={status:'complete',request_id:'b',hotel_id:1,source:{id:'tuniu',name:'途牛'},
      queried_at:new Date(now).toISOString(), occupancy_status:'matched',
      quotes:[{qualification:{tier:'qualified',budget:{verdict:'pass',basis:'nightly_verified'}}}],
    } as QuotesResponse;
    const result=recommendParallel(batch(),{'a:1':room,'b:1':room},{},false,now);
    expect(result.map(row=>[row.key,row.tier])).toEqual([['b:1','reference'],['a:1','pending']]);
    const stale=recommendParallel(batch(),{'b:1':room},{},false,now+5*60*1000+1);
    expect(stale.every(row=>row.tier==='pending')).toBe(true);
  });
  it('不把未声明口径的跨晚多房参考价作为预算达标证据',()=>{
    const original=batch();
    original.context={...original.context,rooms:2,check_in:'2026-11-23',check_out:'2026-11-25'};
    const result=recommendParallel(original,{},{},false,now);
    expect(result.map(row=>row.tier)).toEqual(['pending','pending']);
    expect(result[0].reasons.join(' ')).toContain('报价口径和入住条件待核实');
  });
  it('明确的同店冲突移出推荐，人数未核实的房型不能晋级',()=>{
    const common={hotel_id:1,queried_at:new Date(now).toISOString(),occupancy_status:'unverified',quotes:[
      {qualification:{tier:'qualified',budget:{verdict:'pass',basis:'nightly_verified'}}},
    ]} as Partial<QuotesResponse>;
    const result=recommendParallel(batch(),{
      'a:1':{...common,status:'unavailable',request_id:'a',source:{id:'rollinggo',name:'道旅'},
        error:{code:'CONTEXT_MISMATCH',message:'入住条件不一致'}} as QuotesResponse,
      'b:1':{...common,status:'complete',request_id:'b',source:{id:'tuniu',name:'途牛'}} as QuotesResponse,
    },{},false,now);
    expect(result.map(row=>[row.key,row.tier])).toEqual([['b:1','pending']]);
  });

  it('keeps a known same-session identity conflict out after the quote expires and during a failed refresh',()=>{
    const previous:QuotesResponse={...quotesFixture(),request_id:'a',hotel_id:1,source:{id:'rollinggo',name:'道旅'},
      queried_at:new Date(now).toISOString(),status:'unavailable',
      error:{code:'IDENTITY_MISMATCH',message:'住宿身份不同'},quotes:[]};
    const timeout={...previous,error:{code:'TIMEOUT',message:'暂未取得结果'}};
    expect(rememberParallelQuote(previous,null)).toBe(previous);
    expect(rememberParallelQuote(previous,timeout)).toBe(previous);
    const late=now+300001;
    expect(recommendParallel(batch(),{'a:1':previous},{},false,late).map(item=>item.key)).toEqual(['b:1']);
    const replacement:QuotesResponse={...previous,status:'complete',error:null,quotes:quotesFixture().quotes};
    expect(rememberParallelQuote(previous,replacement)).toBe(replacement);
  });
  it('prioritizes an affordable display clue for one night and one room without promoting it',()=>{
    const input=batch();
    input.context={...input.context,check_in:'2026-12-09',check_out:'2026-12-10',rooms:1,budget_fen:20000};
    input.sources[0].search={...input.sources[0].search!,hotels:Array.from({length:5},(_,index)=>
      ({...hotel,id:index+1,name:'高价酒店 '+index,reference_price_fen:90000}))};
    const ranked=recommendParallel(input,{},{},false,now);
    expect(ranked[0]).toMatchObject({key:'b:1',tier:'pending'});
    expect(ranked[0].reasons.join(' ')).toContain('预算核对线索');
    input.context.rooms=2;
    expect(recommendParallel(input,{},{},false,now)[0].key).toBe('a:1');
  });
  it('prioritizes shorter verified routes when comparing results for the same destination',()=>{
    const ranked=recommendParallel(batch(),{},{
      'a:1':{metres:1499,expiresAt:now+1000},'b:1':{metres:120,expiresAt:now+1000},
    },true,now);
    expect(ranked.map(row=>row.key)).toEqual(['b:1','a:1']);
  });
  it('keeps all supplier candidates while giving indistinguishable labels one shortlist slot',()=>{
    const input=batch();
    input.sources[1].search!.hotels=[hotel,{...hotel,id:2,address:'越秀区中山五路44号'}];
    const ranked=recommendParallel(input,{},{},false,now);
    expect(ranked).toHaveLength(3);
    expect(shortlistParallel(ranked).map(row=>row.key)).toEqual(['a:1','b:2']);
  });
});
