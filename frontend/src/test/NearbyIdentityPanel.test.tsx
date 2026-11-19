import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import NearbyIdentityPanel from '../NearbyIdentityPanel';
import {confirmNearbyIdentity,revokeNearbyIdentity,nearbyIdentityStatus,hotelQuotes,searchHotels,resolveNearby,exploreNearby,walkNearby} from '../api';
import {context,searchFixture,quotesFixture} from './fixtures';
import type {NearbyHotelOption,NearbyIdentityResponse,QuotesResponse} from '../types';

vi.mock('../api',()=>({confirmNearbyIdentity:vi.fn(),revokeNearbyIdentity:vi.fn(),nearbyIdentityStatus:vi.fn(),
 hotelQuotes:vi.fn(),searchHotels:vi.fn(),resolveNearby:vi.fn(),exploreNearby:vi.fn(),walkNearby:vi.fn()}));
const hotel=searchFixture().hotels[0];
const point:NearbyHotelOption={id:'b'.repeat(32),name:hotel.name,address:hotel.address!,city:'广州市',straight_line_meters:120,
 source_match:'unverified',source_hotel_id:null,walking_distance_meters:null,walking_duration_seconds:null,
 rating_status:'unavailable',map_rating:null,review_count:null,stayed_count:null,guest_review:null};
const scope={contextId:'c'.repeat(32),anchorId:'a'.repeat(32),requestId:'source-one'};
const base:NearbyIdentityResponse={context_id:scope.contextId,anchor_id:scope.anchorId,request_id:scope.requestId,
 status:'complete',revision:0,intent_seq:0,expires_in_seconds:120,bindings:[],reason:'仅本轮城市缺失',error:null,
 eligible_pairs:[{hotel_option_id:point.id,source_hotel_id:hotel.id,map_name:point.name,map_address:point.address,map_city:point.city,
 source_name:hotel.name,source_address:hotel.address!,source_city_status:'missing',source_observed_at:'2026-11-17T01:02:03Z'}]};
const deferred=<T,>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return {promise,resolve};};
function mount(){return render(<NearbyIdentityPanel {...scope} point={point} sourceHotels={[hotel]} query={context} expiresAt={Date.now()+120000} disabled={false}/>);}
async function choose(){
 fireEvent.click(screen.getByRole('button',{name:/人工核对同店/}));
 const select=await screen.findByRole('combobox',{name:'本轮住宿来源酒店'});
 expect(select).toHaveValue('');
 expect(screen.getByRole('button',{name:'确认同店'})).toBeDisabled();
 fireEvent.change(select,{target:{value:String(hotel.id)}});
 fireEvent.click(screen.getByRole('checkbox',{name:'我已核对两侧名称、地址和城市，确认是同一家酒店'}));
}
beforeEach(()=>{
 vi.resetAllMocks();
 vi.mocked(nearbyIdentityStatus).mockResolvedValue({...base});
 vi.mocked(confirmNearbyIdentity).mockImplementation(async input=>{
  const result:NearbyIdentityResponse={...base,revision:1,intent_seq:input.intent_seq,
   bindings:[{hotel_option_id:point.id,source_hotel_id:hotel.id,operation_id:input.operation_id,identity_basis:'user_confirmed'}]};
  vi.mocked(nearbyIdentityStatus).mockResolvedValue(result);return result;
 });
 vi.mocked(revokeNearbyIdentity).mockResolvedValue({...base,revision:2,intent_seq:2});
});
afterEach(()=>{vi.restoreAllMocks();vi.useRealTimers();});

it('requires selection and unchecked acknowledgement; uses original price and separate provenance without source HTTP',async()=>{
 mount();expect(nearbyIdentityStatus).not.toHaveBeenCalled();await choose();
 expect(screen.getByText('住宿来源城市：未知（本轮原响应缺失）')).toBeInTheDocument();
 expect(screen.getByText(/住宿来源未独立提供城市/)).toBeInTheDocument();
 expect(screen.getByText(/2026/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'确认同店'}));
 await screen.findByText('你已确认同店 · 仅本轮有效');
 expect(screen.getByText('¥188')).toBeInTheDocument();
 expect(confirmNearbyIdentity).toHaveBeenCalledWith(expect.objectContaining({confirmed:true,source_hotel_id:hotel.id}),expect.any(AbortSignal));
 expect(hotelQuotes).not.toHaveBeenCalled();expect(searchHotels).not.toHaveBeenCalled();expect(resolveNearby).not.toHaveBeenCalled();
 expect(exploreNearby).not.toHaveBeenCalled();expect(walkNearby).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'撤销本次同店确认'}));
 expect(screen.queryByText('¥188')).toBeNull();
 expect(screen.queryByRole('button',{name:/查看这家酒店房型/})).toBeNull();
 await waitFor(()=>expect(revokeNearbyIdentity).toHaveBeenCalledTimes(1));
});

it('revokes pending confirmation and ignores late successful confirm response',async()=>{
 const pending=deferred<NearbyIdentityResponse>();vi.mocked(confirmNearbyIdentity).mockReturnValue(pending.promise);
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));
 const first=vi.mocked(confirmNearbyIdentity).mock.calls[0][0];
 fireEvent.click(screen.getByRole('button',{name:'撤销本次同店确认'}));
 await waitFor(()=>expect(revokeNearbyIdentity).toHaveBeenCalledTimes(1));
 expect(vi.mocked(revokeNearbyIdentity).mock.calls[0][0].target_operation_id).toBe(first.operation_id);
 await act(async()=>pending.resolve({...base,revision:1,intent_seq:1,bindings:[{hotel_option_id:point.id,source_hotel_id:hotel.id,operation_id:first.operation_id,identity_basis:'user_confirmed'}]}));
 expect(screen.queryByText('¥188')).toBeNull();
 expect(screen.queryByText('你已确认同店 · 仅本轮有效')).toBeNull();
});

it('409 revocation syncs locally then revokes only the same confirmation target',async()=>{
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));
 await screen.findByText('你已确认同店 · 仅本轮有效');
 const first=vi.mocked(confirmNearbyIdentity).mock.calls[0][0];
 vi.mocked(revokeNearbyIdentity).mockRejectedValueOnce(Object.assign(new Error('版本冲突'),{status:409}));
 vi.mocked(nearbyIdentityStatus).mockResolvedValue({...base,revision:1,intent_seq:1,
   bindings:[{hotel_option_id:point.id,source_hotel_id:hotel.id,operation_id:first.operation_id,identity_basis:'user_confirmed'}]});
 fireEvent.click(screen.getByRole('button',{name:'撤销本次同店确认'}));
 await waitFor(()=>expect(revokeNearbyIdentity).toHaveBeenCalledTimes(2));
 expect(vi.mocked(revokeNearbyIdentity).mock.calls[1][0]).toEqual(expect.objectContaining({expected_revision:1,target_operation_id:first.operation_id}));
 expect(screen.queryByText('¥188')).toBeNull();expect(searchHotels).not.toHaveBeenCalled();
});

it('409 cannot revoke a newer reassociation or restore its price',async()=>{
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 vi.mocked(revokeNearbyIdentity).mockRejectedValueOnce(Object.assign(new Error('版本冲突'),{status:409}));
 vi.mocked(nearbyIdentityStatus).mockResolvedValue({...base,revision:3,intent_seq:3,
   bindings:[{hotel_option_id:point.id,source_hotel_id:hotel.id,operation_id:'f'.repeat(32),identity_basis:'user_confirmed'}]});
 fireEvent.click(screen.getByRole('button',{name:'撤销本次同店确认'}));
 await screen.findByText(/确认关系已变化/);expect(revokeNearbyIdentity).toHaveBeenCalledTimes(1);
 expect(screen.queryByText('¥188')).toBeNull();expect(screen.queryByRole('button',{name:/查看这家酒店房型/})).toBeNull();
});

it('expiry and changed quote scope remove manual price and room entry',async()=>{
 const mounted=mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 const now=Date.now();vi.spyOn(Date,'now').mockReturnValue(now+121000);
 mounted.rerender(<NearbyIdentityPanel {...scope} point={point} sourceHotels={[hotel]} query={context} expiresAt={now+120000} disabled={false}/>);
 expect(screen.queryByText('¥188')).toBeNull();expect(screen.queryByRole('button',{name:/查看这家酒店房型/})).toBeNull();
 mounted.rerender(<NearbyIdentityPanel {...scope} requestId="new-session" point={point} sourceHotels={[hotel]} query={context} expiresAt={now+240000} disabled={false}/>);
 expect(screen.queryByText('¥188')).toBeNull();
});

it('does not attach a binding with a wrong scope or altered identity snapshot',async()=>{
 vi.mocked(nearbyIdentityStatus).mockResolvedValue({...base,request_id:'different-session'});
 mount();fireEvent.click(screen.getByRole('button',{name:/人工核对同店/}));await screen.findByRole('alert');
 expect(screen.queryByText('¥188')).toBeNull();expect(confirmNearbyIdentity).not.toHaveBeenCalled();
});

it('room query is explicit and a late quote after revocation stays unmounted',async()=>{
 const pending=deferred<QuotesResponse>();vi.mocked(hotelQuotes).mockReturnValue(pending.promise);
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 expect(hotelQuotes).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:/查看这家酒店房型/}));
 await waitFor(()=>expect(hotelQuotes).toHaveBeenCalledTimes(1));
 fireEvent.click(screen.getByRole('button',{name:'撤销本次同店确认'}));
 expect(vi.mocked(hotelQuotes).mock.calls[0][2].aborted).toBe(true);
 await act(async()=>pending.resolve({...quotesFixture(),request_id:scope.requestId}));
 expect(screen.queryByRole('region',{name:'人工确认酒店的房型参考'})).toBeNull();
 expect(screen.queryByText('测试大床房')).toBeNull();
});

it('uses structural quote context equality independent of object key order',async()=>{
 const quote=quotesFixture();
 vi.mocked(hotelQuotes).mockResolvedValue({...quote,request_id:scope.requestId,
  context:Object.fromEntries(Object.entries(context).reverse()) as typeof context});
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 fireEvent.click(screen.getByRole('button',{name:/查看这家酒店房型/}));
 await screen.findByRole('region',{name:'人工确认酒店的房型参考'});
 expect(screen.queryByRole('alert')).toBeNull();
});

it('revalidates local identity on room click and refuses changed SQL identity before supplier HTTP',async()=>{
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 vi.mocked(nearbyIdentityStatus).mockRejectedValue(Object.assign(new Error('本轮来源身份已变化'),{status:409}));
 fireEvent.click(screen.getByRole('button',{name:/查看这家酒店房型/}));
 await screen.findByText(/本轮来源身份已变化/);
 expect(hotelQuotes).not.toHaveBeenCalled();expect(screen.queryByText('¥188')).toBeNull();
});

it('checks actual expiry at room click even before the next display timer tick',async()=>{
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 vi.spyOn(Date,'now').mockReturnValue(Date.now()+121000);
 fireEvent.click(screen.getByRole('button',{name:/查看这家酒店房型/}));
 await screen.findByText(/同店关联已过期/);
 expect(hotelQuotes).not.toHaveBeenCalled();
});

it('failed revoke remains suppressed across sync and sync finishes only its original target',async()=>{
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 vi.mocked(revokeNearbyIdentity).mockRejectedValueOnce(Object.assign(new Error('本地连接中断'),{status:503}));
 fireEvent.click(screen.getByRole('button',{name:'撤销本次同店确认'}));await screen.findByText('本地连接中断');
 expect(screen.queryByText('¥188')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'同步本轮状态（仅本地）'}));
 await waitFor(()=>expect(revokeNearbyIdentity).toHaveBeenCalledTimes(2));
 expect(screen.queryByText('¥188')).toBeNull();
 expect(vi.mocked(revokeNearbyIdentity).mock.calls[1][0].target_operation_id).toBe(vi.mocked(confirmNearbyIdentity).mock.calls[0][0].operation_id);
});

it('repeated 409 then sync still revokes only the original pending target',async()=>{
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 const operation=vi.mocked(confirmNearbyIdentity).mock.calls[0][0].operation_id;
 const conflict=Object.assign(new Error('版本仍在变化'),{status:409});
 vi.mocked(revokeNearbyIdentity).mockRejectedValueOnce(conflict).mockRejectedValueOnce(conflict);
 fireEvent.click(screen.getByRole('button',{name:'撤销本次同店确认'}));await screen.findByText('版本仍在变化');
 fireEvent.click(screen.getByRole('button',{name:'同步本轮状态（仅本地）'}));
 await waitFor(()=>expect(revokeNearbyIdentity).toHaveBeenCalledTimes(3));
 expect(screen.queryByText('¥188')).toBeNull();
 expect(vi.mocked(revokeNearbyIdentity).mock.calls.every(([input])=>input.target_operation_id===operation)).toBe(true);
});

it('refuses a room response that arrives after expiry before the display timer runs',async()=>{
 const pending=deferred<QuotesResponse>();vi.mocked(hotelQuotes).mockReturnValue(pending.promise);
 mount();await choose();fireEvent.click(screen.getByRole('button',{name:'确认同店'}));await screen.findByText('你已确认同店 · 仅本轮有效');
 fireEvent.click(screen.getByRole('button',{name:/查看这家酒店房型/}));await waitFor(()=>expect(hotelQuotes).toHaveBeenCalledTimes(1));
 vi.spyOn(Date,'now').mockReturnValue(Date.now()+121000);
 await act(async()=>pending.resolve({...quotesFixture(),request_id:scope.requestId}));
 expect(screen.queryByRole('region',{name:'人工确认酒店的房型参考'})).toBeNull();
 expect(screen.queryByText('¥188')).toBeNull();
});
