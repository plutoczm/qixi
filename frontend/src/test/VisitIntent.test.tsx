import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {beforeEach,it,expect,vi} from 'vitest';
import NaturalQuery from '../NaturalQuery';
import SearchForm from '../SearchForm';
import {naturalGuidance} from '../naturalGuidance';
import {selectedRestaurant} from '../tripIntent';
import {conversationFixture} from './conversation-fixtures';
import {createConversation,interpretConditions,cancelInterpretation} from '../api';
import type {PlannedVisit} from '../types';

vi.mock('../api',()=>({createConversation:vi.fn(),getConversation:vi.fn(),interpretConditions:vi.fn(),cancelInterpretation:vi.fn()}));
const visits:PlannedVisit[]=[
 {name:'海底捞',category:'餐饮',area_hint:'荔胜广场',stay_near:true},
 {name:'广州塔',category:'景点',area_hint:null,stay_near:false},
];
beforeEach(()=>{
 vi.resetAllMocks();
 vi.mocked(createConversation).mockResolvedValue(conversationFixture());
 vi.mocked(cancelInterpretation).mockResolvedValue(conversationFixture('cancelled'));
});

it('asks for the city when the user only supplied a restaurant intent',()=>{
 const state=conversationFixture('succeeded');
 state.query=null;state.draft={...state.draft,place:null,place_type:null,visits:[visits[0]]};
 state.missing=['目的地'];
 const guide=naturalGuidance(state);
 expect(guide.question).toContain('海底捞');
 expect(guide.question).toContain('城市');
 expect(guide.ready).toBe(false);
});

it('rejects non-restaurant anchors and requires explicit restaurant selection',()=>{
 expect(selectedRestaurant(visits,'广州塔')).toBeNull();
 expect(selectedRestaurant(visits,'海底捞')).toBe('海底捞');
 expect(selectedRestaurant(visits,null)).toBeNull();
});

it('shows model-structured destinations and hands a chosen restaurant to the form only on user action',async()=>{
 const result=conversationFixture('succeeded');result.draft={...result.draft,visits};
 vi.mocked(interpretConditions).mockResolvedValue(result);
 const apply=vi.fn();render(<NaturalQuery onApply={apply}/>);
 fireEvent.change(screen.getByLabelText('回复小助手（一次补充一点即可）'),{target:{value:'广州海底捞吃火锅，还要去广州塔'}});
 fireEvent.click(screen.getByRole('button',{name:'发送给小助手'}));
 await screen.findByText('你计划去的地方');
 expect(screen.getByText('海底捞')).toBeInTheDocument();
 expect(screen.getByText('广州塔')).toBeInTheDocument();
 expect(screen.getByText(/具体分店由你稍后核对|尚未核验/)).toBeInTheDocument();
 expect(apply).not.toHaveBeenCalled();
 const select=screen.getByLabelText('这次希望住在哪个地点附近？');
 expect(select).toHaveValue('');
 fireEvent.change(select,{target:{value:'海底捞'}});
 expect(apply).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'核对后填入查询表单'}));
 expect(apply).toHaveBeenCalledWith(
  expect.objectContaining({place:'广州'}),
  {visits,restaurantAnchor:null,stayAnchor:'海底捞'},
 );
});

it('unselected or unsupported visit never silently activates restaurant recall',()=>{
 const submit=vi.fn().mockResolvedValue(undefined),recall=vi.fn().mockResolvedValue(undefined);
 const {rerender}=render(<SearchForm loading={false} onSubmit={submit} onRecall={recall}
   trip={{visits,restaurantAnchor:null}}/>);
 expect(screen.getByRole('checkbox',{name:'使用餐饮门店专项工具（可选）'})).not.toBeChecked();
 expect(recall).not.toHaveBeenCalled();
 rerender(<SearchForm loading={false} onSubmit={submit} onRecall={recall}
   trip={{visits,restaurantAnchor:'海底捞'}}/>);
 expect(screen.getByRole('checkbox',{name:'使用餐饮门店专项工具（可选）'})).toBeChecked();
 expect(screen.getByLabelText('餐厅关键词')).toHaveValue('海底捞');
 expect(recall).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
 fireEvent.click(screen.getByRole('button',{name:/查询房价/}));
 expect(recall).toHaveBeenCalledWith(expect.objectContaining({place:'广州'}),
  expect.objectContaining({restaurant_keyword:'海底捞',strategy:'branch_place',branch_limit:3}));
 expect(submit).not.toHaveBeenCalled();
});

it('unsupported source keeps the visit visible but never pretends it can recall branches',async()=>{
 const submit=vi.fn().mockResolvedValue(undefined);
 render(<SearchForm loading={false} sourceName="途牛" onSubmit={submit}
   trip={{visits,restaurantAnchor:'海底捞'}}/>);
 expect(screen.queryByRole('checkbox',{name:'使用餐饮门店专项工具（可选）'})).toBeNull();
 expect(screen.getByLabelText('AI整理的行程地点')).toHaveTextContent('没有未经确认就自动替换住宿目的地');
 fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
 fireEvent.click(screen.getByRole('button',{name:/查询房价/}));
 await waitFor(()=>expect(submit).toHaveBeenCalledTimes(1));
 expect(submit.mock.calls[0][0]).toMatchObject({place:'广州',poi_name:null});
});
