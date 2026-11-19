import {searchFixture} from './fixtures';
import type {RecallResponse} from '../types';
export function fixture():RecallResponse{
 const base=searchFixture();
 return {...base,attempted_hotel_names:[],continuation_cursor:null,continuation_expires_at:null,plan_queried_at:null,revision:0,round_number:1,continuation_delta:false,removed_hotel_ids:[],retained_total:2,hotel_observations:[{hotel_id:101,queried_at:base.queried_at},{hotel_id:102,queried_at:base.queried_at}],strategy:'branch_place',walk_check_available:true,operation_id:'test-operation',status:'partial',
 hotels:[base.hotels[0],{...base.hotels[0],id:102,name:'第二门店住宿'}],map_provider:'amap',restaurant_keyword:'海底捞',
 branches:[{role:'restaurant_body',role_reason:'name_and_category',source_category:'美食:火锅',nearby_status:'not_requested',hotel_targets:[],id:'b1',name:'测试餐厅1店',address:'测试路1号',city:'广州',status:'complete',query_place:'广州 测试路1号',hotel_ids:[101],returned_count:1,limited:false,reason:null,error:null,queried_at:null},
 {role:'restaurant_body',role_reason:'name_and_category',source_category:'美食:火锅',nearby_status:'not_requested',hotel_targets:[],id:'b2',name:'测试餐厅2店',address:'测试路2号',city:'广州',status:'complete',query_place:'广州 测试路2号',hotel_ids:[102],returned_count:1,limited:false,reason:null,error:null,queried_at:null},
 {role:'restaurant_body',role_reason:'name_and_category',source_category:'美食:火锅',nearby_status:'not_requested',hotel_targets:[],id:'b3',name:'测试餐厅3店',address:'测试路3号',city:'广州',status:'skipped',query_place:'广州 测试路3号',hotel_ids:[],returned_count:0,limited:false,reason:'deadline',error:null,queried_at:null}],
 recall_coverage:{total_hotel_lookups:0,total_quote_http_requests:0,role_restaurant_bodies:3,role_auxiliary:0,role_unknown:0,map_http_requests:0,nearby_branches_attempted:0,named_hotel_map_lookups:0,named_hotel_map_matches:0,nearby_invalid:0,hotel_lookups_attempted:0,hotel_lookup_limit:5,lookup_reuses:0,excluded_source_hotels:0,unqueried_targets:0,pages_requested:2,raw_places:20,unique_places:3,invalid_places:0,total_hint:null,discovery_stop_reason:'page_limit',discovery_complete:false,queried_branches:2,branch_limit:3,identity_conflicts:0,duplicate_hotels:0,identical_branch_sets:0,quote_http_requests:5}};
}

export function mapFixture():RecallResponse {
 const data=fixture();data.strategy='map_hotels';data.attempted_hotel_names=['测试查名1','测试查名2','测试查名3','测试查名4','测试查名5'];data.continuation_cursor='a'.repeat(32);data.continuation_expires_at=new Date(Date.now()+1800000).toISOString();data.plan_queried_at=data.queried_at;
 Object.assign(data.recall_coverage,{map_http_requests:5,nearby_branches_attempted:3,hotel_lookups_attempted:5,total_hotel_lookups:5,total_quote_http_requests:8,lookup_reuses:1,excluded_source_hotels:1,unqueried_targets:2});
 data.branches[0].nearby_status='complete';
 data.branches[0].hotel_targets=[
 {id:'target1',name:'地图测试住宿',address:'地图测试街1号',city:'广州',status:'retained',reason:null,source_count:1,hotel_id:101,match_status:'unverified'},
 {id:'target2',name:'其他分店查询',address:'测试街2号',city:'广州',status:'excluded',reason:'other_source_hotel',source_count:1,hotel_id:null,match_status:null},
 {id:'target3',name:'未查地图住宿',address:'测试街3号',city:'广州',status:'not_queried',reason:'lookup_budget',source_count:0,hotel_id:null,match_status:null},
 ];
 return data;
}

export function continuationFixture(previous:RecallResponse,operation:string):RecallResponse {
 const next=structuredClone(previous);next.attempted_hotel_names.push('测试查名6','测试查名7','测试查名8','测试查名9','测试查名10');next.operation_id=operation;next.revision++;next.round_number++;
 next.continuation_delta=true;next.continuation_cursor=null;next.queried_at=new Date(Date.parse(previous.queried_at)+60000).toISOString();
 next.hotels=[{...previous.hotels[0],id:103,name:'续查新增住宿',address:'测试街103号',reference_price_fen:13200}];
 next.branches[0].hotel_ids.push(103);next.retained_total=previous.hotels.length+1;
 next.branches[0].hotel_targets.forEach(t=>{if(t.id==="target3"){t.status="retained";t.hotel_id=103;t.reason=null;t.match_status="unverified";t.source_count=1;}});
 next.hotel_observations.push({hotel_id:103,queried_at:next.queried_at});
 Object.assign(next.recall_coverage,{hotel_lookups_attempted:5,total_hotel_lookups:10,quote_http_requests:8,total_quote_http_requests:16,unqueried_targets:0,map_http_requests:0});
 return next;
}
