import {describe,expect,it} from 'vitest';
import {selectLocalHotels} from '../hotelLocalView';
import {searchFixture} from './fixtures';
import type {Hotel} from '../types';

const example=searchFixture().hotels[0];
function hotel(id:number,name:string,price:number|null,currency:string|null='CNY',address:string|null=null):Hotel {
  return {...example,id,name,reference_price_fen:price,currency,address};
}
const mixed:Hotel[]=[
  hotel(1,'春日木屋',29000,'CNY','成都锦江区'),
  hotel(2,'晚风旅舍',null,'CNY','成都武侯区'),
  hotel(3,'松间酒店',11900,'CNY','重庆江北区'),
  hotel(4,'庭院',9000,'USD','成都锦江区'),
  hotel(5,'小院',11900,'CNY','成都春熙路'),
  hotel(6,'晨光',-99,'CNY','成都二环'),
  hotel(7,'河畔',NaN,'CNY','成都一环'),
];

describe('local hotel view, never a supplier query',()=>{
  it('keeps original supplier order and does not modify the original array',()=>{
    const original=mixed.map(x=>x.id);
    const result=selectLocalHotels(mixed,{sort:'source',keyword:'',maxPriceFen:null});
    expect(result.map(x=>x.id)).toEqual(original);
    expect(result).not.toBe(mixed);
    expect(mixed.map(x=>x.id)).toEqual(original);
  });
  it('sorts legal CNY references, unknown and foreign values last in either direction, and preserves equal-price order',()=>{
    expect(selectLocalHotels(mixed,{sort:'price_low',keyword:'',maxPriceFen:null}).map(x=>x.id))
      .toEqual([3,5,1,2,4,6,7]);
    expect(selectLocalHotels(mixed,{sort:'price_high',keyword:'',maxPriceFen:null}).map(x=>x.id))
      .toEqual([1,3,5,2,4,6,7]);
  });
  it('filters hotel name or address keywords after trimming and case folding',()=>{
    const hotels=[hotel(1,'Sunny STAY',16000,'CNY',null),hotel(2,'春日木屋',17000,'CNY',' 成都锦江区 '),hotel(3,'成都河岸',19000,'CNY','其他')];
    expect(selectLocalHotels(hotels,{sort:'source',keyword:' sunny ',maxPriceFen:null}).map(x=>x.id)).toEqual([1]);
    expect(selectLocalHotels(hotels,{sort:'source',keyword:' 锦江 ',maxPriceFen:null}).map(x=>x.id)).toEqual([2]);
    expect(selectLocalHotels(hotels,{sort:'source',keyword:'不存在',maxPriceFen:null})).toEqual([]);
  });
  it('applies an inclusive CNY reference ceiling without treating unknown or USD as free',()=>{
    expect(selectLocalHotels(mixed,{sort:'price_high',keyword:'',maxPriceFen:11900}).map(x=>x.id)).toEqual([3,5]);
    expect(selectLocalHotels(mixed,{sort:'source',keyword:'成都',maxPriceFen:0})).toEqual([]);
    expect(selectLocalHotels(mixed,{sort:'source',keyword:'',maxPriceFen:29000}).map(x=>x.id)).toEqual([1,3,5]);
  });
});
