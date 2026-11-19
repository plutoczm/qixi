import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import App from '../App';
import {health,searchHotels,loadTuniuPage,hotelQuotes,recallHotels} from '../api';
import {searchFixture,quotesFixture} from './fixtures';

vi.mock('../api',()=>({
  health:vi.fn(),searchHotels:vi.fn(),loadTuniuPage:vi.fn(),hotelQuotes:vi.fn(),recallHotels:vi.fn(),
  cancelRecall:vi.fn(),continueRecall:vi.fn(),travelPlaces:vi.fn(),travelRoute:vi.fn(),
  checkpointState:vi.fn(),saveCheckpoint:vi.fn(),resumeCheckpoint:vi.fn(),discoverySearch:vi.fn(),
}));

beforeEach(()=>{
  vi.resetAllMocks();
  vi.mocked(health).mockResolvedValue({
    status:'ok',provider:'tuniu',credential_configured:false,
    web_ready:true,today_shanghai:'2026-11-02',
    features:{maps:true,fare:true,llm:true,multi_branch_recall:false}, data_mode:'live', dataset:null, channels:{},
  });
});

it('makes Tuniu the displayed provider and blocks unported multi-branch recall',async()=>{
  render(<App/>);
  expect(await screen.findByText(/价格来自途牛，搜索价仅供参考/)).toBeInTheDocument();
  expect(screen.getByText(/暂不提供跨餐厅门店的周边酒店比较/)).toBeInTheDocument();
  expect(screen.queryByLabelText('比较多家餐厅周边住宿')).toBeNull();
  expect(screen.getByText(/价格来自途牛/)).toBeInTheDocument();
  expect(screen.getByText(/酒店搜索服务暂时不可用/)).toBeInTheDocument();
  expect(recallHotels).not.toHaveBeenCalled();
});

it('never shows a RollingGo booking link for Tuniu even if malicious detail claims matched',async()=>{
  const result=searchFixture();
  result.source={id:'tuniu',name:'途牛'};
  result.request_id='tuniu-test-only';
  result.hotels[0].reference_price_fen=17400;
  const detail=quotesFixture();
  detail.source={id:'tuniu',name:'途牛'};
  detail.request_id='tuniu-test-only';
  detail.booking_status='matched';
  detail.occupancy_status='matched';
  detail.booking_url='https://rollinggo.cn/unsafe';
  detail.quotes[0].price_basis='supplier_reference';
  detail.quotes[0].source_inventory_observed=2;
  vi.mocked(searchHotels).mockResolvedValue(result);
  vi.mocked(hotelQuotes).mockResolvedValue(detail);
  render(<App/>);
  expect(await screen.findByText(/价格来自途牛，搜索价仅供参考/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  await screen.findByText('界面测试住宿');
  expect(screen.getByText('途牛 · 住宿')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/查看房型/}));
  expect(await screen.findByText('途牛房型参考价 · 非最终总价')).toBeInTheDocument();
  expect(screen.getByText(/来源本次库存观察：2/)).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/前往.*核实条件/})).toBeNull();
  expect(screen.getByText('入住条件未核实，跳转已停用')).toBeInTheDocument();
  await waitFor(()=>expect(hotelQuotes).toHaveBeenCalledTimes(1));
});

it('does not silently show RollingGo results as Tuniu when supplier is unavailable',async()=>{
  const failed=searchFixture();
  failed.source={id:'tuniu',name:'途牛'};
  failed.hotels=[];
  failed.status='unavailable';
  failed.error={code:'CONFIGURATION_MISSING',message:'途牛尚未授权；请先完成合法的本机认证配置。'};
  vi.mocked(searchHotels).mockResolvedValue(failed);
  render(<App/>);
  expect(await screen.findByText(/价格来自途牛，搜索价仅供参考/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('途牛尚未授权');
  expect(screen.queryByText('界面测试住宿')).toBeNull();
});


it('shows a partial-source-page explanation without any automatic retry',async()=>{
  const partial=searchFixture();
  partial.source={id:'tuniu',name:'途牛'};
  partial.hotels=[];
  partial.status='partial';
  partial.coverage.returned_count=0;
  partial.coverage.limited=true;
  partial.error=null;
  vi.mocked(searchHotels).mockResolvedValue(partial);
  render(<App/>);
  expect(await screen.findByText(/价格来自途牛，搜索价仅供参考/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  expect(await screen.findByRole('heading',{name:'途牛首页无候选，但仍有未查询的来源页'})).toBeInTheDocument();
  expect(screen.getByText(/重新搜索会消耗途牛请求额度/)).toBeInTheDocument();
  expect(screen.queryByText('界面测试住宿')).toBeNull();
  await waitFor(()=>expect(searchHotels).toHaveBeenCalledTimes(1));
});


it('requests exactly one manually selected source page and preserves one search id',async()=>{
  const first=searchFixture();
  first.source={id:'tuniu',name:'途牛'};
  first.request_id='tuniu-test-session';
  first.current_page=1;
  first.next_page=2;
  first.status='partial';
  first.hotels=[];
  first.coverage.returned_count=0;
  first.coverage.limited=true;
  const second=searchFixture();
  second.source={id:'tuniu',name:'途牛'};
  second.request_id=first.request_id;
  second.current_page=2;
  second.next_page=null;
  second.status='partial';
  second.hotels[0].name='第二页测试住宿';
  vi.mocked(searchHotels).mockResolvedValue(first);
  vi.mocked(loadTuniuPage).mockResolvedValue(second);
  render(<App/>);
  expect(await screen.findByText(/价格来自途牛，搜索价仅供参考/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
  const btn=await screen.findByRole('button',{name:/手动查看途牛第 2 页/});
  expect(loadTuniuPage).not.toHaveBeenCalled();
  fireEvent.click(btn);
  await screen.findByText('第二页测试住宿');
  expect(loadTuniuPage).toHaveBeenCalledTimes(1);
  const args=vi.mocked(loadTuniuPage).mock.calls[0];
  expect(args[0]).toBe(first.request_id);
  expect(args[1]).toBe(2);
  expect(screen.queryByRole('button',{name:/手动查看途牛第 3 页/})).toBeNull();
  expect(searchHotels).toHaveBeenCalledTimes(1);
});
