import {render,screen,fireEvent,waitFor,act} from '@testing-library/react';
import {beforeEach,it,expect,vi} from 'vitest';
import NaturalQuery from '../NaturalQuery';
import {createConversation,getConversation,interpretConditions,cancelInterpretation} from '../api';
import {conversationFixture} from './conversation-fixtures';
import type {ConversationState} from '../types';
vi.mock('../api',()=>({createConversation:vi.fn(),getConversation:vi.fn(),interpretConditions:vi.fn(),cancelInterpretation:vi.fn()}));
beforeEach(()=>{vi.resetAllMocks();vi.mocked(createConversation).mockResolvedValue(conversationFixture());vi.mocked(interpretConditions).mockResolvedValue(conversationFixture('succeeded'));vi.mocked(cancelInterpretation).mockResolvedValue(conversationFixture('cancelled'));});
function type(message='广州，明天1晚，每房2成人1房无儿童，每晚200元'){fireEvent.change(screen.getByLabelText('回复小助手（一次补充一点即可）'),{target:{value:message}});}
it('prepares a draft and only hands it to the form on explicit confirmation',async()=>{
 const apply=vi.fn();render(<NaturalQuery onApply={apply}/>);expect(createConversation).not.toHaveBeenCalled();
 type();fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await screen.findByText('已整理的条件');await waitFor(()=>expect(screen.getByRole('button',{name:'核对后填入查询表单'})).toBeEnabled());
 expect(apply).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'核对后填入查询表单'}));
 expect(apply).toHaveBeenCalledWith(expect.objectContaining({place:'广州',budget_fen:20000,rooms:1}),{visits:[],restaurantAnchor:null,stayAnchor:null});
 expect(screen.queryByRole('button',{name:/GPT/})).toBeNull();
 expect(screen.getByText(/你的输入会发送至 DeepSeek/)).toBeInTheDocument();
});
it('restores a committed checkpoint draft and continues from its revision',async()=>{
 const resumed={...conversationFixture('succeeded'),revision:3,generation:2,client_sequence:1};
 const apply=vi.fn();render(<NaturalQuery onApply={apply} resumeState={resumed}/>);
 await screen.findByText('已整理的条件');
 type('改日期');fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await waitFor(()=>expect(interpretConditions).toHaveBeenCalledWith(
  'test-conversation',
  expect.objectContaining({expected_revision:3,client_sequence:2}),
  expect.any(AbortSignal),
 ));
 expect(createConversation).not.toHaveBeenCalled();
});
it('stop notifies the backend and ignores a late completed response',async()=>{
 let finish!:(value:ConversationState)=>void;
 vi.mocked(interpretConditions).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 render(<NaturalQuery onApply={vi.fn()}/>);type();fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await waitFor(()=>expect(interpretConditions).toHaveBeenCalled());
 fireEvent.click(screen.getByRole('button',{name:'停止理解'}));
 await screen.findByText(/已停止/);
 await act(async()=>finish(conversationFixture('succeeded')));
 expect(screen.queryByText('¥200/房晚')).toBeNull();
 expect(cancelInterpretation).toHaveBeenCalledWith('test-conversation',expect.objectContaining({client_sequence:1}));
});
it('replacement preserves pending user intent and rejects older polling results',async()=>{
 let finish!:(value:ConversationState)=>void;
 vi.mocked(interpretConditions).mockResolvedValueOnce({...conversationFixture('queued'),operation_id:'old'}).mockResolvedValueOnce(conversationFixture('succeeded','杭州'));
 vi.mocked(getConversation).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 render(<NaturalQuery onApply={vi.fn()}/>);type('原始广州条件');fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await waitFor(()=>expect(getConversation).toHaveBeenCalled());
 type('改去杭州');fireEvent.click(screen.getByRole('button',{name:'用新回答替换'}));
 await screen.findByText('杭州 · 城市');
 expect(interpretConditions).toHaveBeenLastCalledWith('test-conversation',expect.objectContaining({client_sequence:2,user_inputs:['原始广州条件','改去杭州']}),expect.any(AbortSignal));
 await act(async()=>finish(conversationFixture('succeeded','广州')));
 expect(screen.getByText('杭州 · 城市')).toBeInTheDocument();expect(screen.queryByText('广州 · 城市')).toBeNull();
});
it('unhandled conditions require acknowledgement and new text blocks stale handoff',async()=>{
 const data=conversationFixture('succeeded');data.draft.notes=['独立卫浴'];vi.mocked(interpretConditions).mockResolvedValue(data);
 render(<NaturalQuery onApply={vi.fn()}/>);type();fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await screen.findByText('尚未自动用于筛选');
 const apply=screen.getByRole('button',{name:'核对后填入查询表单'});expect(apply).toBeDisabled();
 fireEvent.click(screen.getByRole('checkbox',{name:/我知道这些附加条件/}));expect(apply).toBeEnabled();
 type('改日期');expect(apply).toBeDisabled();
});
it('revision conflict synchronizes known state but retains the new command for resending',async()=>{
 vi.mocked(interpretConditions).mockRejectedValue(new Error('条件版本已更新'));
 vi.mocked(getConversation).mockResolvedValue(conversationFixture('succeeded'));
 render(<NaturalQuery onApply={vi.fn()}/>);type('改去杭州');fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await screen.findByRole('alert');await screen.findByText('广州 · 城市');
 expect(screen.getByLabelText('回复小助手（一次补充一点即可）')).toHaveValue('改去杭州');
 expect(screen.getByRole('button',{name:'核对后填入查询表单'})).toBeDisabled();
});


it('shows the distinct quota explanation returned by the backend without applying a draft',async()=>{
 const failed=conversationFixture('failed');
 failed.error={code:'INSUFFICIENT_BALANCE',message:'DeepSeek账户余额不足，未应用新条件；请查看供应商控制台的余额状态。'};
 vi.mocked(interpretConditions).mockResolvedValue(failed);
 render(<NaturalQuery onApply={vi.fn()}/>);type();fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await screen.findByText(/账户余额不足/);
 expect(screen.queryByRole('button',{name:'核对后填入查询表单'})).toBeNull();
});

it('treats connection loss as unknown backend status, not a completed cancellation',async()=>{
 vi.mocked(interpretConditions).mockRejectedValue(new TypeError('Failed to fetch'));
 vi.mocked(getConversation).mockRejectedValue(new TypeError('Failed to fetch'));
 render(<NaturalQuery onApply={vi.fn()}/>);type('改去深圳');
 fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await screen.findByRole('alert');
 expect(screen.getByRole('alert')).toHaveTextContent('后台是否完成尚未确认');
 expect(screen.getByRole('alert')).not.toHaveTextContent('Failed to fetch');
 expect(screen.getByLabelText('回复小助手（一次补充一点即可）')).toHaveValue('改去深圳');
});

it('collects a stay across separate answers, preserving the committed draft and requiring explicit sends',async()=>{
 const partial=conversationFixture('succeeded');
 partial.query=null;
 partial.draft={...partial.draft,check_in:null,check_out:null,nights:null,rooms:null,adults:null,children_count:null,children_ages:null,budget_fen:null,budget_basis:null};
 partial.missing=['入住日期','退房日期或晚数','房间数','每房成人数','每房儿童人数'];
 const ready={...conversationFixture('succeeded'),revision:2,client_sequence:2};
 vi.mocked(interpretConditions).mockResolvedValueOnce(partial).mockResolvedValueOnce(ready);
 const apply=vi.fn();render(<NaturalQuery onApply={apply}/>);
 expect(screen.getByText('先告诉我，想去哪里住？')).toBeInTheDocument();
 expect(screen.getByText('0/4 项已整理')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'广州'}));
 expect(screen.getByLabelText('回复小助手（一次补充一点即可）')).toHaveValue('广州');
 expect(interpretConditions).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await screen.findByText('打算哪天入住、住几晚？');
 expect(screen.getByText('1/4 项已整理')).toBeInTheDocument();
 expect(screen.getByText('广州 · 城市')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'核对后填入查询表单'})).toBeNull();
 expect(screen.getByText('还差几项基本信息。整理齐后，就能一键填入查询表单。')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'明天入住1晚'}));
 expect(screen.getByLabelText('回复小助手（一次补充一点即可）')).toHaveValue('明天入住1晚');
 expect(interpretConditions).toHaveBeenCalledTimes(1);
 const remaining='11月20日入住1晚，1间房，每房2成人，没有儿童，每房每晚200元以内';
 type(remaining);
 fireEvent.click(screen.getByRole('button',{name:'发送这条补充'}));
 await screen.findByText('基本信息都齐了，可以填入查询表单啦。');
 expect(screen.getByText('4/4 项已整理')).toBeInTheDocument();
 expect(interpretConditions).toHaveBeenLastCalledWith('test-conversation',
  expect.objectContaining({expected_revision:1,client_sequence:2,user_inputs:[remaining]}),
  expect.any(AbortSignal));
 expect(apply).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'核对后填入查询表单'}));
 expect(apply).toHaveBeenCalledTimes(1);
});
