import {beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import SearchForm from '../SearchForm';
import {chinaDate} from '../format';

beforeEach(()=>window.sessionStorage.clear());

describe('personal search-condition draft',()=>{
 it('saves valid dates, family and budget only on explicit click and restores without querying',()=>{
  const submit=vi.fn().mockResolvedValue(undefined);
  const first=render(<SearchForm loading={false} onSubmit={submit}/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'上海'}});
  fireEvent.change(screen.getByLabelText('入住日期'),{target:{value:chinaDate(3)}});
  fireEvent.change(screen.getByLabelText('退房日期'),{target:{value:chinaDate(5)}});
  fireEvent.change(screen.getByLabelText('每房成人'),{target:{value:'3'}});
  fireEvent.change(screen.getByLabelText('房间数量'),{target:{value:'2'}});
  fireEvent.change(screen.getByLabelText('每房儿童'),{target:{value:'1'}});
  fireEvent.change(screen.getByLabelText('儿童1年龄'),{target:{value:'8'}});
  fireEvent.change(screen.getByLabelText(/每晚预算参考/),{target:{value:'258.50'}});
  expect(window.sessionStorage.length).toBe(0);
  fireEvent.click(screen.getByRole('button',{name:'保存本次条件'}));
  expect(screen.getByText(/仅暂存在当前标签页/)).toBeInTheDocument();
  expect(submit).not.toHaveBeenCalled();
  const raw=JSON.stringify(window.sessionStorage);
  expect(raw).not.toContain('api_key');
  first.unmount();
  render(<SearchForm loading={false} onSubmit={submit}/>);
  expect(screen.getByLabelText('希望住在哪个地方附近？')).toHaveValue('');
  expect(screen.getByRole('button',{name:'恢复已保存条件'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'恢复已保存条件'}));
  expect(screen.getByLabelText('希望住在哪个地方附近？')).toHaveValue('上海');
  expect(screen.getByLabelText('入住日期')).toHaveValue(chinaDate(3));
  expect(screen.getByLabelText('退房日期')).toHaveValue(chinaDate(5));
  expect(screen.getByLabelText('每房成人')).toHaveValue('3');
  expect(screen.getByLabelText('房间数量')).toHaveValue('2');
  expect(screen.getByLabelText('儿童1年龄')).toHaveValue(8);
  expect(screen.getByLabelText(/每晚预算参考/)).toHaveValue('258.5');
  expect(submit).not.toHaveBeenCalled();
 });
 it('clears saved conditions without changing current unsent form',()=>{
  render(<SearchForm loading={false} onSubmit={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'保存本次条件'}));
  expect(screen.getByRole('button',{name:'清除已保存条件'})).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'杭州'}});
  fireEvent.click(screen.getByRole('button',{name:'清除已保存条件'}));
  expect(screen.queryByRole('button',{name:'恢复已保存条件'})).toBeNull();
  expect(screen.getByLabelText('希望住在哪个地方附近？')).toHaveValue('杭州');
  cleanup();
  render(<SearchForm loading={false} onSubmit={vi.fn()}/>);
  expect(screen.queryByRole('button',{name:'恢复已保存条件'})).toBeNull();
 });
 it('rejects a previously saved overseas draft instead of relabeling it mainland',()=>{
  const bad={
   place:'东京',place_type:'城市',country_region:'日本',
   check_in:chinaDate(1),check_out:chinaDate(2),adults:2,rooms:1,
   children_ages:[],budget_fen:null,limit:20,poi_name:null,waypoints:[],
   accepts_dormitory:false,
  };
  window.sessionStorage.setItem('xunqi:recent-search:v1',JSON.stringify({version:1,input:bad}));
  render(<SearchForm loading={false} onSubmit={vi.fn()}/>);
  expect(screen.queryByRole('button',{name:'恢复已保存条件'})).toBeNull();
  expect(screen.getByLabelText('希望住在哪个地方附近？')).toHaveValue('');
  expect(screen.getByText('中国大陆')).toBeInTheDocument();
 });
 it('rejects incomplete child ages and does not save or query',()=>{
  const submit=vi.fn();
  render(<SearchForm loading={false} onSubmit={submit}/>);
  fireEvent.change(screen.getByLabelText('每房儿童'),{target:{value:'1'}});
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.click(screen.getByRole('button',{name:'保存本次条件'}));
  expect(screen.getByRole('alert')).toHaveTextContent('儿童的年龄');
  expect(window.sessionStorage.length).toBe(0);
  expect(submit).not.toHaveBeenCalled();
 });
 it('ignores malformed and expired saved records instead of reviving stale travel dates',()=>{
  window.sessionStorage.setItem('xunqi:recent-search:v1','{not-json');
  const malformed=render(<SearchForm loading={false} onSubmit={vi.fn()}/>);
  expect(screen.queryByRole('button',{name:'恢复已保存条件'})).toBeNull();
  malformed.unmount();
  window.sessionStorage.setItem('xunqi:recent-search:v1',JSON.stringify({
   version:1,input:{
    place:'成都',place_type:'城市',check_in:'2020-01-01',check_out:'2020-01-02',
    adults:2,rooms:1,children_ages:[],budget_fen:null,limit:20,poi_name:null,
    accepts_dormitory:false,
   },
  }));
  render(<SearchForm loading={false} onSubmit={vi.fn()}/>);
  expect(screen.queryByRole('button',{name:'恢复已保存条件'})).toBeNull();
  expect(screen.getByLabelText('希望住在哪个地方附近？')).toHaveValue('');
 });
});
