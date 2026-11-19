import {render,screen,fireEvent,waitFor,within,act} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import BranchWalkPanel from '../BranchWalkPanel';
import {branchWalkBatch,branchWalkRoute,cancelBranchWalk} from '../api';
import {walkRecallFixture,walkBatchFixture} from './walk-fixtures';
import type {BranchWalkBatchResponse} from '../types';
vi.mock('../api',()=>({branchWalkBatch:vi.fn(),branchWalkRoute:vi.fn(),cancelBranchWalk:vi.fn()}));
beforeEach(()=>{vi.resetAllMocks();vi.mocked(cancelBranchWalk).mockResolvedValue({accepted:true});vi.mocked(branchWalkBatch).mockImplementation(async input=>walkBatchFixture(input));});
async function start(){fireEvent.click(screen.getByRole('button',{name:'批量核对位置与交通（最多3组）'}));await screen.findByText(/第1至3组/);}
it('requires both identity confirmations and never queries a route just from a recall association',async()=>{
 render(<BranchWalkPanel data={walkRecallFixture()}/>);expect(branchWalkBatch).not.toHaveBeenCalled();await start();
 const pair=screen.getByRole('article',{name:'测试餐厅1店与测试住宿1的交通核对'});
 const view=within(pair);expect(branchWalkRoute).not.toHaveBeenCalled();
 fireEvent.change(view.getByLabelText('住宿地图地点'),{target:{value:'h101'}});
 fireEvent.change(view.getByLabelText('召回门店地图地点'),{target:{value:'r101'}});
 fireEvent.click(view.getByLabelText('我已核对所选地点对应原道旅住宿'));
 expect(view.getByRole('button',{name:'确认并查询交通路线'})).toBeDisabled();
 fireEvent.click(view.getByLabelText('我已核对所选地点对应原召回门店'));
 expect(view.getByRole('button',{name:'确认并查询交通路线'})).toBeEnabled();
});
it('shows walking distance OR driving duration without inventing taxi pickup or inventory',async()=>{
 vi.mocked(branchWalkBatch).mockImplementation(async input=>walkBatchFixture(input,true));
 render(<BranchWalkPanel data={walkRecallFixture()}/>);await start();
 expect(screen.getAllByText(/符合步行1500米或驾车15分钟任一条件/)).toHaveLength(2);
 expect(screen.getByText(/步行与驾车均超出限制/)).toBeInTheDocument();
 expect(screen.getByText(/步行路线：测试餐厅1店 → 测试住宿1 · 900米/)).toBeInTheDocument();
 expect(screen.getByText(/步行路线：测试餐厅2店 → 测试住宿2 · 1600米/)).toBeInTheDocument();
 expect(screen.getByText(/驾车预计：测试餐厅2店 → 测试住宿2 · 3200米 \/ 900秒/)).toBeInTheDocument();
 expect(screen.getAllByText(/不包含打车接驾\/候车/)).toHaveLength(3);
});
it('expired points disable reuse and recheck resets identity choices',async()=>{
 vi.mocked(branchWalkBatch).mockImplementationOnce(async input=>{const data=walkBatchFixture(input);data.pairs.forEach(p=>{p.expires_in_seconds=0;});return data;}).mockImplementation(async input=>walkBatchFixture(input));
 render(<BranchWalkPanel data={walkRecallFixture()}/>);await start();
 expect(screen.getAllByText(/本组点位已过期/)).toHaveLength(3);
 fireEvent.click(screen.getByRole('button',{name:'重新核对本组点位'}));
 await waitFor(()=>expect(branchWalkBatch).toHaveBeenCalledTimes(2));
 await waitFor(()=>expect(screen.queryByText(/本组点位已过期/)).toBeNull());
 expect(screen.getAllByLabelText('住宿地图地点')[0]).toHaveValue('');
 expect(branchWalkRoute).not.toHaveBeenCalled();
});
it('stop rejects a late response and asks the backend to cancel',async()=>{
 let finish!:(value:BranchWalkBatchResponse)=>void;
 vi.mocked(branchWalkBatch).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 render(<BranchWalkPanel data={walkRecallFixture()}/>);
 fireEvent.click(screen.getByRole('button',{name:'批量核对位置与交通（最多3组）'}));
 await waitFor(()=>expect(branchWalkBatch).toHaveBeenCalled());
 const input=vi.mocked(branchWalkBatch).mock.calls[0][0];
 fireEvent.click(screen.getByRole('button',{name:'停止本轮核对'}));
 await act(async()=>finish(walkBatchFixture(input,true)));
 expect(screen.queryByText(/餐厅→住宿交通子指标/)).toBeNull();expect(cancelBranchWalk).toHaveBeenCalledWith(input.operation_id);
});
it('a failed refresh clears previous walk values and cannot attach another pair',async()=>{
 vi.mocked(branchWalkBatch).mockImplementation(async input=>walkBatchFixture(input,true));
 vi.mocked(branchWalkRoute).mockRejectedValue(new Error('来源超时'));
 render(<BranchWalkPanel data={walkRecallFixture()}/>);await start();
 const pair=within(screen.getByRole('article',{name:'测试餐厅1店与测试住宿1的交通核对'}));
 fireEvent.click(pair.getByRole('button',{name:'刷新交通路线'}));
 await screen.findByRole('alert');
 expect(pair.queryByText(/餐厅→住宿交通子指标/)).toBeNull();
 expect(screen.getAllByText(/餐厅→住宿交通子指标/)).toHaveLength(2);
});
it('next group explicitly queries fresh maps and stops old context',async()=>{
 render(<BranchWalkPanel data={walkRecallFixture()}/>);await start();
 fireEvent.click(screen.getByRole('button',{name:'核对下一组（会新查地图）'}));
 await screen.findByText(/第4至6组/);
 expect(branchWalkBatch).toHaveBeenLastCalledWith(expect.objectContaining({offset:3}),expect.any(AbortSignal));
 expect(cancelBranchWalk).toHaveBeenCalledTimes(1);
});


it('keeps the recalled map-hotel reference visible without asserting confirmed identity',async()=>{
 vi.mocked(branchWalkBatch).mockImplementation(async input=>{
  const data=walkBatchFixture(input);data.pairs[0].recall_hotel={id:'target-reference',name:'原地图酒店本体',address:'完整参考地址1号',city:'广州',name_match:null,address_match:null,city_match:null};return data;
 });
 render(<BranchWalkPanel data={walkRecallFixture()}/>);await start();
 expect(screen.getByText(/召回时地图酒店参考：原地图酒店本体/)).toBeInTheDocument();
 expect(branchWalkRoute).not.toHaveBeenCalled();
});
