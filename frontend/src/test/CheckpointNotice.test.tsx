import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import CheckpointNotice from '../CheckpointNotice';
import {checkpointState,resumeCheckpoint,saveCheckpoint} from '../api';
import type {CheckpointState} from '../types';

vi.mock('../api',()=>({checkpointState:vi.fn(),saveCheckpoint:vi.fn(),resumeCheckpoint:vi.fn()}));

const saved:CheckpointState={
  connected:false,status:'disconnected',disconnected_at:'2026-11-09T10:00:00Z',
  checkpoint_id:'break-demo',app_commit:'abc123',saved_at:'2026-11-09T10:00:00Z',
  search_session_count:1,conversation_count:1,resume_available:true,
};
const resumed={status:'ready' as const,checkpoint_id:'break-demo',saved_at:'2026-11-09T10:00:00Z',
  disconnected_at:'2026-11-09T10:00:00Z',search:null,conversation:null,warnings:[],error:null};

beforeEach(()=>{
  vi.resetAllMocks();
  vi.mocked(checkpointState).mockRejectedValue(new Error('connection lost'));
  vi.mocked(saveCheckpoint).mockResolvedValue({status:'saved',checkpoint:saved});
  vi.mocked(resumeCheckpoint).mockResolvedValue(resumed);
});

it('saves a checkpoint and labels the disconnection moment',async()=>{
  const resume=vi.fn();
  render(<CheckpointNotice offline={true} onResume={resume}/>);
  await screen.findByText(/已断开 · 保存断点后恢复使用/);
  fireEvent.click(screen.getByRole('button',{name:'保存断点并标记断时'}));
  expect(await screen.findByText(/断点已保存 · 等待恢复连接/)).toBeInTheDocument();
  expect(screen.getByText(/断时记录/)).toBeInTheDocument();
  expect(screen.getByText(/断点：break-demo/)).toBeInTheDocument();
  expect(saveCheckpoint).toHaveBeenCalledOnce();
  expect(resume).not.toHaveBeenCalled();
});

it('offers a resume action after reconnect and passes the validated state to the app',async()=>{
  const resume=vi.fn();
  vi.mocked(checkpointState).mockResolvedValue({...saved,connected:true,status:'connected'});
  render(<CheckpointNotice offline={true} onResume={resume}/>);
  await screen.findByText(/已恢复连接 · 发现断时记录/);
  fireEvent.click(screen.getByRole('button',{name:'继续上次断点'}));
  await waitFor(()=>expect(resume).toHaveBeenCalledWith(resumed));
  expect(resumeCheckpoint).toHaveBeenCalledOnce();
});
