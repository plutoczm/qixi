import {describe,it,expect} from 'vitest';
import {comparableNearbyCount,nearbyPoiRating,nearbyPriceFen,rankNearbyHotels} from '../nearbyRanking';
import {searchFixture} from './fixtures';
import type {NearbyHotelOption} from '../types';

const base:NearbyHotelOption={
  id:'a',name:'A',address:'北京市',city:'北京市',straight_line_meters:600,
  source_hotel_id:null,source_match:'unverified',walking_distance_meters:null,
  walking_duration_seconds:null,rating_status:'unavailable',map_rating:null,
  review_count:null,stayed_count:null,guest_review:null,
};
const pricedHotel=searchFixture().hotels[0];
const hotels=new Map([[pricedHotel.id,pricedHotel]]);

describe('evidence-aware local nearby sorting',()=>{
  it('never binds price from map-only, ambiguous or mismatched IDs',()=>{
    expect(nearbyPriceFen(base,hotels)).toBeNull();
    expect(nearbyPriceFen({...base,source_match:'matched',source_hotel_id:999},hotels)).toBeNull();
    expect(nearbyPriceFen({...base,source_match:'ambiguous',source_hotel_id:pricedHotel.id},hotels)).toBeNull();
  });
  it('refuses fabricated or malformed POI scores',()=>{
    expect(nearbyPoiRating({...base,map_rating:5})).toBeNull();
    expect(nearbyPoiRating({...base,rating_status:'map_poi',map_rating:0})).toBeNull();
    expect(nearbyPoiRating({...base,rating_status:'map_poi',map_rating:5.1})).toBeNull();
  });
  it('distance-only is deterministic and leaves source data unchanged',()=>{
    const input=[{...base,id:'far',straight_line_meters:800},{...base,id:'close',straight_line_meters:200}];
    expect(rankNearbyHotels(input,hotels,'distance',2000).map(x=>x.id)).toEqual(['close','far']);
    expect(input[0].id).toBe('far');
  });
  it('price sorts observed values first but map scores do not qualify guest ranking',()=>{
    const known={...base,id:'known',rating_status:'map_poi' as const,map_rating:4.2,
      source_hotel_id:pricedHotel.id,source_match:'matched' as const,straight_line_meters:900};
    const missing={...base,id:'missing',straight_line_meters:100};
    expect(nearbyPriceFen(known,hotels)).not.toBeNull();
    expect(comparableNearbyCount([missing,known],hotels)).toBe(0);
    for(const mode of ['price'] as const){
      expect(rankNearbyHotels([missing,known],hotels,mode,2000)[0].id).toBe('known');
    }
  });
  it('map scores never override distance in guest-score mode without guest data',()=>{
    const h1={...base,id:'low',rating_status:'map_poi' as const,map_rating:3.2,
      source_hotel_id:pricedHotel.id,source_match:'matched' as const,straight_line_meters:1200};
    const h2={...h1,id:'high',map_rating:4.9,straight_line_meters:300};
    expect(rankNearbyHotels([h1,h2],hotels,'balanced',2000).map(x=>x.id)).toEqual(['high','low']);
    expect(rankNearbyHotels([h1,h2],hotels,'rating',2000).map(x=>x.id)).toEqual(['high','low']);
  });
  it('balanced falls back to distance if complete evidence is absent',()=>{
    const near={...base,id:'near',straight_line_meters:100};
    const far={...base,id:'far',straight_line_meters:1200};
    expect(comparableNearbyCount([far,near],hotels)).toBe(0);
    expect(rankNearbyHotels([far,near],hotels,'balanced',1000).map(x=>x.id)).toEqual(['near','far']);
  });
});
