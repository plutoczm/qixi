import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {beforeEach,it,expect,vi} from 'vitest';
import TravelPanel from '../TravelPanel';
import HotelTransport from '../HotelTransport';
import {travelPlaces,travelRoute} from '../api';
import {searchFixture} from './fixtures';
import {placesFixture,routeFixture} from './travel-fixtures';
vi.mock('../api',()=>({travelPlaces:vi.fn(),travelRoute:vi.fn()}));
beforeEach(()=>{vi.resetAllMocks();vi.mocked(travelPlaces).mockResolvedValue(placesFixture());vi.mocked(travelRoute).mockResolvedValue(routeFixture());});
function show(){const search=searchFixture();return render(<TravelPanel hotel={search.hotels[0]} requestId={search.request_id} context={search.context}/>);}
it('only resolves on request and never chooses the first of several restaurants',async()=>{
 show();expect(travelPlaces).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 expect(await screen.findByText('严格同店匹配通过')).toBeInTheDocument();
 expect(screen.getByLabelText('住宿地图地点')).toHaveValue('h1');
 expect(screen.getByLabelText('具体餐厅门店')).toHaveValue('');
 const button=screen.getByRole('button',{name:'查询交通与地铁'});expect(button).toBeDisabled();
 fireEvent.change(screen.getByLabelText('具体餐厅门店'),{target:{value:'r1'}});
 fireEvent.click(button);expect(await screen.findByText('800米 · 来源预计601秒')).toBeInTheDocument();
 expect(travelRoute).toHaveBeenCalledWith(101,expect.objectContaining({context_id:'travel-test-context',restaurant_option_id:'r1',confirm_hotel:false}),expect.any(AbortSignal));
});
it('requires an explicit check for an unverified hotel candidate',async()=>{
 vi.mocked(travelPlaces).mockResolvedValue(placesFixture('amap',false));show();
 fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 await screen.findByText('同店匹配尚未确认');
 fireEvent.change(screen.getByLabelText('住宿地图地点'),{target:{value:'h1'}});
 fireEvent.change(screen.getByLabelText('具体餐厅门店'),{target:{value:'r2'}});
 expect(screen.getByRole('button',{name:'查询交通与地铁'})).toBeDisabled();
 fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByRole('button',{name:'查询交通与地铁'}));
 await waitFor(()=>expect(travelRoute).toHaveBeenCalledWith(101,expect.objectContaining({confirm_hotel:true,restaurant_option_id:'r2'}),expect.any(AbortSignal)));
});
it('switching source clears selections and ignores an older in-flight result',async()=>{
 let finish!:(value:TravelPlacesResponseForTest)=>void;
 type TravelPlacesResponseForTest=ReturnType<typeof placesFixture>;
 vi.mocked(travelPlaces).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockResolvedValueOnce(placesFixture('didi'));
 show();fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 fireEvent.click(screen.getByRole('button',{name:'滴滴估价'}));
 fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 await screen.findByText('严格同店匹配通过');
 finish(placesFixture('amap'));
 await waitFor(()=>expect(screen.getByRole('button',{name:'查询当前车费'})).toBeInTheDocument());
 expect(travelRoute).not.toHaveBeenCalled();
});
it('shows an explicit fare interval without inventing a total or capacity guarantee',async()=>{
 vi.mocked(travelPlaces).mockResolvedValue(placesFixture('didi'));vi.mocked(travelRoute).mockResolvedValue(routeFixture('didi'));
 show();fireEvent.click(screen.getByRole('button',{name:'滴滴估价'}));fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 await screen.findByText('严格同店匹配通过');fireEvent.change(screen.getByLabelText('具体餐厅门店'),{target:{value:'r1'}});
 fireEvent.click(screen.getByRole('button',{name:'查询当前车费'}));
 expect(await screen.findByText('¥12—18')).toBeInTheDocument();
 expect(screen.getByText('当前预估 · 容量及优惠适用待核实')).toBeInTheDocument();
 expect(screen.getByText(/不跨响应拼接，也不代表可订/)).toBeInTheDocument();
});
it.each([
 ['amap','高德地图 · 交通路线参考'],
 ['didi','滴滴估价 · 当前车费估价'],
] as const)('personal live %s results do not appear as simulated data',async(provider,expected)=>{
 vi.mocked(travelPlaces).mockResolvedValue({...placesFixture(provider),data_mode:'live'});
 vi.mocked(travelRoute).mockResolvedValue({...routeFixture(provider),data_mode:'live'});
 show();
 if(provider==='didi')fireEvent.click(screen.getByRole('button',{name:'滴滴估价'}));
 fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 await screen.findByText('严格同店匹配通过');
 fireEvent.change(screen.getByLabelText('具体餐厅门店'),{target:{value:'r1'}});
 fireEvent.click(screen.getByRole('button',{name:provider==='didi'?'查询当前车费':'查询交通与地铁'}));
 expect(await screen.findByText(expected)).toBeInTheDocument();
 expect(screen.queryByText(/模拟步行路线|模拟当前车费参考/)).toBeNull();
});
it('provider failure has no demonstration fallback and preserves no stale route',async()=>{
 vi.mocked(travelPlaces).mockResolvedValue({...placesFixture(),status:'unavailable',hotels:[],restaurants:[],error:{code:'SOURCE_DAILY_LIMIT',message:'来源日额度已达上限。'}});
 show();fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('日额度');
 expect(screen.queryByText('严格同店匹配通过')).toBeNull();
});

it('shows the same-source driving fallback and the OR transport result',async()=>{
 const walking={...routeFixture()};
 vi.mocked(travelRoute).mockResolvedValue({...walking,return_walk:{...walking.return_walk!,distance_meters:2008,duration_seconds:1606},return_drive:{origin_name:'海底捞甲店',destination_name:'界面测试住宿',distance_meters:3500,duration_seconds:771,point_precision:'poi_point',pickup_wait_verified:false,future_traffic_verified:false},walk_limit_status:'over_1500',drive_limit_status:'within_900',transport_status:'pass_walk_or_drive'});
 show();fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 await screen.findByText('严格同店匹配通过');fireEvent.change(screen.getByLabelText('具体餐厅门店'),{target:{value:'r1'}});
 fireEvent.click(screen.getByRole('button',{name:'查询交通与地铁'}));
 expect(await screen.findByText('3500米 · 来源预计771秒')).toBeInTheDocument();
 expect(screen.getByText(/步行≤1500米或驾车≤900秒/)).toBeInTheDocument();
 expect(screen.getByText('交通子指标：步行≤1500米或驾车≤900秒，满足其中一项（不含接驾等待与未来路况）。')).toBeInTheDocument();
});
it('renders the P7 route assessment without borrowing a stay price',async()=>{
 const route=routeFixture();
 route.return_trip={plan_id:'route:101',tier:'reference_only',
  conditions:[{key:'stay_band',label:'住宿价格区间',verdict:'unknown',basis:'missing',detail:'住宿金额不在本响应内。'},
   {key:'night_access',label:'夜间可达',verdict:'unknown',basis:'unverified',detail:'夜间入口未核实，不承诺可用。'}],
  stay_known_fen:null,fare_used_fen:null,total_known_fen:null,
  guest_score_tenths:null,review_count:null,
  notes:['住宿已知费用小计未取得，本方案不计算综合总成本。'],
  risks:['当前估价不等于未来深夜实价，也不构成锁价。']};
 vi.mocked(travelRoute).mockResolvedValue(route);
 show();fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 await screen.findByText('严格同店匹配通过');fireEvent.change(screen.getByLabelText('具体餐厅门店'),{target:{value:'r1'}});
 fireEvent.click(screen.getByRole('button',{name:'查询交通与地铁'}));
 expect(await screen.findByText('返程判定（仅使用本次路线响应内的证据）')).toBeInTheDocument();
 expect(screen.getByText('住宿金额不在本响应内。')).toBeInTheDocument();
 expect(screen.getByText(/不跨响应拼接，也不代表可订/)).toBeInTheDocument();
 expect(screen.queryByText('已核实可订')).toBeNull();
});

it('queries a specific destination explicitly and retains the walk on the same card after collapse',async()=>{
 const search=searchFixture();
 render(<HotelTransport hotel={search.hotels[0]} requestId={search.request_id}
   context={{...search.context,poi_name:'永庆坊'}}/>);
 expect(travelPlaces).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'查交通'}));
 expect(travelPlaces).not.toHaveBeenCalled();
 expect(screen.getByLabelText('目的地名称或地址')).toHaveValue('永庆坊');
 fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 const origin=await screen.findByLabelText('确认具体目的地');
 expect(travelPlaces).toHaveBeenCalledWith(101,expect.objectContaining({
   city:search.context.place,destination_keyword:'永庆坊',
 }),expect.any(AbortSignal));
 const routeButton=screen.getByRole('button',{name:'查询路线'});
 expect(routeButton).toBeDisabled();
 fireEvent.change(origin,{target:{value:'r1'}});
 fireEvent.click(routeButton);
 expect(await screen.findByRole('status')).toHaveTextContent('海底捞甲店 → 酒店 · 步行约 11 分钟 · 高德');
 fireEvent.click(screen.getByRole('button',{name:'收起交通'}));
 expect(screen.queryByLabelText('目的地名称或地址')).not.toBeInTheDocument();
 expect(screen.getByRole('status')).toHaveTextContent('步行约 11 分钟');
});

it('shows the returned ride estimate without inventing a taxi duration',async()=>{
 const search=searchFixture();
 vi.mocked(travelPlaces).mockResolvedValue(placesFixture('didi'));
 vi.mocked(travelRoute).mockResolvedValue(routeFixture('didi'));
 render(<HotelTransport hotel={search.hotels[0]} requestId={search.request_id}
   context={{...search.context,poi_name:'永庆坊'}}/>);
 fireEvent.click(screen.getByRole('button',{name:'查交通'}));
 fireEvent.click(screen.getByRole('button',{name:'滴滴估价'}));
 fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
 fireEvent.change(await screen.findByLabelText('确认具体目的地'),{target:{value:'r1'}});
 fireEvent.click(screen.getByRole('button',{name:'查询当前车费'}));
 const summary=await screen.findByRole('status');
 expect(summary).toHaveTextContent('快车当前估价 ¥12–18 · 滴滴');
 expect(summary).not.toHaveTextContent('分钟');
});

it.each([true,false])('pairs drive and fare only for the same selected points (%s)',async sameOrigin=>{
 const search=searchFixture();
 const map=routeFixture();
 map.return_walk=null;
 map.return_drive={
   origin_name:'海底捞甲店',destination_name:search.hotels[0].name,distance_meters:2400,duration_seconds:481,
   point_precision:'poi_point',pickup_wait_verified:false,future_traffic_verified:false,
 };
 vi.mocked(travelPlaces).mockImplementation(async (_,input)=>placesFixture(input.provider));
 const fare=routeFixture('didi');
 if(!sameOrigin)fare.restaurant_name='海底捞乙店';
 vi.mocked(travelRoute).mockResolvedValueOnce(map).mockResolvedValueOnce(fare);
 render(<HotelTransport hotel={search.hotels[0]} requestId={search.request_id}
   context={{...search.context,poi_name:'永庆坊'}}/>);
 fireEvent.click(screen.getByRole('button',{name:'查交通'}));
 async function selectOrigin(id:string) {
  fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
  fireEvent.change(await screen.findByLabelText('确认具体目的地'),{target:{value:id}});
 }
 await selectOrigin('r1');
 fireEvent.click(screen.getByRole('button',{name:'查询路线'}));
 expect(await screen.findByRole('status')).toHaveTextContent('驾车约 9 分钟');
 fireEvent.click(screen.getByRole('button',{name:'滴滴估价'}));
 await selectOrigin(sameOrigin?'r1':'r2');
 fireEvent.click(screen.getByRole('button',{name:'查询当前车费'}));
 const summary=await screen.findByRole('status');
 expect(summary).toHaveTextContent('¥12–18');
 expect(summary.textContent?.includes('驾车约 9 分钟')).toBe(sameOrigin);
});

it('requires a fresh per-card confirmation before showing differently labelled endpoints together',async()=>{
 const search=searchFixture();
 const map=routeFixture();
 map.return_drive={
   origin_name:'海底捞甲店',destination_name:search.hotels[0].name,distance_meters:2400,duration_seconds:481,
   point_precision:'poi_point',pickup_wait_verified:false,future_traffic_verified:false,
 };
 const other=placesFixture('didi');
 other.restaurants[0]={...other.restaurants[0],name:'海底捞甲店（生日主题店）',address:'甲地址6楼'};
 const fare={...routeFixture('didi'),restaurant_name:other.restaurants[0].name};
 vi.mocked(travelPlaces).mockResolvedValueOnce(placesFixture()).mockResolvedValueOnce(other);
 vi.mocked(travelRoute).mockResolvedValueOnce(map).mockResolvedValueOnce(fare);
 render(<HotelTransport hotel={search.hotels[0]} requestId={search.request_id}
   context={{...search.context,poi_name:'海底捞甲店'}}/>);
 fireEvent.click(screen.getByRole('button',{name:'查交通'}));
 async function locate() {
  fireEvent.click(screen.getByRole('button',{name:'检索地点'}));
  fireEvent.change(await screen.findByLabelText('确认具体目的地'),{target:{value:'r1'}});
 }
 await locate();
 fireEvent.click(screen.getByRole('button',{name:'查询路线'}));
 expect(await screen.findByRole('status')).toHaveTextContent('驾车约 9 分钟');
 fireEvent.click(screen.getByRole('button',{name:'滴滴估价'}));
 await locate();
 fireEvent.click(screen.getByRole('button',{name:'查询当前车费'}));
 const summary=await screen.findByRole('status');
 expect(summary).not.toHaveTextContent('驾车约');
 fireEvent.click(screen.getByText('核对两家来源的地点'));
 expect(screen.getByText(/出发点.*甲地址6楼/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'确认两端各自是同一地点'}));
 expect(summary).toHaveTextContent('驾车约 9 分钟');
 expect(summary).toHaveTextContent('¥12–18');
 expect(summary).toHaveTextContent('地点已人工核对');
 fireEvent.click(screen.getByRole('button',{name:'撤销地点核对'}));
 expect(summary).not.toHaveTextContent('驾车约');
});
