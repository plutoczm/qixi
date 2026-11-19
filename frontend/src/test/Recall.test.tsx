import {render,screen,fireEvent,waitFor,act} from '@testing-library/react';
import {beforeEach,it,expect,vi} from 'vitest';
import App from '../App';
import {health,recallHotels,cancelRecall,continueRecall} from '../api';
import {fixture,mapFixture,continuationFixture} from './recall-fixtures';
import type {RecallResponse} from '../types';
vi.mock('../api',()=>({health:vi.fn(),recallHotels:vi.fn(),cancelRecall:vi.fn(),continueRecall:vi.fn(),searchHotels:vi.fn(),hotelQuotes:vi.fn(),travelPlaces:vi.fn(),travelRoute:vi.fn(),checkpointState:vi.fn(),saveCheckpoint:vi.fn(),resumeCheckpoint:vi.fn()}));
beforeEach(()=>{vi.resetAllMocks();vi.mocked(health).mockResolvedValue({status:'ok',provider:'rollinggo',credential_configured:true,web_ready:true,today_shanghai:'2026-10-18',features:{maps:true,fare:true,llm:true,multi_branch_recall:true}, data_mode:'live', dataset:null, channels:{}});vi.mocked(cancelRecall).mockResolvedValue({accepted:true});});
async function start(){fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});fireEvent.click(await screen.findByLabelText('使用餐饮门店专项工具（可选）'));fireEvent.click(screen.getByRole('button',{name:'查询房价'}));}
it('submits identical stay context and filters an explicit branch without another query',async()=>{
 vi.mocked(recallHotels).mockResolvedValue(fixture());render(<App/>);await start();
 await screen.findByText('第二门店住宿');
 expect(recallHotels).toHaveBeenCalledWith(expect.objectContaining({provider:'amap',restaurant_keyword:'海底捞',branch_limit:3,query:expect.objectContaining({place:'广州',rooms:1})}),expect.any(AbortSignal));
 expect(screen.getByText(/达到2页上限/)).toBeInTheDocument();expect(screen.getByText('达到查询截止')).toBeInTheDocument();
 fireEvent.click(screen.getAllByRole('button',{name:'查看该门店候选'})[0]);
 expect(screen.queryByText('第二门店住宿')).toBeNull();expect(recallHotels).toHaveBeenCalledTimes(1);
 fireEvent.click(screen.getByRole('button',{name:/查看候选并集/}));expect(screen.getByText('第二门店住宿')).toBeInTheDocument();
});
it('stop notifies backend and rejects a late recall completion',async()=>{
 let finish!:(value:RecallResponse)=>void;
 vi.mocked(recallHotels).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 render(<App/>);await start();await waitFor(()=>expect(recallHotels).toHaveBeenCalled());
 fireEvent.click(screen.getByRole('button',{name:'停止查询'}));
 await waitFor(()=>expect(cancelRecall).toHaveBeenCalled());
 await act(async()=>finish(fixture()));
 expect(screen.queryByText('第二门店住宿')).toBeNull();
 expect(screen.getByText(/已请求停止/)).toBeInTheDocument();
});
it('all source failures retain branch coverage and an unavailable message',async()=>{
 const data=fixture();data.status='unavailable';data.hotels=[];data.error={code:'TIMEOUT',message:'报价来源查询超时'};
 data.branches[0].status='unavailable';data.branches[0].error=data.error;
 vi.mocked(recallHotels).mockResolvedValue(data);render(<App/>);await start();
 await screen.findByText('抱歉，这次搜索没有完成');
 expect(screen.getByRole('region',{name:'多门店覆盖记录'})).toBeInTheDocument();
 expect(screen.getByText('测试数据 · 仅用于界面验证')).toBeInTheDocument();
 expect(screen.getByText('查询失败')).toBeInTheDocument();
});

it('identical branch sets are explicitly a location uncertainty, not proof of nearby hotels',async()=>{
 const data=fixture();data.recall_coverage.identical_branch_sets=2;
 vi.mocked(recallHotels).mockResolvedValue(data);render(<App/>);await start();
 expect(await screen.findByText(/不同门店返回完全相同的住宿集合/)).toBeInTheDocument();
 expect(recallHotels).toHaveBeenCalledTimes(1);
});


it('map-first option submits a global budget strategy and exposes excluded and unqueried targets',async()=>{
 vi.mocked(recallHotels).mockResolvedValue(mapFixture());render(<App/>);
 fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});fireEvent.click(await screen.findByLabelText('使用餐饮门店专项工具（可选）'));
 expect(screen.getByLabelText('候选查找方式')).toHaveValue('branch_place');
 fireEvent.change(screen.getByLabelText('候选查找方式'),{target:{value:'map_hotels'}});
 fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
 await screen.findByText(/本轮查名5\/5次/);
 expect(recallHotels).toHaveBeenCalledWith(expect.objectContaining({strategy:'map_hotels',branch_limit:3}),expect.any(AbortSignal));
 fireEvent.click(screen.getByText(/地图酒店与查名记录/));
 expect(screen.getByText('其他酒店已排除')).toBeInTheDocument();
 expect(screen.getByText('本轮5次查名预算已用完')).toBeInTheDocument();
 expect(screen.queryByText('步行已达标')).toBeNull();
});

it('explicit empty-result actions keep map-first strategy instead of falling back',async()=>{
 const data=mapFixture();data.hotels=[];data.context.limit=6;data.context.budget_fen=20000;
 vi.mocked(recallHotels).mockResolvedValue(data);render(<App/>);
 fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});fireEvent.click(await screen.findByLabelText('使用餐饮门店专项工具（可选）'));
 fireEvent.change(screen.getByLabelText('候选查找方式'),{target:{value:'map_hotels'}});
 fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
 await screen.findByText('这次条件下，暂未查到住宿');
 fireEvent.click(screen.getByRole('button',{name:'扩大到20家再查'}));
 await waitFor(()=>expect(recallHotels).toHaveBeenCalledTimes(2));
 expect(vi.mocked(recallHotels).mock.calls[1][0]).toEqual(expect.objectContaining({strategy:'map_hotels',query:expect.objectContaining({limit:20})}));
 await screen.findByText('这次条件下，暂未查到住宿');
 fireEvent.click(screen.getByRole('button',{name:'清除预算再查一次'}));
 await waitFor(()=>expect(recallHotels).toHaveBeenCalledTimes(3));
 expect(vi.mocked(recallHotels).mock.calls[2][0]).toEqual(expect.objectContaining({strategy:'map_hotels',query:expect.objectContaining({budget_fen:null})}));
});

async function startMap(){
 fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});fireEvent.click(await screen.findByLabelText('使用餐饮门店专项工具（可选）'));
 fireEvent.change(screen.getByLabelText('候选查找方式'),{target:{value:'map_hotels'}});
 fireEvent.click(screen.getByRole('button',{name:'查询房价'}));
 await screen.findByText('第二门店住宿');
}
it('continues only on click and retains previous candidates and observation times',async()=>{
 const first=mapFixture();vi.mocked(recallHotels).mockResolvedValue(first);
 vi.mocked(continueRecall).mockImplementation(async input=>continuationFixture(first,input.operation_id));
 render(<App/>);await startMap();expect(continueRecall).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'继续查下一轮（最多5次）'}));
 await screen.findByText('续查新增住宿');
 expect(screen.getByText('第二门店住宿')).toBeInTheDocument();
 expect(continueRecall).toHaveBeenCalledWith(expect.objectContaining({request_id:first.request_id,continuation_cursor:first.continuation_cursor}),expect.any(AbortSignal));
 expect(Object.keys(vi.mocked(continueRecall).mock.calls[0][0]).sort()).toEqual(['continuation_cursor','operation_id','request_id']);
 expect(screen.getByText(/累计10次（第2轮）/)).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'继续查下一轮（最多5次）'})).toBeNull();
});
it('stopping continuation keeps old results and ignores a late delta',async()=>{
 const first=mapFixture();let release!:(value:RecallResponse)=>void;let operation='';
 vi.mocked(recallHotels).mockResolvedValue(first);
 vi.mocked(continueRecall).mockImplementation(input=>{operation=input.operation_id;return new Promise(resolve=>{release=resolve;});});
 render(<App/>);await startMap();
 fireEvent.click(screen.getByRole('button',{name:'继续查下一轮（最多5次）'}));
 expect(screen.getByText('第二门店住宿')).toBeInTheDocument();
 await screen.findByText('正在查找更多酒店');
 fireEvent.click(screen.getByRole('button',{name:'停止查询'}));
 await waitFor(()=>expect(cancelRecall).toHaveBeenCalledWith(operation));
 await act(async()=>release(continuationFixture(first,operation)));
 expect(screen.queryByText('续查新增住宿')).toBeNull();
 expect(screen.getByText('第二门店住宿')).toBeInTheDocument();
 expect(screen.getByText(/已停止续查，原结果保留/)).toBeInTheDocument();
});
it('stale continuation disables the cursor while retaining old prices',async()=>{
 vi.mocked(recallHotels).mockResolvedValue(mapFixture());
 vi.mocked(continueRecall).mockRejectedValue(Object.assign(new Error('续查状态已变化'),{code:'CONTINUATION_STALE'}));
 render(<App/>);await startMap();
 fireEvent.click(screen.getByRole('button',{name:'继续查下一轮（最多5次）'}));
 await screen.findByText(/续查状态已变化/);
 expect(screen.getByText('第二门店住宿')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'继续查下一轮（最多5次）'})).toBeNull();
});
it('expired continuation is disabled without a source request',async()=>{
 const first=mapFixture();first.continuation_expires_at=new Date(Date.now()-1000).toISOString();
 vi.mocked(recallHotels).mockResolvedValue(first);render(<App/>);await startMap();
 expect(screen.getByRole('button',{name:'继续查下一轮（最多5次）'})).toBeDisabled();
 expect(screen.getByText(/续查队列已过期/)).toBeInTheDocument();
 expect(continueRecall).not.toHaveBeenCalled();
});
