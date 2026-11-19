import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import SearchForm from '../SearchForm';

const today=new Date().toISOString().slice(0,10);
const quotes=[
 {base:'CNY',quote:'USD',rate:0.14,date:today},
 {base:'CNY',quote:'HKD',rate:1.09,date:today},
 {base:'CNY',quote:'EUR',rate:0.13,date:today},
 {base:'CNY',quote:'JPY',rate:22,date:today},
];
beforeEach(()=>vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>quotes})));
afterEach(()=>vi.unstubAllGlobals());

describe('warm budget input, reference FX and search safety',()=>{
 it('shows a separate responsive budget area, with no FX calls or limits when left blank',async()=>{
  const submit=vi.fn().mockResolvedValue(undefined);
  const {container}=render(<SearchForm loading={false} onSubmit={submit}/>);
  expect(container.querySelector('.budget-studio')).not.toBeNull();
  expect(screen.getByLabelText('预算金额')).toHaveValue('');
  expect(screen.getByText(/不填则不限预算/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/查看参考换算/})).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.submit(screen.getByRole('form'));
  await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({budget_fen:null})));
  expect(fetch).not.toHaveBeenCalled();
 });
 it('only requests reference FX when explicitly opening conversion, without searching hotels',async()=>{
  const submit=vi.fn();
  render(<SearchForm loading={false} onSubmit={submit}/>);
  fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'200'}});
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:/查看参考换算/}));
  await screen.findByText(/USD/);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/2026|20\d\d/)).toBeInTheDocument();
  expect(submit).not.toHaveBeenCalled();
  expect(screen.getByText(/预算上限，不是最终房价/)).toBeInTheDocument();
 });
 it('converts explicit USD budget into CNY cents only after a trusted reference rate is available',async()=>{
  const submit=vi.fn().mockResolvedValue(undefined);
  render(<SearchForm loading={false} onSubmit={submit}/>);
  fireEvent.change(screen.getByLabelText('预算币种'),{target:{value:'USD'}});
  expect(screen.getByLabelText('预算金额')).toHaveValue('');
  await screen.findByText(/参考汇率日期/);
  fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'100'}});
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.submit(screen.getByRole('form'));
  await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({budget_fen:71428})));
 });
 it('prevents submitting unsupported FX and lets user switch back to CNY without guessing',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('network offline')));
  const submit=vi.fn().mockResolvedValue(undefined);
  render(<SearchForm loading={false} onSubmit={submit}/>);
  fireEvent.change(screen.getByLabelText('预算币种'),{target:{value:'HKD'}});
  await screen.findByText(/汇率暂时不可用/);
  fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'300'}});
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.submit(screen.getByRole('form'));
  expect(submit).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/汇率/);
  fireEvent.change(screen.getByLabelText('预算币种'),{target:{value:'CNY'}});
  expect(screen.getByLabelText('预算金额')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'300'}});
  fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
  fireEvent.submit(screen.getByRole('form'));
  await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({budget_fen:30000})));
 });
});
