import {fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import NearbyExplorer from '../NearbyExplorer';
import {resolveNearby,exploreNearby,exploreParallelNearby,walkNearby,searchHotels,parallelSearchHotels} from '../api';
import {context,searchFixture} from './fixtures';
import type {NearbyExploreResponse,NearbyResolveResponse,NearbyWalkResponse} from '../types';

vi.mock('../api',()=>({resolveNearby:vi.fn(),exploreNearby:vi.fn(),exploreParallelNearby:vi.fn(),
 walkNearby:vi.fn(),searchHotels:vi.fn(),parallelSearchHotels:vi.fn(),cancelParallelSearch:vi.fn()}));
const anchorId='a'.repeat(32),hotelId='b'.repeat(32),session='c'.repeat(32);
const resolved:NearbyResolveResponse={
 provider:'amap',context_id:session,queried_at:'2026-11-12T00:00:00Z',data_mode:'test',
 status:'complete',expires_in_seconds:120,coverage_note:'仅本次五个地点',
 options:[{id:anchorId,name:'上海迪士尼乐园',address:'浦东新区川沙镇',city:'上海市',category:'景点'}],error:null,
};
const near:NearbyExploreResponse={
 context_id:session,anchor_id:anchorId,provider:'amap',queried_at:'2026-11-12T00:00:00Z',data_mode:'test',
 status:'complete',coverage_note:'本次地图上限10',ranking_status:'not_comparable',
 ranking_reason:'缺住客评价人数与实际住过人数，不能综合排名。',
 hotels:[{
   id:hotelId,name:'界面测试住宿',address:'广州 · 测试地址',city:'上海市',straight_line_meters:340,
   source_hotel_id:101,source_match:'matched',walking_distance_meters:null,walking_duration_seconds:null,
   rating_status:'unavailable',map_rating:null,review_count:null,stayed_count:null,guest_review:null,
 },{
   id:'d'.repeat(32),name:'地图候选酒店B',address:'附近街道',city:'上海市',straight_line_meters:740,
   source_hotel_id:null,source_match:'unverified',walking_distance_meters:null,walking_duration_seconds:null,
   rating_status:'unavailable',map_rating:null,review_count:null,stayed_count:null,guest_review:null,
 }],error:null,
};
const walk:NearbyWalkResponse={
 context_id:session,hotel_option_id:hotelId,provider:'amap',queried_at:new Date().toISOString(),
 data_mode:'test',status:'complete',distance_meters:650,duration_seconds:490,error:null,
};
function mount(props?:{offline?:boolean;place?:string;region?:string}){
  const query={...context,place:props?.place??'上海迪士尼乐园',place_type:'景点' as const,country_region:props?.region??'中国大陆'};
  render(<NearbyExplorer context={query} hotels={searchFixture().hotels} requestId="search-test" offline={props?.offline??false}/>);
}
function setCity(){
 fireEvent.change(screen.getByLabelText('所在城市'),{target:{value:'上海'}});
}
async function doResolve(){
 setCity();
 fireEvent.click(screen.getByRole('button',{name:/搜索具体地点/}));
 await screen.findByText('上海迪士尼乐园',{exact:true,selector:'.nearby-option strong'});
}
async function doNearby(){
 await doResolve();
 fireEvent.click(screen.getByRole('radio'));
 fireEvent.click(screen.getByRole('button',{name:/查找此地点附近的酒店/}));
 await screen.findByText('地图候选酒店B');
}
beforeEach(()=>{
 vi.resetAllMocks();
 vi.mocked(resolveNearby).mockResolvedValue({...resolved});
 vi.mocked(exploreNearby).mockResolvedValue({...near});
 vi.mocked(walkNearby).mockResolvedValue({...walk});
});
afterEach(()=>vi.clearAllMocks());

describe('source-bound nearby explorer',()=>{
 it('binds a single confirmed POI search to the three-source round and updates its recommendation after a manual walk',async()=>{
   const search=searchFixture();
   vi.mocked(parallelSearchHotels).mockImplementation(async (query,operation_id)=>({
     operation_id,context:query,status:'partial',sources:[
       {id:'rollinggo',status:'complete',search:{...search,context:query},http_used:4,
        observed_at:search.queried_at,unavailable_reason:null,candidates:[]},
       {id:'tuniu',status:'unconfigured',search:null,http_used:0,observed_at:null,
        unavailable_reason:'CONFIGURATION_MISSING',candidates:[]},
       {id:'flyai',status:'complete',search:null,http_used:1,observed_at:null,unavailable_reason:null,candidates:[]},
     ],
   }));
   vi.mocked(exploreParallelNearby).mockResolvedValue({...near,recommendation_links:[
     {request_id:search.request_id,hotel_id:101,hotel_option_id:hotelId},
   ]});
   render(<NearbyExplorer context={{...context,place:'上海迪士尼乐园',place_type:'景点'}}
     hotels={[]} requestId={null} offline={false} parallel/>);
   await doResolve();
   fireEvent.click(screen.getByRole('radio'));
   fireEvent.click(screen.getByRole('button',{name:'按此目的地并行查三家住宿来源'}));
   await screen.findByText(/步行距离待核实/,{selector:'small'});
   expect(searchHotels).not.toHaveBeenCalled();
   fireEvent.click(screen.getByRole('button',{name:/查找此地点附近的酒店/}));
   await screen.findByText('地图候选酒店B');
   expect(exploreParallelNearby).toHaveBeenCalledWith({
     context_id:session,anchor_id:anchorId,request_id:search.request_id,
     operation_id:vi.mocked(parallelSearchHotels).mock.calls[0][1],
   },expect.any(AbortSignal));
   expect(walkNearby).not.toHaveBeenCalled();
   fireEvent.click(screen.getAllByRole('button',{name:'查步行路线'})[0]);
   expect(await screen.findByText(/已核实步行 650 米/)).toBeInTheDocument();
 });
 it('does no map or hotel calls on page render and refuses missing city',()=>{
   mount();
   expect(resolveNearby).not.toHaveBeenCalled();
   expect(exploreNearby).not.toHaveBeenCalled();
   expect(walkNearby).not.toHaveBeenCalled();
   fireEvent.click(screen.getByRole('button',{name:/搜索具体地点/}));
   expect(screen.getByRole('alert')).toHaveTextContent('所属国内城市');
   expect(resolveNearby).not.toHaveBeenCalled();
 });
 it('does not let an old overseas context override the fixed mainland map scope',()=>{
   mount({region:'日本',place:'上海迪士尼乐园'});
   expect(screen.queryByLabelText('国家 / 地区')).toBeNull();
   setCity();
   fireEvent.click(screen.getByRole('button',{name:/搜索具体地点/}));
   expect(resolveNearby).toHaveBeenCalledWith(expect.objectContaining({
     country_region:'中国大陆',provider:'amap',
   }),expect.any(AbortSignal));
 });
 it('requires choosing a specific POI before fetching nearby hotels',async()=>{
   mount();
   await doResolve();
   expect(resolveNearby).toHaveBeenCalledWith(expect.objectContaining({
     keyword:'上海迪士尼乐园',city:'上海',country_region:'中国大陆',provider:'amap',
   }),expect.any(AbortSignal));
   expect(exploreNearby).not.toHaveBeenCalled();
   expect(screen.getByRole('button',{name:/查找此地点附近的酒店/})).toBeDisabled();
   fireEvent.click(screen.getByRole('radio'));
   fireEvent.click(screen.getByRole('button',{name:/查找此地点附近的酒店/}));
   await screen.findByText('地图候选酒店B');
   expect(exploreNearby).toHaveBeenCalledWith({
     context_id:session,anchor_id:anchorId,request_id:'search-test',
   },expect.any(AbortSignal));
 });
 it('displays linked supplier reference price only for a unique identity match',async()=>{
   mount();
   await doNearby();
   expect(screen.getByText('¥188')).toBeInTheDocument();
   expect(screen.getByText(/地图酒店地点，尚未和报价来源确认同一家/)).toBeInTheDocument();
   expect(screen.queryByText(/实际住过人数：未取得|缺少已核实授权的数据/)).not.toBeInTheDocument();
   expect(screen.queryByRole('option',{name:/住客评分优先|四因素综合参考/})).not.toBeInTheDocument();
   expect(screen.getByRole('combobox',{name:'参考排序方式'})).toHaveValue('distance');
   expect(screen.getByRole('combobox',{name:'直线距离范围'})).toHaveValue('2000');
   fireEvent.change(screen.getByRole('combobox',{name:'直线距离范围'}),{target:{value:'500'}});
   expect(screen.queryByText('地图候选酒店B')).toBeNull();
   expect(exploreNearby).toHaveBeenCalledTimes(1);
 });
 it('queries walking route once on an explicit click without pretending it is straight line',async()=>{
   mount();
   await doNearby();
   expect(walkNearby).not.toHaveBeenCalled();
   fireEvent.click(screen.getAllByRole('button',{name:'查步行路线'})[0]);
   await screen.findByText(/目的地→酒店实测步行 650 米/);
   expect(walkNearby).toHaveBeenCalledTimes(1);
   expect(walkNearby).toHaveBeenCalledWith({context_id:session,hotel_option_id:hotelId},expect.any(AbortSignal));
   expect(screen.getByText(/直线约 340 米/)).toBeInTheDocument();
   expect(screen.getByRole('button',{name:'已尝试核对'})).toBeDisabled();
 });
 it('a hard walking threshold hides unknown routes but does not call the map automatically',async()=>{
   mount();
   await doNearby();
   const hard=screen.getByRole('checkbox',{name:/只看已核实步行/});
   fireEvent.click(hard);
   expect(screen.getByText(/未核实的酒店不等于超标/)).toBeInTheDocument();
   expect(walkNearby).not.toHaveBeenCalled();
   fireEvent.click(hard);
   fireEvent.click(screen.getAllByRole('button',{name:'查步行路线'})[0]);
   await screen.findByText(/目的地→酒店实测步行 650 米/);
   fireEvent.click(hard);
   expect(screen.getByText('界面测试住宿')).toBeInTheDocument();
   expect(screen.queryByText('地图候选酒店B')).toBeNull();
   expect(exploreNearby).toHaveBeenCalledTimes(1);
 });
 it('offline mode does not expose any active network controls',()=>{
   mount({offline:true});
   expect(screen.getByText(/当前是离线夹具，不能验证真实地点和路线/)).toBeInTheDocument();
   expect(screen.queryByRole('button',{name:/搜索具体地点/})).toBeNull();
 });
 it('switches sorting locally and excludes routes after context expiry',async()=>{
   mount();
   await doNearby();
   fireEvent.click(screen.getAllByRole('button',{name:'查步行路线'})[0]);
   await screen.findByText(/目的地→酒店实测步行 650 米/);
   const clock=vi.spyOn(Date,'now').mockReturnValue(Date.now()+121000);
   fireEvent.change(screen.getByRole('combobox',{name:'参考排序方式'}),{target:{value:'price'}});
   expect(screen.getByText(/本次地点证据已过期/)).toBeInTheDocument();
   fireEvent.click(screen.getByRole('checkbox',{name:/只看已核实步行/}));
   expect(screen.queryByText('界面测试住宿')).toBeNull();
   expect(resolveNearby).toHaveBeenCalledTimes(1);
   expect(exploreNearby).toHaveBeenCalledTimes(1);
   expect(walkNearby).toHaveBeenCalledTimes(1);
   clock.mockRestore();
 });
 it('provider errors show unavailable evidence, not invented map hotels',async()=>{
   vi.mocked(resolveNearby).mockResolvedValue({...resolved,status:'unavailable',options:[],error:{code:'RATE_LIMITED',message:'来源额度耗尽'}});
   mount();setCity();
   fireEvent.click(screen.getByRole('button',{name:/搜索具体地点/}));
   await screen.findByText('来源额度耗尽');
   expect(screen.queryByText('地图候选酒店B')).toBeNull();
   expect(exploreNearby).not.toHaveBeenCalled();
 });
});


describe('R107 destination-first supplier recall',()=>{
 it('searches only after anchor confirmation and joins the new supplier session',async()=>{
   const result={...searchFixture(),request_id:'destination-search'};
   vi.mocked(searchHotels).mockResolvedValue(result);
   mount();
   expect(searchHotels).not.toHaveBeenCalled();
   await doResolve();
   expect(screen.getByRole('button',{name:/按此目的地查住宿来源/})).toBeDisabled();
   fireEvent.click(screen.getByRole('radio'));
   fireEvent.click(screen.getByRole('button',{name:/按此目的地查住宿来源/}));
   await screen.findByText(/已取得目的地住宿参考/);
   expect(searchHotels).toHaveBeenCalledWith(expect.objectContaining({
     place:'上海',place_type:'城市',poi_name:'上海迪士尼乐园',limit:10,
     check_in:context.check_in,check_out:context.check_out,adults:context.adults,rooms:context.rooms,
   }),expect.any(AbortSignal));
   expect(exploreNearby).not.toHaveBeenCalled();
   fireEvent.click(screen.getByRole('button',{name:/查找此地点附近的酒店/}));
   await screen.findByText('地图候选酒店B');
   expect(exploreNearby).toHaveBeenCalledWith(expect.objectContaining({request_id:'destination-search'}),expect.any(AbortSignal));
   expect(searchHotels).toHaveBeenCalledTimes(1);
 });
 it('does not reuse old city prices after destination supplier failure',async()=>{
   vi.mocked(searchHotels).mockRejectedValue(new Error('测试来源失败'));
   mount();await doResolve();fireEvent.click(screen.getByRole('radio'));
   fireEvent.click(screen.getByRole('button',{name:/按此目的地查住宿来源/}));
   await screen.findByText('测试来源失败');
   fireEvent.click(screen.getByRole('button',{name:/查找此地点附近的酒店/}));
   await screen.findByText('地图候选酒店B');
   expect(exploreNearby).toHaveBeenCalledWith(expect.objectContaining({request_id:null}),expect.any(AbortSignal));
   expect(screen.queryByText('¥188')).toBeNull();
 });
});


it('refuses a destination supplier request after map context expiry',async()=>{
 mount();await doResolve();fireEvent.click(screen.getByRole('radio'));
 const now=vi.spyOn(Date,'now').mockReturnValue(Date.now()+121000);
 fireEvent.click(screen.getByRole('button',{name:/按此目的地查住宿来源/}));
 expect(searchHotels).not.toHaveBeenCalled();
 expect(screen.getByRole('alert')).toHaveTextContent('地点已过期');
 now.mockRestore();
});


it('shows an explicit map timeout and does not automatically repeat the operation',async()=>{
 vi.mocked(exploreNearby).mockResolvedValue({...near,status:'unavailable',hotels:[],
  error:{code:'TIMEOUT',message:'地图请求超时，本次未取得新的位置或路线证据；没有自动重试。'}});
 mount();await doResolve();fireEvent.click(screen.getByRole('radio'));
 fireEvent.click(screen.getByRole('button',{name:/查找此地点附近的酒店/}));
 expect(await screen.findByRole('alert')).toHaveTextContent('地图请求超时');
 expect(screen.getByRole('alert')).toHaveTextContent('没有自动重试');
 expect(exploreNearby).toHaveBeenCalledTimes(1);
 expect(walkNearby).not.toHaveBeenCalled();
 expect(screen.queryByText('地图候选酒店B')).toBeNull();
});
