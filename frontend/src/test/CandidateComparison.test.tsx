import {fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import CandidateComparison,{referenceDelta,referenceFen} from '../CandidateComparison';
import HotelResults from '../HotelResults';
import {searchFixture} from './fixtures';
import {fixture as recallFixture} from './recall-fixtures';
import type {Hotel,SearchResponse} from '../types';

vi.mock('../api',()=>({hotelQuotes:vi.fn(),travelPlaces:vi.fn(),travelRoute:vi.fn()}));
import {hotelQuotes} from '../api';

function sample(howMany=4):{result:SearchResponse;hotels:Hotel[]} {
  const result=searchFixture();
  result.data_mode='live';
  const base=result.hotels[0];
  const hotels=Array.from({length:howMany},(_,index)=>({
    ...base,id:101+index,name:'离线对比酒店'+(index+1),
    address:'广州 · 测试'+(index+1)+'号',
    currency:'CNY',reference_price_fen:12000+index*1100,
  }));
  return {result:{...result,hotels},hotels};
}

afterEach(()=>vi.clearAllMocks());

describe('manual same-result comparison',()=>{
  it('validates integer cents and never silently converts other currencies or missing values',()=>{
    const {hotels}=sample(3);
    expect(referenceFen(hotels[0])).toBe(12000);
    expect(referenceFen({...hotels[0],reference_price_fen:null})).toBeNull();
    expect(referenceFen({...hotels[0],reference_price_fen:-1})).toBeNull();
    expect(referenceFen({...hotels[0],reference_price_fen:120.3})).toBeNull();
    expect(referenceFen({...hotels[0],reference_price_fen:Infinity})).toBeNull();
    expect(referenceFen({...hotels[0],currency:'USD'})).toBeNull();
    expect(referenceFen({...hotels[0],currency:null})).toBeNull();
    const items=hotels.map(hotel=>({hotel,observedAt:new Date().toISOString()}));
    expect([...referenceDelta(items)!]).toEqual([[101,0],[102,1100],[103,2200]]);
    expect(referenceDelta([{...items[0],hotel:{...hotels[0],currency:'USD'}},items[1]])).toBeNull();
    expect(referenceDelta([items[0],{...items[1],hotel:{...hotels[1],reference_price_fen:null}}])).toBeNull();
    expect(referenceDelta([items[0]])).toBeNull();
    expect(referenceDelta([...items,items[0]])).toBeNull();
  });

  it('requires manual choices, caps at three, supports removal and clear without extra API calls',()=>{
    const {result,hotels}=sample();
    render(<HotelResults result={result} multi={null} hotels={hotels}/>);
    expect(screen.queryByRole('region',{name:'酒店参考候选对比'})).toBeNull();
    expect(screen.getByText('已选 0/3 家')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店1'}));
    expect(screen.getByText(/已选 1\/3 家/)).toBeInTheDocument();
    expect(screen.queryByRole('region',{name:'酒店参考候选对比'})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店2'}));
    const comparison=screen.getByRole('region',{name:'酒店参考候选对比'});
    expect(within(comparison).getByRole('row',{name:/相对所选最低参考价/})).toHaveTextContent('+11元（仅参考差额）');
    expect(within(comparison).getByRole('row',{name:/预订资格/})).toHaveTextContent('未核实');
    expect(within(comparison).queryByRole('link')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店3'}));
    expect(screen.getByText(/已选 3\/3 家/)).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'加入对比：离线对比酒店4'})).toBeDisabled();
    fireEvent.click(within(comparison).getByRole('button',{name:'移出对比：离线对比酒店2'}));
    expect(screen.getByRole('button',{name:'加入对比：离线对比酒店4'})).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店4'}));
    expect(within(comparison).getByText('离线对比酒店4')).toBeInTheDocument();
    fireEvent.click(within(comparison).getByRole('button',{name:'清空对比'}));
    expect(screen.queryByRole('region',{name:'酒店参考候选对比'})).toBeNull();
    expect(vi.mocked(hotelQuotes)).not.toHaveBeenCalled();
  });

  it('offers a compact compare dock that focuses the comparison without querying',()=>{
    const {result,hotels}=sample(3);
    render(<HotelResults result={result} multi={null} hotels={hotels}/>);
    expect(screen.queryByRole('region',{name:'酒店对比快捷栏'})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店1'}));
    let dock=screen.getByRole('region',{name:'酒店对比快捷栏'});
    expect(within(dock).getByText('1 / 3')).toBeInTheDocument();
    expect(within(dock).getByRole('button',{name:'查看对比'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店2'}));
    dock=screen.getByRole('region',{name:'酒店对比快捷栏'});
    const table=screen.getByRole('region',{name:'酒店参考候选对比'});
    const scroll=vi.fn();
    Object.defineProperty(table,'scrollIntoView',{value:scroll});
    fireEvent.click(within(dock).getByRole('button',{name:'查看对比'}));
    expect(scroll).toHaveBeenCalledWith(expect.objectContaining({block:'start'}));
    fireEvent.click(within(dock).getByRole('button',{name:'清空选择'}));
    expect(screen.queryByRole('region',{name:'酒店对比快捷栏'})).toBeNull();
    expect(vi.mocked(hotelQuotes)).not.toHaveBeenCalled();
  });

  it('does not make reference differences when one source price is unavailable or not CNY',()=>{
    const {result,hotels}=sample(2);
    hotels[1]={...hotels[1],currency:'USD',reference_price_fen:9000};
    render(<HotelResults result={result} multi={null} hotels={hotels}/>);
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店1'}));
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店2'}));
    const comparison=screen.getByRole('region',{name:'酒店参考候选对比'});
    expect(within(comparison).getByRole('row',{name:/搜索参考价/})).toHaveTextContent('人民币参考价未取得');
    expect(within(comparison).getByRole('row',{name:/相对所选最低参考价/})).toHaveTextContent('不可比较');
    expect(screen.getByText('人民币参考价待核实')).toBeInTheDocument();
    expect(comparison).not.toHaveTextContent('¥90');
  });

  it('displays per-hotel observation timestamps and stale warning without fetching',()=>{
    const {hotels}=sample(2);
    const recent=new Date().toISOString();
    const old=new Date(Date.now()-12*60_000).toISOString();
    render(<CandidateComparison
      items={[{hotel:hotels[0],observedAt:recent},{hotel:hotels[1],observedAt:old}]}
      onClear={()=>{}} onRemove={()=>{}} isTest={false}/>);
    const comparison=screen.getByRole('region',{name:'酒店参考候选对比'});
    expect(within(comparison).getByRole('row',{name:/来源查询时间/})).toHaveTextContent('已超过5分钟');
    expect(comparison).toHaveTextContent('不同酒店的来源观察时间可能不同');
    expect(vi.mocked(hotelQuotes)).not.toHaveBeenCalled();
  });

  it('synthetic mode remains marked as simulation and produces no booking action',()=>{
    const {result,hotels}=sample(2);
    result.data_mode='test';
    render(<HotelResults result={result} multi={null} hotels={hotels}/>);
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店1'}));
    fireEvent.click(screen.getByRole('button',{name:'加入对比：离线对比酒店2'}));
    expect(screen.getByText(/本次比较不能作为真实房价证据/)).toBeInTheDocument();
    expect(screen.queryByRole('link',{name:/预订|下单|支付/})).toBeNull();
  });

  it('does not borrow the latest recall timestamp when one hotel observation is absent',()=>{
    const result=recallFixture();
    result.hotel_observations=result.hotel_observations.filter(entry=>entry.hotel_id!==102);
    render(<HotelResults result={result} multi={result} hotels={result.hotels}/>);
    const buttons=screen.getAllByRole('button',{name:/加入对比/});
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    const comparison=screen.getByRole('region',{name:'酒店参考候选对比'});
    const row=within(comparison).getByRole('row',{name:/来源查询时间/});
    expect(row).toHaveTextContent('查询时间未取得');
    expect(row).toHaveTextContent('时效未核实');
    expect(row).not.toHaveTextContent('已超过5分钟');
  });
});
