import type {ParallelSearchResponse,QuotesResponse} from './types';

export type WalkEvidence = {metres:number;expiresAt:number};
type Recommendation = {
  key:string;name:string;address:string|null;source:string;tier:'reference'|'pending';reasons:string[];
  verifiedRoom:boolean;walkMetres:number|null;hasReference:boolean;budgetClue:'within'|'unknown'|'over';
};

const maxQuoteAge=5*60*1000;
const maxWalkMetres=1500;
const fresh=(at:string,now:number)=>now-Date.parse(at)>=0&&now-Date.parse(at)<=maxQuoteAge;
export const parallelHotelKey=(requestId:string,hotelId:number)=>requestId+':'+hotelId;

export function conflictingParallelQuote(quote:QuotesResponse) {
  return quote.occupancy_status==='mismatch'||quote.error?.code==='CONTEXT_MISMATCH'
    ||quote.error?.code==='IDENTITY_MISMATCH';
}

export function rememberParallelQuote(previous:QuotesResponse|undefined,incoming:QuotesResponse|null) {
  if(!previous||!conflictingParallelQuote(previous))return incoming??undefined;
  if(!incoming||incoming.request_id!==previous.request_id||incoming.hotel_id!==previous.hotel_id
    ||incoming.source.id!==previous.source.id||!(Date.parse(incoming.queried_at)>=Date.parse(previous.queried_at))){
    return previous;
  }
  if(conflictingParallelQuote(incoming))return incoming;
  const needsOccupancy=previous.occupancy_status==='mismatch'||previous.error?.code==='CONTEXT_MISMATCH';
  return incoming.status!=='unavailable'&&!incoming.error&&incoming.quotes.length>0
    &&(!needsOccupancy||incoming.occupancy_status==='matched') ? incoming : previous;
}

export function recommendParallel(
  batch:ParallelSearchResponse,
  quotes:Readonly<Record<string,QuotesResponse>>,
  walks:Readonly<Record<string,WalkEvidence>>,
  requireWalk:boolean,
  now=Date.now(),
):Recommendation[]{
  const choices:Recommendation[]=[];
  const {budget_fen:budget,rooms,check_in,check_out}=batch.context;
  const useBudgetClue=budget!==null&&rooms===1&&Date.parse(check_out)-Date.parse(check_in)===86400000;
  for(const source of batch.sources){
    const search=source.search;
    if(!search){
      if(source.id==='flyai')source.candidates.forEach((hotel,index)=>{
        choices.push({key:'flyai:'+index,name:hotel.name,address:hotel.address,source:'飞猪',tier:'pending',
          verifiedRoom:false,walkMetres:null,hasReference:false,budgetClue:'unknown',
          reasons:['入住组合与报价口径待核实',...(requireWalk?['步行距离待核实']:[])]});
      });
      continue;
    }
    for(const hotel of search.hotels){
      const key=parallelHotelKey(search.request_id,hotel.id);
      const route=walks[key];
      const validWalk=route&&route.expiresAt>now&&Number.isFinite(route.metres)&&route.metres>=0?route.metres:null;
      if(requireWalk&&validWalk!==null&&validWalk>maxWalkMetres)continue;
      const rooms=quotes[key];
      const bound=rooms?.source.id===source.id&&rooms.request_id===search.request_id&&rooms.hotel_id===hotel.id;
      if(bound&&conflictingParallelQuote(rooms))continue;
      const validRooms=bound&&rooms.status!=='unavailable'&&fresh(rooms.queried_at,now)?rooms:null;
      const assessments=validRooms?.quotes.map(quote=>quote.qualification)??[];
      if(budget!==null&&validRooms?.status==='complete'&&assessments.length&&assessments.every(
        assessment=>assessment?.budget.verdict==='fail'&&assessment.budget.basis!=='average_presumed',
      ))continue;
      const qualified=validRooms?.occupancy_status==='matched'&&assessments.some(item=>item?.tier==='qualified'&&(budget===null||
        (item.budget.verdict==='pass'&&item.budget.basis!=='average_presumed')));
      const price=hotel.reference_price_fen;
      const hasReference=price!==null&&Number.isSafeInteger(price)&&price>=0
        &&fresh(search.queried_at,now)&&hotel.currency==='CNY';
      const budgetClue=useBudgetClue&&hasReference
        ? (price<=budget?'within':'over') : 'unknown';
      const reasons=[
        qualified?'同轮房型参考通过核对':hasReference?'已取得人民币参考价，报价口径和入住条件待核实':'房型与完整费用待核实',
        ...(requireWalk?[validWalk===null?'步行距离待核实':'已核实步行 '+validWalk+' 米']:[]),
      ];
      if(!qualified&&budgetClue!=='unknown'){
        reasons.push(budgetClue==='within'?'展示价未超过预算上限，仅作预算核对线索':'展示价高于预算上限，仅作预算核对线索');
      }
      choices.push({key,name:hotel.name,address:hotel.address,source:search.source.name,
        tier:qualified&&(!requireWalk||validWalk!==null)?'reference':'pending',reasons,
        verifiedRoom:!!qualified,walkMetres:validWalk,hasReference,budgetClue});
    }
  }
  const budgetOrder={within:0,unknown:1,over:2};
  return choices.sort((a,b)=>
    Number(b.tier==='reference')-Number(a.tier==='reference')
    || Number(b.verifiedRoom)-Number(a.verifiedRoom)
    || (requireWalk?(a.walkMetres??maxWalkMetres+1)-(b.walkMetres??maxWalkMetres+1):0)
    || (a.tier==='pending'&&!a.verifiedRoom&&!b.verifiedRoom?budgetOrder[a.budgetClue]-budgetOrder[b.budgetClue]:0)
    || Number(Boolean(b.address))-Number(Boolean(a.address))
    || Number(b.hasReference)-Number(a.hasReference));
}

export function shortlistParallel(rows:readonly Recommendation[]) {
  const tier=rows[0]?.tier;
  const similarLabels=new Set<string>();
  const selected:Recommendation[]=[];
  for(const row of rows){
    if(row.tier!==tier||selected.length>=5)break;
    const address=row.address?.trim();
    if(address){
      const label=JSON.stringify([row.name.trim(),address]);
      if(similarLabels.has(label))continue;
      similarLabels.add(label);
    }
    selected.push(row);
  }
  return selected;
}
