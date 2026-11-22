import {referenceFen} from './CandidateComparison';
import {budgetFen} from './format';
import type {Hotel} from './types';

export type HotelLocalFilters = {keyword:string;maxPrice:string};
export const emptyHotelLocalFilters:HotelLocalFilters={keyword:'',maxPrice:''};

export function parseHotelLocalPrice(maxPrice:string) {
  try { return {fen:budgetFen(maxPrice),invalid:false}; }
  catch { return {fen:null,invalid:true}; }
}

export type HotelLocalSort = 'source' | 'price_low' | 'price_high';
export type HotelLocalOptions = {
  sort: HotelLocalSort;
  keyword: string;
  maxPriceFen: number | null;
};

/** Project already-received hotels only: never request, mutate or infer missing prices. */
export function selectLocalHotels(hotels:readonly Hotel[], options:HotelLocalOptions):Hotel[] {
  const query=options.keyword.trim().toLocaleLowerCase();
  const filtered=hotels.filter(hotel=>{
    if(query && !(hotel.name+' '+(hotel.address??'')).toLocaleLowerCase().includes(query))return false;
    if(options.maxPriceFen!==null){
      const price=referenceFen(hotel);
      if(price===null || price>options.maxPriceFen)return false;
    }
    return true;
  });
  if(options.sort==='source')return filtered;
  return filtered.map((hotel,index)=>({hotel,index,price:referenceFen(hotel)}))
    .sort((left,right)=>{
      // No CNY price, non-CNY price or invalid cents: unknown, always last.
      if(left.price===null)return right.price===null?left.index-right.index:1;
      if(right.price===null)return -1;
      const delta=options.sort==='price_low' ? left.price-right.price : right.price-left.price;
      return delta || left.index-right.index;
    }).map(item=>item.hotel);
}
