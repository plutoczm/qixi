import {fixture} from './recall-fixtures';
import type {RecallResponse,BranchWalkBatchRequest,BranchWalkBatchResponse,BranchWalkPair} from '../types';
export function walkRecallFixture():RecallResponse {
 const data=fixture();const first=data.hotels[0];
 data.hotels=Array.from({length:6},(_,i)=>({...first,id:101+i,name:'测试住宿'+(i+1),address:'测试街'+(i+1)+'号'}));
 data.branches=data.branches.map((b,i)=>({...b,status:'complete',reason:null,hotel_ids:[101+i,104+i],returned_count:2}));
 data.walk_check_available=true;data.retained_total=data.hotels.length;data.hotel_observations=data.hotels.map(h=>({hotel_id:h.id,queried_at:data.queried_at}));
 return data;
}
export function walkPairFixture(request:BranchWalkBatchRequest,index=0,automatic=false):BranchWalkPair {
 const hotel=101+request.offset+index,branchIndex=index+1;
 const hid='h'+hotel,rid='r'+hotel;
 const original='测试住宿'+(request.offset+index+1),restaurant='测试餐厅'+branchIndex+'店';
 const m={status:automatic?'matched':'unverified',selected_id:null,basis:automatic?'exact':null,explanation:automatic?'名称、地址、城市唯一匹配。':'字段不同，请核对。'} as const;
 return {recall_hotel:null,source_http_requests:6,context_id:'ctx-'+request.operation_id.slice(0,8)+'-'+index,branch_id:'b'+branchIndex,hotel_id:hotel,
 original_hotel_name:original,original_hotel_address:'测试街'+(request.offset+index+1)+'号',
 original_restaurant:{id:'b'+branchIndex,name:restaurant,address:'测试路'+branchIndex+'号',city:'广州',name_match:null,address_match:null,city_match:null},
 status:automatic?'route_verified':'needs_confirmation',
 hotels:[{id:hid,name:automatic?original:'地图'+original,address:'测试街'+(request.offset+index+1)+'号',city:'广州',name_match:automatic,address_match:true,city_match:true}],
 restaurants:[{id:rid,name:automatic?restaurant:'地图'+restaurant,address:'测试路'+branchIndex+'号',city:'广州',name_match:automatic,address_match:true,city_match:true}],
 hotel_matching:{...m,selected_id:automatic?hid:null},restaurant_matching:{...m,selected_id:automatic?rid:null},
 walk:automatic?{origin_name:restaurant,destination_name:original,distance_meters:index===0?900:index===1?1600:1900,duration_seconds:index===0?1200:600,point_precision:'poi_point',night_access_verified:false}:null,
 walk_limit_status:automatic?(index===0?'within_1500':'over_1500'):'unverified',
 drive:automatic&&index>0?{origin_name:restaurant,destination_name:original,distance_meters:3200,duration_seconds:index===1?900:901,point_precision:'poi_point',pickup_wait_verified:false,future_traffic_verified:false}:null,
 drive_limit_status:automatic&&index>0?(index===1?'within_900':'over_900'):'unverified',
 transport_status:automatic?(index===2?'rejected_both_modes_over_limit':'pass_walk_or_drive'):'unknown_or_partial',
 hotel_match_basis:automatic?'exact_fields':null,restaurant_match_basis:automatic?'exact_fields':null,
 queried_at:new Date().toISOString(),expires_in_seconds:120,error:null,data_mode:'test'};
}
export function walkBatchFixture(request:BranchWalkBatchRequest,automatic=false):BranchWalkBatchResponse {
 return {request_id:request.request_id,operation_id:request.operation_id,provider:'amap',status:automatic?'complete':'partial',
 offset:request.offset,total_pairs:6,next_offset:request.offset===0?3:null,processed_pairs:3,map_http_requests:automatic?11:6,
 pairs:Array.from({length:3},(_,i)=>walkPairFixture(request,i,automatic)),queried_at:new Date().toISOString(),warnings:[],error:null,data_mode:'test'};
}
