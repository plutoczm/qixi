import {fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import HotelResults from '../HotelResults';
import {searchFixture} from './fixtures';
import type {Hotel} from '../types';

vi.mock('../api',()=>({hotelQuotes:vi.fn(),travelPlaces:vi.fn(),travelRoute:vi.fn()}));
import {hotelQuotes,travelPlaces,travelRoute} from '../api';

function setup(){
  const result=searchFixture();
  result.data_mode='test';
  const base=result.hotels[0];
  const hotels:Hotel[]=[
    {...base,id:501,name:'木居客栈',address:'成都一环',reference_price_fen:21000,currency:'CNY'},
    {...base,id:502,name:'青瓦酒店',address:'上海',reference_price_fen:null,currency:'CNY'},
    {...base,id:503,name:'暖院旅宿',address:'成都二环',reference_price_fen:15000,currency:'CNY'},
    {...base,id:504,name:'海外酒店',address:'广州',reference_price_fen:9800,currency:'USD'},
    {...base,id:505,name:'清风民宿',address:'成都三环',reference_price_fen:15000,currency:'CNY'},
  ];
  return {result:{...result,hotels},hotels};
}
const names=()=>Array.from(document.querySelectorAll('.hotel-grid .hotel-card h3')).map(x=>x.textContent);

afterEach(()=>vi.clearAllMocks());

describe('hotel result local-only sorting and filters',()=>{
 it('sorts already received hotels while keeping unknown price last without querying providers',()=>{
  const {result,hotels}=setup();
  render(<HotelResults result={result} multi={null} hotels={hotels}/>);
  expect(names()).toEqual(['木居客栈','青瓦酒店','暖院旅宿','海外酒店','清风民宿']);
  fireEvent.change(screen.getByRole('combobox',{name:'排序方式'}),{target:{value:'price_low'}});
  expect(names()).toEqual(['暖院旅宿','清风民宿','木居客栈','青瓦酒店','海外酒店']);
  fireEvent.change(screen.getByRole('combobox',{name:'排序方式'}),{target:{value:'price_high'}});
  expect(names()).toEqual(['木居客栈','暖院旅宿','清风民宿','青瓦酒店','海外酒店']);
  expect(hotels.map(h=>h.id)).toEqual([501,502,503,504,505]);
  expect(vi.mocked(hotelQuotes)).not.toHaveBeenCalled();
  expect(vi.mocked(travelPlaces)).not.toHaveBeenCalled();
  expect(vi.mocked(travelRoute)).not.toHaveBeenCalled();
 });
 it('filters by name or address and reference ceiling, shows exact count and empty reset without new requests',()=>{
  const {result,hotels}=setup();
  render(<HotelResults result={result} multi={null} hotels={hotels}/>);
  expect(screen.getByText('显示 5 / 5 家')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('searchbox',{name:'筛选名称或地址'}),{target:{value:' 成都 '}});
  expect(names()).toEqual(['木居客栈','暖院旅宿','清风民宿']);
  fireEvent.change(screen.getByRole('textbox',{name:'人民币参考价上限（元）'}),{target:{value:'150'}});
  expect(names()).toEqual(['暖院旅宿','清风民宿']);
  expect(screen.getByText('显示 2 / 5 家')).toBeInTheDocument();
  expect(screen.getByText(/仅依据已返回的人民币搜索参考价/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox',{name:'人民币参考价上限（元）'}),{target:{value:'错误'}});
  expect(screen.getByRole('alert')).toHaveTextContent('金额格式有误');
  expect(names()).toEqual(['木居客栈','暖院旅宿','清风民宿']);
  fireEvent.change(screen.getByRole('searchbox',{name:'筛选名称或地址'}),{target:{value:'不存在的店'}});
  expect(screen.getByText('本地筛选没有匹配的酒店')).toBeInTheDocument();
  expect(screen.getByText('显示 0 / 5 家')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'清除本地筛选'}));
  expect(names()).toEqual(['木居客栈','青瓦酒店','暖院旅宿','海外酒店','清风民宿']);
  expect(screen.queryByText('本地筛选没有匹配的酒店')).toBeNull();
  expect(screen.getByText('显示 5 / 5 家')).toBeInTheDocument();
  expect(vi.mocked(hotelQuotes)).not.toHaveBeenCalled();
 });
 it('keeps manually selected hotels available in comparison even when locally hidden',()=>{
  const {result,hotels}=setup();
  render(<HotelResults result={result} multi={null} hotels={hotels}/>);
  fireEvent.click(screen.getByRole('button',{name:'加入对比：木居客栈'}));
  fireEvent.change(screen.getByRole('searchbox',{name:'筛选名称或地址'}),{target:{value:'暖院'}});
  expect(names()).toEqual(['暖院旅宿']);
  const dock=screen.getByRole('region',{name:'酒店对比快捷栏'});
  expect(within(dock).getByText('1 / 3')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'加入对比：暖院旅宿'}));
  const comparison=screen.getByRole('region',{name:'酒店参考候选对比'});
  expect(within(comparison).getByText('木居客栈')).toBeInTheDocument();
  expect(within(comparison).getByText('暖院旅宿')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'清除本地筛选'}));
  expect(within(document.querySelector('.hotel-grid') as HTMLElement).getByRole('button',{name:'移出对比：木居客栈'})).toHaveAttribute('aria-pressed','true');
  expect(vi.mocked(hotelQuotes)).not.toHaveBeenCalled();
 });
 it('resets local view and compare choices on a different request identity',()=>{
  const {result,hotels}=setup();
  const {rerender}=render(<HotelResults result={result} multi={null} hotels={hotels}/>);
  fireEvent.change(screen.getByRole('combobox',{name:'排序方式'}),{target:{value:'price_low'}});
  fireEvent.change(screen.getByRole('searchbox',{name:'筛选名称或地址'}),{target:{value:'成都'}});
  fireEvent.click(screen.getByRole('button',{name:'加入对比：暖院旅宿'}));
  expect(screen.getByRole('region',{name:'酒店对比快捷栏'})).toBeInTheDocument();
  rerender(<HotelResults result={{...result,request_id:'new-independent-search'}} multi={null} hotels={hotels}/>);
  expect(screen.getByRole('combobox',{name:'排序方式'})).toHaveValue('source');
  expect(screen.getByRole('searchbox',{name:'筛选名称或地址'})).toHaveValue('');
  expect(names()).toEqual(['木居客栈','青瓦酒店','暖院旅宿','海外酒店','清风民宿']);
  expect(screen.queryByRole('region',{name:'酒店对比快捷栏'})).toBeNull();
 });
 it('keeps current local filters when more hotels appear in the same query response',()=>{
  const {result,hotels}=setup();
  const {rerender}=render(<HotelResults result={result} multi={null} hotels={hotels}/>);
  fireEvent.change(screen.getByRole('searchbox',{name:'筛选名称或地址'}),{target:{value:'成都'}});
  fireEvent.change(screen.getByRole('combobox',{name:'排序方式'}),{target:{value:'price_low'}});
  const extra:Hotel={...hotels[0],id:506,name:'榕荫民宿',reference_price_fen:14000};
  rerender(<HotelResults result={{...result,hotels:[...hotels,extra]}} multi={null} hotels={[...hotels,extra]}/>);
  expect(names()).toEqual(['榕荫民宿','暖院旅宿','清风民宿','木居客栈']);
  expect(screen.getByText('显示 4 / 6 家')).toBeInTheDocument();
 });
});
