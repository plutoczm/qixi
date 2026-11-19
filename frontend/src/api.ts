import type {Health, QuotesResponse, SearchInput, SearchResponse, SearchPageRequest, TravelPlaceRequest, TravelPlacesResponse, TravelRouteRequest, TravelRouteResponse,ConversationState,InterpretRequest,CancelInterpretRequest} from './types';

async function request<T>(url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, body === undefined ? {signal} : {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal,
    });
  } catch(reason) {
    // A deliberate stop or timeout is owned by the caller, never converted into network failure.
    if(reason instanceof Error && reason.name==='AbortError') throw reason;
    if(reason instanceof TypeError) {
      throw Object.assign(new Error('网络连接失败，请检查网络后再手动重试。'),{code:'NETWORK_ERROR'});
    }
    throw reason;
  }
  let data: unknown;
  try {data=await response.json();} catch {
    if (response.ok) throw new Error('暂时无法读取查询结果，请稍后手动重试。');
    // Many gateways return HTML for 502/503/429: preserve the status instead of reporting bad JSON.
    const message=response.status===429 ? '请求频繁，请稍后再手动重试。'
      : response.status>=500 ? '服务暂时不可用，请稍后再手动重试。'
      : '查询暂时不可用，请稍后手动重试。';
    throw Object.assign(new Error(message),{status:response.status});
  }
  if (!response.ok) {
    const candidate=data && typeof data==='object' && !Array.isArray(data)
      ? (data as {error?:{message?:unknown;code?:unknown}}).error : undefined;
    const fallback=response.status===429 ? '请求频繁，请稍后再手动重试。'
      : response.status>=500 ? '服务暂时不可用，请稍后再手动重试。'
      : '查询暂时不可用，请稍后手动重试。';
    const message=typeof candidate?.message==='string' && candidate.message.trim()
      ? candidate.message : fallback;
    const code=typeof candidate?.code==='string' ? candidate.code : undefined;
    throw Object.assign(new Error(message),{code,status:response.status});
  }
  return data as T;
}
export const health = (signal?:AbortSignal) => request<Health>('/api/health',undefined,signal);
export const searchHotels = (input:SearchInput, signal:AbortSignal) => request<SearchResponse>('/api/search',input,signal);
export const parallelSearchHotels = (input:SearchInput, operationId:string, signal:AbortSignal) =>
  request<import('./types').ParallelSearchResponse>('/api/search/parallel',{query:input,operation_id:operationId},signal);
export const cancelParallelSearch = (operationId:string) =>
  request<{accepted:boolean}>('/api/search/parallel/'+encodeURIComponent(operationId)+'/cancel',{});
export const loadTuniuPage = (request_id:string,expected_page:number,signal:AbortSignal) => {
  const input:SearchPageRequest={request_id,expected_page};
  return request<SearchResponse>('/api/search/next',input,signal);
};
export const hotelQuotes = (hotelId:number,requestId:string,signal:AbortSignal) =>
  request<QuotesResponse>(`/api/hotels/${hotelId}/quotes`,{request_id:requestId},signal);

// Candidate discovery only: one bounded supplier call, never a quote.
export const discoverySearch = (input:SearchInput, signal:AbortSignal) =>
  request<import('./types').DiscoveryResponse>('/api/discovery',input,signal);

export const travelPlaces=(hotelId:number,input:TravelPlaceRequest,signal:AbortSignal)=>
  request<TravelPlacesResponse>(`/api/hotels/${hotelId}/travel/places`,input,signal);
export const travelRoute=(hotelId:number,input:TravelRouteRequest,signal:AbortSignal)=>
  request<TravelRouteResponse>(`/api/hotels/${hotelId}/travel/route`,input,signal);

export const createConversation=(signal:AbortSignal)=>request<ConversationState>('/api/conversations',{},signal);
export const getConversation=(id:string)=>request<ConversationState>(`/api/conversations/${id}`);
export const checkpointState=()=>request<import('./types').CheckpointState>('/api/checkpoint');
export const saveCheckpoint=()=>request<{status:'saved';checkpoint:import('./types').CheckpointState}>('/api/checkpoint',{});
export const resumeCheckpoint=()=>request<import('./types').CheckpointResume>('/api/checkpoint/resume');
export const interpretConditions=(id:string,input:InterpretRequest,signal:AbortSignal)=>request<ConversationState>(`/api/conversations/${id}/operations`,input,signal);
export const cancelInterpretation=(id:string,input:CancelInterpretRequest)=>request<ConversationState>(`/api/conversations/${id}/cancel`,input);

export const recallHotels=(input:import('./types').RecallInput,signal:AbortSignal)=>
 request<import('./types').RecallResponse>('/api/recall',input,signal);
export const cancelRecall=(id:string)=>request<{accepted:boolean}>('/api/recall/'+id+'/cancel',{});

export const branchWalkBatch=(input:import('./types').BranchWalkBatchRequest,signal:AbortSignal)=>
 request<import('./types').BranchWalkBatchResponse>('/api/branch-walk/batch',input,signal);
export const branchWalkRoute=(input:import('./types').BranchWalkRouteRequest,signal:AbortSignal)=>
 request<import('./types').BranchWalkPair>('/api/branch-walk/route',input,signal);
export const cancelBranchWalk=(id:string)=>request<{accepted:boolean}>('/api/branch-walk/'+id+'/cancel',{});

export const continueRecall=(input:import('./types').RecallContinueRequest,signal:AbortSignal)=>
 request<import('./types').RecallResponse>('/api/recall/continue',input,signal);


// User-triggered map POI and hotel distance evidence; none are automatic calls.
export const resolveNearby=(input:import('./types').NearbyResolveRequest,signal:AbortSignal)=>
  request<import('./types').NearbyResolveResponse>('/api/places/resolve',input,signal);
export const exploreNearby=(input:import('./types').NearbyExploreRequest,signal:AbortSignal)=>
  request<import('./types').NearbyExploreResponse>('/api/places/nearby',input,signal);
export const exploreParallelNearby=(input:import('./types').ParallelNearbyExploreRequest,signal:AbortSignal)=>
  request<import('./types').ParallelNearbyExploreResponse>('/api/places/nearby/parallel',input,signal);
export const walkNearby=(input:import('./types').NearbyWalkRequest,signal:AbortSignal)=>
  request<import('./types').NearbyWalkResponse>('/api/places/walk',input,signal);

// These three endpoints only read/change local, expiring identity state.
export const nearbyIdentityStatus=(input:import('./types').NearbyIdentityStatusRequest,signal:AbortSignal)=>
  request<import('./types').NearbyIdentityResponse>('/api/places/identity/status',input,signal);
export const confirmNearbyIdentity=(input:import('./types').NearbyIdentityConfirmRequest,signal:AbortSignal)=>
  request<import('./types').NearbyIdentityResponse>('/api/places/identity/confirm',input,signal);
export const revokeNearbyIdentity=(input:import('./types').NearbyIdentityRevokeRequest,signal:AbortSignal)=>
  request<import('./types').NearbyIdentityResponse>('/api/places/identity/revoke',input,signal);
