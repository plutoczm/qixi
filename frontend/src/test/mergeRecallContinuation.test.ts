import {expect,it} from 'vitest';
import {mergeRecallContinuation} from '../mergeRecallContinuation';
import {mapFixture,continuationFixture} from './recall-fixtures';
it('merges new hotels while preserving the first price object and source time',()=>{
 const first=mapFixture(),next=continuationFixture(first,'operation');
 next.hotels.push({...first.hotels[0],reference_price_fen:99900});
 const result=mergeRecallContinuation(first,next,'operation');
 expect(result.hotels).toHaveLength(3);expect(result.hotels[0]).toBe(first.hotels[0]);
 expect(result.hotels[0].reference_price_fen).toBe(18800);
 expect(result.hotel_observations[0]).toEqual(first.hotel_observations[0]);
 expect(first.hotels).toHaveLength(2);expect(result.continuation_delta).toBe(false);
});
it('removes cross-round conflicts from the retained client union',()=>{
 const first=mapFixture(),next=continuationFixture(first,'operation');
 next.removed_hotel_ids=[101];next.retained_total=2;
 next.branches.forEach(b=>{b.hotel_ids=b.hotel_ids.filter(id=>id!==101);});
 next.hotel_observations=next.hotel_observations.filter(o=>o.hotel_id!==101);
 expect(mergeRecallContinuation(first,next,'operation').hotels.map(h=>h.id)).toEqual([102,103]);
});
it.each(['request','revision','context','expiry','old_time'])('rejects changed %s without mutating retained data',kind=>{
 const first=mapFixture(),next=continuationFixture(first,'operation');
 const before=JSON.stringify(first);
 if(kind==='request')next.request_id='other';
 if(kind==='revision')next.revision=4;
 if(kind==='context')next.context.budget_fen=null===first.context.budget_fen?20000:null;
 if(kind==='expiry')next.continuation_expires_at=new Date(Date.now()+3600000).toISOString();
 if(kind==='old_time')next.hotel_observations[0].queried_at=next.queried_at;
 expect(()=>mergeRecallContinuation(first,next,'operation')).toThrow(/不一致/);
 expect(JSON.stringify(first)).toBe(before);
});
it('cannot fabricate an unseen price after a response is lost',()=>{
 const first=mapFixture(),next=continuationFixture(first,'operation');next.hotels=[];
 expect(()=>mergeRecallContinuation(first,next,'operation')).toThrow(/不一致/);
});
