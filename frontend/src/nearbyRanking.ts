import type {Hotel,NearbyGuestReview,NearbyHotelOption,NearbyWalkResponse} from './types';
export type NearbySort='distance'|'price'|'rating'|'balanced';
type Walks=Readonly<Record<string,NearbyWalkResponse>>;
type Row={item:NearbyHotelOption;index:number;price:number|null;review:NearbyGuestReview|null;walk:number|null};

export function nearbyPriceFen(item:NearbyHotelOption,hotels:ReadonlyMap<number,Hotel>):number|null{
  if(item.source_match!=='matched'||item.source_hotel_id===null)return null;
  const h=hotels.get(item.source_hotel_id);
  const price=h?.reference_price_fen;
  return h?.currency==='CNY'&&typeof price==='number'&&Number.isSafeInteger(price)&&price>=0?price:null;
}
export function nearbyPoiRating(item:NearbyHotelOption):number|null{
  const n=item.map_rating;
  return item.rating_status==='map_poi'&&typeof n==='number'&&Number.isFinite(n)&&n>0&&n<=5?n:null;
}
export function nearbyGuestReview(item:NearbyHotelOption):NearbyGuestReview|null{
  const r=item.guest_review;
  if(item.source_match!=='matched'||item.source_hotel_id===null||
     !r||r.authorization_status!=='operator_attested'||r.scale!==5||
     typeof r.score!=='number'||!Number.isFinite(r.score)||r.score<=0||r.score>5||
     !Number.isSafeInteger(r.review_count)||r.review_count<1||
     r.review_count!==item.review_count||!r.source)return null;
  const age=Date.now()-Date.parse(r.observed_at);
  return Number.isFinite(age)&&age>=0&&age<=30*24*3600000&&Date.parse(r.valid_until)>Date.now()?r:null;
}
export function nearbyWalkMetres(item:NearbyHotelOption,walks:Walks,contextId:string,provider:string):number|null{
  const r=walks[item.id];
  if(!r||!contextId||r.context_id!==contextId||r.hotel_option_id!==item.id||
     r.provider!==provider||r.status!=='complete'||r.error!==null)return null;
  const age=Date.now()-Date.parse(r.queried_at);
  if(!Number.isFinite(age)||age<0||age>=120000)return null;
  const metres=r.distance_meters;
  return typeof metres==='number'&&Number.isSafeInteger(metres)&&metres>=0&&metres<=100000?metres:null;
}
function rowsFor(items:readonly NearbyHotelOption[],hotels:ReadonlyMap<number,Hotel>,
                 walks:Walks,contextId:string,provider:string):Row[]{
  return items.map((item,index)=>({item,index,price:nearbyPriceFen(item,hotels),
    review:nearbyGuestReview(item),walk:nearbyWalkMetres(item,walks,contextId,provider)}));
}
function complete(rows:Row[]):Row[]{
  const ready=rows.filter(r=>r.price!==null&&r.review!==null&&r.walk!==null);
  return new Set(ready.map(r=>r.review!.source)).size===1?ready:[];
}
export function comparableNearbyCount(items:readonly NearbyHotelOption[],hotels:ReadonlyMap<number,Hotel>,
                  walks:Walks={},contextId='',provider=''):number{
  return complete(rowsFor(items,hotels,walks,contextId,provider)).length;
}
export function mixedNearbyReviewSources(items:readonly NearbyHotelOption[],hotels:ReadonlyMap<number,Hotel>,
                  walks:Walks={},contextId='',provider=''):boolean{
  const ready=rowsFor(items,hotels,walks,contextId,provider).filter(r=>r.price!==null&&r.review!==null&&r.walk!==null);
  return new Set(ready.map(r=>r.review!.source)).size>1;
}
export function rankNearbyHotels(items:readonly NearbyHotelOption[],hotels:ReadonlyMap<number,Hotel>,
     mode:NearbySort,_radius:number,walks:Walks={},contextId='',provider=''):NearbyHotelOption[]{
  const rows=rowsFor(items,hotels,walks,contextId,provider);
  const ready=complete(rows);
  const ids=new Set(ready.map(r=>r.item.id));
  const prices=ready.map(r=>r.price!);
  const low=prices.length?Math.min(...prices):0,high=prices.length?Math.max(...prices):0;
  const byDistance=(a:Row,b:Row)=>(a.walk===null?1:0)-(b.walk===null?1:0)||
       (a.walk??a.item.straight_line_meters)-(b.walk??b.item.straight_line_meters)||
       a.item.straight_line_meters-b.item.straight_line_meters||a.index-b.index;
  const total=(r:Row):number|null=>{
    if(ready.length<2||!ids.has(r.item.id)||r.price===null||r.walk===null||r.review===null)return null;
    const p=high===low?1:(high-r.price)/(high-low);
    const d=1-Math.min(r.walk,3000)/3000;
    const n=r.review.review_count;
    const adjusted=(n*r.review.score+30*3.5)/(n+30);
    const rating=Math.max(0,Math.min((adjusted-1)/4,1));
    const count=Math.min(Math.log1p(n)/Math.log1p(1000),1);
    return .30*p+.25*d+.30*rating+.15*count;
  };
  const mixed=new Set(rows.filter(r=>r.review!==null).map(r=>r.review!.source)).size>1;
  return rows.sort((a,b)=>{
    if(mode==='distance')return byDistance(a,b);
    if(mode==='balanced'){
      const x=total(a),y=total(b);
      if(x===null||y===null)return x===null?(y===null?byDistance(a,b):1):-1;
      return y-x||byDistance(a,b);
    }
    if(mode==='price'){
      if(a.price===null||b.price===null)return a.price===null?(b.price===null?byDistance(a,b):1):-1;
      return a.price-b.price||byDistance(a,b);
    }
    if(mixed)return byDistance(a,b);
    if(!a.review||!b.review)return !a.review?(!b.review?byDistance(a,b):1):-1;
    return b.review.score-a.review.score||b.review.review_count-a.review.review_count||byDistance(a,b);
  }).map(r=>r.item);
}
