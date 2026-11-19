import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import DiscoveryPanel from '../DiscoveryPanel';
import {discoverySearch} from '../api';
import {searchFixture} from './fixtures';
vi.mock('../api',()=>({discoverySearch:vi.fn()}));

const context=searchFixture().context;

function result(overrides:Record<string,unknown>={}){
  return {
    source:{id:'flyai' as const,name:'飞猪' as const},
    status:'complete' as const,
    queried_at:new Date().toISOString(),
    observed_at:new Date().toISOString(),
    http_used:1,
    candidates:[{
      name:'某民宿',address:'广州-越秀区-某路1号',price_text:'¥144',
      star:'经济型',nearby_transit:'近某地铁站',coordinates_present:true,
    }],
    limitations:['本次按「广州」查询，人数与房间数没有传给飞猪。','不与途牛／道旅报价合并、去重或排序；同一家店的身份也未核实。'],
    error:null,
    ...overrides,
  };
}

beforeEach(()=>vi.resetAllMocks());

it('never queries the discovery source until the user asks for it',()=>{
  render(<DiscoveryPanel context={context}/>);
  expect(discoverySearch).not.toHaveBeenCalled();
  expect(screen.getByRole('button',{name:/查询飞猪候选/})).toBeInTheDocument();
  expect(screen.getByText(/不与上方报价合并或排序/)).toBeInTheDocument();
});

it('shows the display price as supplier text and never as a comparable amount',async()=>{
  vi.mocked(discoverySearch).mockResolvedValue(result() as never);
  render(<DiscoveryPanel context={context}/>);
  fireEvent.click(screen.getByRole('button',{name:/查询飞猪候选/}));
  expect(await screen.findByText('某民宿')).toBeInTheDocument();
  expect(screen.getByText(/来源展示价原文“¥144”/)).toBeInTheDocument();
  expect(screen.getByText(/币种与口径未注明/)).toBeInTheDocument();
  // No booking entry and no quote-style price element may appear here.
  expect(screen.queryByRole('link')).toBeNull();
  expect(screen.queryByText(/^¥144$/)).toBeNull();
});

it('states the source limits instead of implying a complete or bookable list',async()=>{
  vi.mocked(discoverySearch).mockResolvedValue(result() as never);
  render(<DiscoveryPanel context={context}/>);
  fireEvent.click(screen.getByRole('button',{name:/查询飞猪候选/}));
  expect(await screen.findByText(/不与途牛／道旅报价合并/)).toBeInTheDocument();
  expect(screen.getByText(/来源带坐标（本页不用于距离或路线）/)).toBeInTheDocument();
});

it('reports a missing credential as its own state, not as empty inventory',async()=>{
  vi.mocked(discoverySearch).mockResolvedValue(result({
    status:'unconfigured',candidates:[],
    limitations:['飞猪凭据未配置，本次没有发起请求。'],
    error:{code:'CONFIGURATION_MISSING',message:'飞猪凭据未配置，本次没有发起请求。'},
  }) as never);
  render(<DiscoveryPanel context={context}/>);
  fireEvent.click(screen.getByRole('button',{name:/查询飞猪候选/}));
  const alert=await screen.findByRole('alert');
  expect(alert).toHaveTextContent(/飞猪凭据未配置/);
  expect(screen.getByText(/本次没有返回候选名称；这不代表该城市没有住宿/)).toBeInTheDocument();
  expect(screen.queryByRole('link')).toBeNull();
});

it('does not silently retry after a failure',async()=>{
  vi.mocked(discoverySearch).mockRejectedValue(new Error('飞猪查询未完成。'));
  render(<DiscoveryPanel context={context}/>);
  fireEvent.click(screen.getByRole('button',{name:/查询飞猪候选/}));
  await waitFor(()=>expect(discoverySearch).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole('alert')).toHaveTextContent('飞猪查询未完成。');
});
