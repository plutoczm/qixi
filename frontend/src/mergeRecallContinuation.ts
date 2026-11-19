import type {RecallResponse} from './types';

/** Materialize a delta without replacing an old reference price or its source time. */
export function mergeRecallContinuation(previous:RecallResponse,next:RecallResponse,operation:string):RecallResponse {
 const invalid=()=>Object.assign(new Error('续查结果与已保留条件不一致，请重新查询；未覆盖旧参考价。'),{code:'CONTINUATION_INVALID_DELTA'});
 if(previous.request_id!==next.request_id||next.operation_id!==operation||
    next.strategy!=='map_hotels'||previous.strategy!=='map_hotels'||
    next.map_provider!==previous.map_provider||next.restaurant_keyword!==previous.restaurant_keyword||
    next.revision!==previous.revision+1||!next.continuation_delta||
    next.plan_queried_at!==previous.plan_queried_at||
    next.continuation_expires_at!==previous.continuation_expires_at||
    JSON.stringify(next.context)!==JSON.stringify(previous.context))throw invalid();
 const attempted=new Set(next.attempted_hotel_names);
 if(attempted.size!==next.attempted_hotel_names.length||
    attempted.size!==next.recall_coverage.total_hotel_lookups||
    previous.attempted_hotel_names.some(name=>!attempted.has(name)))throw invalid();
 const allowed=new Set(next.branches.flatMap(b=>b.hotel_ids));
 const removed=new Set(next.removed_hotel_ids);
 if(allowed.size!==next.retained_total||[...removed].some(id=>allowed.has(id)))throw invalid();
 const merged=new Map(previous.hotels.filter(h=>allowed.has(h.id)&&!removed.has(h.id)).map(h=>[h.id,h]));
 for(const hotel of next.hotels){
  if(!allowed.has(hotel.id))throw invalid();
  const old=merged.get(hotel.id);
  if(old&&(old.name!==hotel.name||old.address!==hotel.address))throw invalid();
  if(!old)merged.set(hotel.id,hotel);
 }
 if(merged.size!==next.retained_total)throw invalid();
 const observations=new Map(next.hotel_observations.map(o=>[o.hotel_id,o.queried_at]));
 if(observations.size!==next.hotel_observations.length)throw invalid();
 for(const id of merged.keys()){
  const stamp=observations.get(id);
  if(!stamp||!Number.isFinite(Date.parse(stamp)))throw invalid();
  const old=previous.hotel_observations.find(o=>o.hotel_id===id);
  if(old&&old.queried_at!==stamp)throw invalid();
 }
 return {...next,hotels:[...merged.values()],continuation_delta:false,
  coverage:{...next.coverage,returned_count:merged.size}};
}
