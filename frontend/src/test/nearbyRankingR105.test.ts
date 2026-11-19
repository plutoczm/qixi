import {describe,it,expect} from 'vitest';
import {comparableNearbyCount,mixedNearbyReviewSources,nearbyGuestReview,nearbyPriceFen,nearbyPoiRating,nearbyWalkMetres,rankNearbyHotels} from '../nearbyRanking';
import {searchFixture} from './fixtures';
import type {NearbyHotelOption,NearbyWalkResponse} from '../types';
const base:NearbyHotelOption={
 id:'a',name:'A',address:'北京市',city:'北京市',straight_line_meters:600,
 source_hotel_id:null,source_match:'unverified',walking_distance_meters:null,
 walking_duration_seconds:null,rating_status:'unavailable',map_rating:null,
 review_count:null,stayed_count:null,guest_review:null,
};
const h=searchFixture().hotels[0],hotels=new Map([[h.id,h]]);
const rated=(id:string,score:number,n:number,source='同一家点评源'):NearbyHotelOption=>({
 ...base,id,source_match:'matched',source_hotel_id:h.id,review_count:n,
 guest_review:{score,scale:5,review_count:n,source,
 observed_at:new Date(Date.now()-1000).toISOString(),valid_until:new Date(Date.now()+60000).toISOString(),authorization_status:'operator_attested'},
});
const walk=(id:string,m:number):NearbyWalkResponse=>({
 context_id:'ctx',hotel_option_id:id,provider:'amap',queried_at:new Date().toISOString(),
 data_mode:'test',status:'complete',distance_meters:m,duration_seconds:300,error:null,
});
const walks={a:walk('a',900),b:walk('b',400)};
describe('four-factor guest-review ranking',()=>{
 it('map ratings never stand in for guest evidence',()=>{
  const fake={...base,rating_status:'map_poi' as const,map_rating:5};
  expect(nearbyPoiRating(fake)).toBe(5);
  expect(nearbyGuestReview(fake)).toBeNull();
  expect(comparableNearbyCount([fake],hotels,walks,'ctx','amap')).toBe(0);
 });
 it('cannot use ambiguous price or stale/mislabelled guest scores',()=>{
  expect(nearbyPriceFen({...base,source_match:'ambiguous',source_hotel_id:h.id},hotels)).toBeNull();
  const a=rated('a',4.9,150);
  expect(nearbyGuestReview({...a,review_count:999})).toBeNull();
  expect(nearbyGuestReview({...a,guest_review:{...a.guest_review!,observed_at:'2010-01-01T00:00:00Z'}})).toBeNull();
 });
 it('requires same-context real walking evidence, price, rating and count',()=>{
  const a=rated('a',4.1,110),b=rated('b',4.9,1000);
  expect(comparableNearbyCount([a,b],hotels,walks,'ctx','amap')).toBe(2);
  expect(rankNearbyHotels([a,b],hotels,'balanced',2000,walks,'ctx','amap').map(r=>r.id)).toEqual(['b','a']);
  expect(comparableNearbyCount([a,b],hotels,walks,'wrong','amap')).toBe(0);
  expect(comparableNearbyCount([a,b],hotels,{},'ctx','amap')).toBe(0);
 });
 it('rejects cross-platform combined ranking',()=>{
  const a=rated('a',4.1,110),b=rated('b',4.9,1000,'另一个点评平台');
  expect(mixedNearbyReviewSources([a,b],hotels,walks,'ctx','amap')).toBe(true);
  expect(comparableNearbyCount([a,b],hotels,walks,'ctx','amap')).toBe(0);
 });
 it('keeps map straightline separate from verified walking',()=>{
  const a={...base,id:'a',straight_line_meters:50};
  const b={...base,id:'b',straight_line_meters:900};
  expect(rankNearbyHotels([b,a],hotels,'distance',2000).map(r=>r.id)).toEqual(['a','b']);
  expect(rankNearbyHotels([b,a],hotels,'distance',2000,{b:walk('b',250)},'ctx','amap').map(r=>r.id)).toEqual(['b','a']);
 });
 it('does not rank on expired authorization or expired walking evidence',()=>{
  const a=rated('a',4.8,120);
  expect(nearbyGuestReview({...a,guest_review:{...a.guest_review!,valid_until:new Date(Date.now()-1).toISOString()}})).toBeNull();
  const stale={...walk('a',800),queried_at:new Date(Date.now()-121000).toISOString()};
  expect(nearbyWalkMetres(a,{a:stale},'ctx','amap')).toBeNull();
  expect(nearbyWalkMetres(a,{a:{...walk('a',800),provider:'tencent'}},'ctx','amap')).toBeNull();
 });
 it('shrinks a tiny perfect sample below a strong large sample',()=>{
  const a=rated('a',5,1),b=rated('b',4.6,500);
  const equalWalks={a:walk('a',500),b:walk('b',500)};
  expect(rankNearbyHotels([a,b],hotels,'balanced',2000,equalWalks,'ctx','amap').map(r=>r.id)).toEqual(['b','a']);
 });
});
