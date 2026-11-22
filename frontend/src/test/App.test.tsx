import {render,screen,fireEvent,waitFor,within} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import App from '../App';
import ParallelHotelResults from '../ParallelHotelResults';
import SearchForm from '../SearchForm';
import {health,searchHotels,hotelQuotes,checkpointState,saveCheckpoint,parallelSearchHotels,cancelParallelSearch} from '../api';
import {searchFixture,quotesFixture} from './fixtures';
import type {Health,ParallelSearchResponse} from '../types';
vi.mock('../api',()=>({health:vi.fn(),searchHotels:vi.fn(),hotelQuotes:vi.fn(),travelPlaces:vi.fn(),travelRoute:vi.fn(),discoverySearch:vi.fn(),checkpointState:vi.fn(),saveCheckpoint:vi.fn(),resumeCheckpoint:vi.fn(),parallelSearchHotels:vi.fn(),cancelParallelSearch:vi.fn()}));
beforeEach(()=>{vi.resetAllMocks();vi.mocked(health).mockResolvedValue({status:'ok',provider:'rollinggo',credential_configured:true,web_ready:true,today_shanghai:'2026-10-16',features:{maps:false,fare:false,llm:false,multi_branch_recall:true}, data_mode:'live', dataset:null, channels:{}});vi.mocked(checkpointState).mockResolvedValue({connected:true,status:'connected',disconnected_at:null});vi.mocked(saveCheckpoint).mockResolvedValue({status:'saved',checkpoint:{connected:false,status:'disconnected',disconnected_at:'2026-11-09T10:00:00Z',checkpoint_id:'break-demo'}});});
describe('query browser',()=>{
  it('keeps recommendation targets distinct when two searches show separate FlyAI candidates',()=>{
    const first:ParallelSearchResponse={operation_id:'city-search',status:'partial',context:searchFixture().context,
      sources:[{id:'flyai',status:'complete',http_used:1,observed_at:null,unavailable_reason:null,search:null,
        candidates:[{name:'城市候选',address:'第一路1号',price_text:'¥180',star:null,nearby_transit:null,coordinates_present:false}]}]};
    const second:ParallelSearchResponse={...first,operation_id:'destination-search',sources:[
      {...first.sources[0],candidates:[{...first.sources[0].candidates[0],name:'目的地候选',address:'第二路1号'}]},
    ]};
    render(<><ParallelHotelResults batch={first}/><ParallelHotelResults batch={second}/></>);
    for(const name of ['城市候选','目的地候选']){
      const target=screen.getByRole('link',{name}).getAttribute('href')!.slice(1);
      expect(document.getElementById(target)).toHaveTextContent(name);
    }
  });
 it('submits one three-source search, keeps the failed source visible and opens quotes from the original search',async()=>{
  const original=searchFixture();
  original.hotels[0].guest_score={value:4.6,scale:5,source:'有授权的住客点评'};
  let publishHealth!:(value:Health)=>void;
  vi.mocked(health).mockImplementation(()=>new Promise(resolve=>{publishHealth=resolve;}));
  vi.mocked(cancelParallelSearch).mockResolvedValue({accepted:true});
  vi.mocked(parallelSearchHotels).mockImplementation(async (query,operation_id)=>({
    operation_id,status:'partial',context:query,sources:[
      {id:'rollinggo',status:'unconfigured',http_used:0,observed_at:null,unavailable_reason:'CONFIGURATION_MISSING',search:null,candidates:[]},
      {id:'tuniu',status:'complete',http_used:4,observed_at:original.queried_at,unavailable_reason:null,
        search:{...original,request_id:'tuniu-quote',source:{id:'tuniu',name:'途牛'}},candidates:[]},
      {id:'flyai',status:'complete',http_used:1,observed_at:null,unavailable_reason:null,search:null,candidates:[]},
    ],
  }));
  vi.mocked(hotelQuotes).mockResolvedValue({...quotesFixture(),request_id:'tuniu-quote',source:{id:'tuniu',name:'途牛'}});
  render(<App/>);
  await waitFor(()=>expect(health).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(searchHotels).not.toHaveBeenCalled();
  expect(parallelSearchHotels).not.toHaveBeenCalled();
  publishHealth({status:'ok',provider:'rollinggo',credential_configured:false,web_ready:true,today_shanghai:'2026-11-18',
    features:{maps:true,fare:true,llm:true,multi_branch_recall:true,parallel_search:true},data_mode:'live',dataset:null,channels:{}});
  expect(await screen.findByRole('heading',{name:'寻栖自动推荐'})).toBeInTheDocument();
  expect(screen.getByRole('region',{name:'道旅搜索结果'})).toHaveTextContent('未配置');
  expect(screen.getByText('住客评分 4.6/5 · 有授权的住客点评')).toBeInTheDocument();
  expect(searchHotels).not.toHaveBeenCalled();
  expect(parallelSearchHotels).toHaveBeenCalledTimes(1);
  fireEvent.click(within(screen.getByRole('region',{name:'途牛搜索结果'})).getByRole('button',{name:/查看房型/}));
  await waitFor(()=>expect(hotelQuotes).toHaveBeenCalledWith(101,'tuniu-quote',expect.any(AbortSignal)));
 });
 it('puts the hotel search first and keeps engineering jargon off the customer landing',()=>{
  render(<App/>);
  expect(screen.getAllByRole('link',{name:/开始搜索/}).every(link=>link.getAttribute('href')==='#query-panel')).toBe(true);
  expect(screen.getByRole('form',{name:'住宿查询条件'})).toBeInTheDocument();
  expect(screen.getByText(/AI 帮我整理入住需求/)).toBeInTheDocument();
  expect(screen.queryByText(/2.0 真实项目|本机个人自用|按需查源/)).not.toBeInTheDocument();
  expect(screen.queryByRole('navigation',{name:'查询证据进度'})).toBeNull();
  expect(searchHotels).not.toHaveBeenCalled();
 });
 it('presents a layered atmospheric hero without starting a supplier request',()=>{
  render(<App/>);
  const hero=screen.getByRole('region',{name:'寻栖住宿搜索'});
  expect(hero.querySelector('.scene-atmosphere')).not.toBeNull();
  expect(hero.querySelector('.scene-map-lines')).not.toBeNull();
  expect(hero.querySelector('[data-scene-depth]')).not.toBeNull();
  expect(searchHotels).not.toHaveBeenCalled();
 });
 it('opens the optional AI assistant from the hero without searching',()=>{
  render(<App/>);
  const drawer=document.getElementById('assistant-panel');
  expect(drawer).not.toHaveAttribute('open');
  fireEvent.click(screen.getByRole('link',{name:/让 AI 帮我填/}));
  expect(drawer).toHaveAttribute('open');
  expect(searchHotels).not.toHaveBeenCalled();
 });
 it('shows two neutral loading previews only while the initial search is pending',async()=>{
  vi.mocked(searchHotels).mockResolvedValue(searchFixture());
  render(<App/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(screen.getAllByTestId('search-preview-skeleton')).toHaveLength(2);
  expect(screen.getByRole('status')).toHaveTextContent('正在为你查找酒店');
  await screen.findByText('界面测试住宿');
  expect(screen.queryByTestId('search-preview-skeleton')).toBeNull();
 });
 it('scrolls to results when a hotel search begins',async()=>{
  vi.mocked(searchHotels).mockResolvedValue(searchFixture());
  render(<App/>);
  const panel=document.getElementById('results-panel')!;
  const scroll=vi.fn();Object.defineProperty(panel,'scrollIntoView',{value:scroll});
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(scroll).toHaveBeenCalledWith(expect.objectContaining({block:'start'}));
 });
 it('lets customers return to edit search after results appear',async()=>{
  vi.mocked(searchHotels).mockResolvedValue(searchFixture());
  render(<App/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  await screen.findByText('界面测试住宿');
  expect(screen.getByRole('link',{name:'修改搜索'})).toHaveAttribute('href','#query-panel');
 });
 it('places supplier coverage jargon under optional search details',async()=>{
  vi.mocked(searchHotels).mockResolvedValue(searchFixture());
  render(<App/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  await screen.findByText('界面测试住宿');
  const scope=screen.getByText('搜索范围与说明').closest('details');
  expect(scope).not.toBeNull();
  expect(scope).not.toHaveAttribute('open');
  expect(scope).toHaveTextContent('本次查询上限');
 });
 it('does not query supplier until explicit submit; preserves unknowns and handoff conditions',async()=>{
  vi.mocked(searchHotels).mockResolvedValue(searchFixture());vi.mocked(hotelQuotes).mockResolvedValue(quotesFixture());
  render(<App/>);expect(searchHotels).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(await screen.findByText('界面测试住宿')).toBeInTheDocument();
  expect(screen.queryByText('住客评分未取得')).not.toBeInTheDocument();
  expect(screen.queryByText('独立客房待核实')).not.toBeInTheDocument();
  expect(screen.queryByText('独卫待核实')).not.toBeInTheDocument();
  expect(screen.queryByText('完整费用待核实')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'查交通'})).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/前往道旅/})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/查看房型/}));
  expect(await screen.findByText('预订后不可退款')).toBeInTheDocument();
  expect(hotelQuotes).toHaveBeenCalledWith(101,'ui-test-search',expect.any(AbortSignal));
  expect(screen.getByRole('link',{name:/前往道旅/})).toHaveAttribute('href',quotesFixture().booking_url!);
  fireEvent.click(screen.getByRole('button',{name:/刷新报价/}));
  await waitFor(()=>expect(hotelQuotes).toHaveBeenCalledTimes(2));
 });
 it('clears budget by making a new request with all other context unchanged',async()=>{
  const empty=searchFixture();empty.hotels=[];empty.context.budget_fen=20000;
  vi.mocked(searchHotels).mockResolvedValue(empty);render(<App/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  fireEvent.click(await screen.findByRole('button',{name:'清除预算再查一次'}));
  await waitFor(()=>expect(searchHotels).toHaveBeenCalledTimes(2));
  expect(vi.mocked(searchHotels).mock.calls[1][0]).toEqual({...empty.context,budget_fen:null});
 });
 it('shows a single explicit retry for a failed first search and preserves the exact request conditions',async()=>{
  vi.mocked(searchHotels)
    .mockRejectedValueOnce(Object.assign(new Error('请求频繁，请稍后再手动重试。'),{status:429}))
    .mockResolvedValueOnce(searchFixture());
  render(<App/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'成都'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('请求频繁');
  const retry=screen.getByRole('button',{name:/按上次条件重新查询/});
  expect(screen.getByText(/成都.*再次调用/)).toBeInTheDocument();
  expect(searchHotels).toHaveBeenCalledTimes(1);
  fireEvent.click(retry);
  expect(await screen.findByText('界面测试住宿')).toBeInTheDocument();
  expect(searchHotels).toHaveBeenCalledTimes(2);
  expect(vi.mocked(searchHotels).mock.calls[1][0]).toEqual(vi.mocked(searchHotels).mock.calls[0][0]);
  expect(screen.queryByRole('button',{name:/按上次条件重新查询/})).toBeNull();
 });
 it('shows stopped state without encouraging replay of a possibly still-running supplier call',async()=>{
  vi.mocked(searchHotels).mockImplementation((_input,signal)=>new Promise((_resolve,reject)=>{
    signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
  }));
  render(<App/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  await waitFor(()=>expect(searchHotels).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button',{name:'停止查询'}));
  expect(screen.getByRole('alert')).toHaveTextContent('已请求停止');
  expect(screen.queryByRole('button',{name:/按上次条件重新查询/})).toBeNull();
  expect(searchHotels).toHaveBeenCalledTimes(1);
 });
 it('reports unavailable without showing fake hotel results',async()=>{
  vi.mocked(searchHotels).mockResolvedValue({...searchFixture(),status:'unavailable',hotels:[],error:{code:'TIMEOUT',message:'查询超时，请稍后重试。'}});
  render(<App/>);fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('查询超时');
  expect(screen.queryByText('界面测试住宿')).toBeNull();
 });
 it('ignores an old request that resolves after a newer search',async()=>{
  let finish!:(value:ReturnType<typeof searchFixture>)=>void;
  vi.mocked(searchHotels).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockResolvedValueOnce({...searchFixture(),hotels:[]});
  render(<App/>);fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  await waitFor(()=>expect(searchHotels).toHaveBeenCalledTimes(1));
  fireEvent.submit(screen.getByRole('form',{name:'住宿查询条件'}));
  await screen.findByText('这次条件下，暂未查到住宿');
  finish(searchFixture());await waitFor(()=>expect(screen.queryByText('界面测试住宿')).toBeNull());
 });
});
describe('observed source limitations',()=>{
 it.each(['mismatch','unverified'] as const)('blocks %s booking context even if a URL is supplied',async status=>{
  const detail={...quotesFixture(),booking_status:status};
  vi.mocked(searchHotels).mockResolvedValue(searchFixture());vi.mocked(hotelQuotes).mockResolvedValue(detail);
  render(<App/>);fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  fireEvent.click(await screen.findByRole('button',{name:/查看房型/}));
  expect(await screen.findByText(/入住条件未核实，跳转已停用/)).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/前往道旅/})).toBeNull();
  expect(screen.getByRole('link',{name:/打开道旅首页自行核对/})).toHaveAttribute('href','https://rollinggo.cn/');
 });
 it('expands a bounded empty search without relaxing budget or dates',async()=>{
  const empty=searchFixture();empty.hotels=[];empty.context.limit=6;empty.context.budget_fen=20000;
  vi.mocked(searchHotels).mockResolvedValue(empty);render(<App/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  fireEvent.click(await screen.findByRole('button',{name:'扩大到20家再查'}));
  await waitFor(()=>expect(searchHotels).toHaveBeenCalledTimes(2));
  expect(vi.mocked(searchHotels).mock.calls[1][0]).toEqual({...empty.context,limit:20});
 });
 it('uses the verified source maximum by default and lets users reduce it',async()=>{
  const submit=vi.fn().mockResolvedValue(undefined);render(<SearchForm loading={false} onSubmit={submit}/>);
  expect(screen.getByLabelText('查询上限')).toHaveValue('20');
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.submit(screen.getByRole('form'));await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({limit:20})));
  fireEvent.change(screen.getByLabelText('查询上限'),{target:{value:'10'}});
  fireEvent.submit(screen.getByRole('form'));await waitFor(()=>expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({limit:10})));
 });
});
describe('form',()=>{
 it('offers core booking fields without making advanced filters prominent',()=>{
  render(<SearchForm loading={false} onSubmit={vi.fn()}/>);
  expect(screen.getByLabelText('希望住在哪个地方附近？')).toBeInTheDocument();
  expect(screen.getByLabelText('入住日期')).toBeInTheDocument();
  expect(screen.getByLabelText('每房成人')).toBeInTheDocument();
  const advanced=screen.getByText('更多住宿设置').closest('details');
  expect(advanced).not.toBeNull();
  expect(advanced).not.toHaveAttribute('open');
 });
 it('requires actual child ages and submits exact budget',async()=>{
  const submit=vi.fn().mockResolvedValue(undefined);render(<SearchForm loading={false} onSubmit={submit}/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.change(screen.getByLabelText('每房儿童'),{target:{value:'1'}});
  fireEvent.submit(screen.getByRole('form'));expect(await screen.findByRole('alert')).toHaveTextContent('儿童的年龄');expect(submit).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('儿童1年龄'),{target:{value:'0'}});
  fireEvent.change(screen.getByLabelText(/每晚预算参考/),{target:{value:'199.29'}});
  fireEvent.submit(screen.getByRole('form'));await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({children_ages:[0],budget_fen:19929})));
 });
 it('allows clearing date input without crashing',()=>{render(<SearchForm loading={false} onSubmit={vi.fn()}/>);fireEvent.change(screen.getByLabelText('入住日期'),{target:{value:''}});expect(screen.getByLabelText('退房日期')).toBeInTheDocument();});
});

describe('quote-plan status and booking handoff gates',()=>{
 it('shows reference-only plan status even when booking link parameters match',async()=>{
  const search=searchFixture();search.context.budget_fen=20000;
  const detail=quotesFixture();detail.context.budget_fen=20000;
  detail.booking_status='matched';detail.occupancy_status='unverified';
  detail.quotes[0].amount_fen=17200;detail.quotes[0].max_occupancy=4;detail.quotes[0].on_request=false;
  vi.mocked(searchHotels).mockResolvedValue(search);vi.mocked(hotelQuotes).mockResolvedValue(detail);
  render(<App/>);fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  fireEvent.click(await screen.findByRole('button',{name:/查看房型/}));
  expect(await screen.findByText('¥172')).toBeInTheDocument();
  expect(screen.getByText('道旅展示均价 · 非最终总价')).toBeInTheDocument();
  expect(screen.getByText(/来源标称单间最多4人；仅供2成人容量初筛/)).toBeInTheDocument();
  expect(screen.getByText('来源未标记需申请，不代表库存已确认')).toBeInTheDocument();
  expect(screen.getByText('参考层：整间与独卫按国内默认推定（未取得来源字段），展示价按已含税处理；到店另付未核实，且无来源跳转，非可订实付价。')).toBeInTheDocument();
  expect(screen.getByText(/来源未完整回显人数与房间组合/)).toBeInTheDocument();
  expect(screen.getByRole('link',{name:/前往道旅核实条件/})).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/立即预订|确认有房/})).toBeNull();
 });
 it('refuses a booking handoff if occupancy says mismatch despite URL status claiming matched',async()=>{
  const detail=quotesFixture();
  detail.booking_status='matched';detail.occupancy_status='mismatch';
  vi.mocked(searchHotels).mockResolvedValue(searchFixture());vi.mocked(hotelQuotes).mockResolvedValue(detail);
  render(<App/>);fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  fireEvent.click(await screen.findByRole('button',{name:/查看房型/}));
  expect(await screen.findByText(/来源回显的入住条件不一致/)).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/前往道旅/})).toBeNull();
  expect(screen.getByText('入住条件未核实，跳转已停用')).toBeInTheDocument();
 });
});
