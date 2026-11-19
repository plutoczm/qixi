import type {TravelPlacesResponse,TravelRouteResponse} from '../types';
export function placesFixture(provider:'amap'|'tencent'|'didi'='amap',matched=true):TravelPlacesResponse {
 return {request_id:'ui-test-search',hotel_id:101,hotel_name:'界面测试住宿',provider,status:'complete',context_id:'travel-test-context',queried_at:new Date().toISOString(),expires_in_seconds:120,data_mode:'test',
 hotels:[{id:'h1',name:'界面测试住宿',address:'测试地址',city:'广州市',name_match:true,address_match:matched?true:null,city_match:true}],
 restaurants:[{id:'r1',name:'海底捞甲店',address:'测试地址甲',city:'广州市',name_match:null,address_match:null,city_match:null},{id:'r2',name:'海底捞乙店',address:'测试地址乙',city:'广州市',name_match:null,address_match:null,city_match:null}],
 matching:{status:matched?'matched':'unverified',selected_id:matched?'h1':null,basis:matched?'exact':null,explanation:matched?'名称、地址、城市均一致且唯一命中。':'缺少可唯一确认的地址，请核对。'},coverage_note:'测试仅返回2家门店，不代表全城完整。',warnings:[],error:null};
}
export function routeFixture(provider:'amap'|'tencent'|'didi'='amap'):TravelRouteResponse {
 return {request_id:'ui-test-search',hotel_id:101,provider,status:'complete',queried_at:new Date().toISOString(),data_mode:'test',hotel_name:'界面测试住宿',restaurant_name:'海底捞甲店',hotel_match_basis:'exact_fields',return_trip:null,
 return_walk:provider==='didi'?null:{origin_name:'海底捞甲店',destination_name:'界面测试住宿',distance_meters:800,duration_seconds:601,point_precision:'poi_point',night_access_verified:false},
 return_drive:null,
 walk_limit_status:provider==='didi'?'unverified':'within_1500',
 drive_limit_status:'unverified',
 transport_status:provider==='didi'?'unknown_or_partial':'pass_walk_or_drive',
 subway:provider==='didi'?null:{name:'测试地铁站B口',precision:'named_entrance_poi',walk:{origin_name:'界面测试住宿',destination_name:'测试地铁站B口',distance_meters:260,duration_seconds:240,point_precision:'poi_point',night_access_verified:false}},
 fares:provider==='didi'?[{name:'快车',lower_fen:1200,upper_fen:1800,capacity_verified:false,discount_eligibility_verified:false}]:[],
 current_reference_only:true,future_departure_verified:false,source_expiry_provided:false,opening_today:null,opening_week:null,
 warnings:['当前参考，不保证夜间入口、完整费用或车型容量。'],error:null};
}
