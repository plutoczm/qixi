import {useEffect,useState} from 'react';
import {checkpointState,resumeCheckpoint,saveCheckpoint} from './api';
import {timeText} from './format';
import type {CheckpointResume,CheckpointState} from './types';

type Props={offline:boolean;onResume:(data:CheckpointResume)=>void};

export default function CheckpointNotice({offline,onResume}:Props) {
  const [state,setState]=useState<CheckpointState|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [warning,setWarning]=useState('');
  useEffect(()=>{
   if(!offline)return;
   let mounted=true;
   async function load(){
    try{
     const value=await checkpointState();
     if(mounted)setState(value);
    }catch{
     if(mounted)setState({connected:false,status:'disconnected',disconnected_at:null});
    }
   }
   void load();
   const timer=window.setInterval(()=>void load(),3000);
   const online=()=>void load();
   const offlineEvent=()=>{
    if(mounted)setState(previous=>({...(previous??{connected:false,status:'disconnected',disconnected_at:null}),connected:false,status:'disconnected'}));
   };
   window.addEventListener('online',online);
   window.addEventListener('offline',offlineEvent);
   return ()=>{mounted=false;window.clearInterval(timer);window.removeEventListener('online',online);window.removeEventListener('offline',offlineEvent);};
  },[offline]);
  if(!offline||!state)return null;
  async function disconnect(){
   setBusy(true);setError('');
   try{
    const result=await saveCheckpoint();
    setState(result.checkpoint);
   }catch{
    setError('断点保存失败，现有数据保持不变。');
   }finally{setBusy(false);}
  }
  async function continueFromBreak(){
   setBusy(true);setError('');setWarning('');
   try{
    const result=await resumeCheckpoint();
    if(result.status!=='ready'){
     setError(result.error?.message??'断点中的已提交状态无法继续。');
     return;
    }
    setWarning(result.warnings.join('；'));
    onResume(result);
   }catch{
    setError('断点恢复失败，现有数据保持不变。');
   }finally{setBusy(false);}
  }
  if(state.connected&&state.checkpoint_id){
   return <div className="checkpoint-notice" role="status">
    <strong>已恢复连接 · 发现断时记录</strong>
    {state.disconnected_at&&<span>断时记录：{timeText(state.disconnected_at)}</span>}
    <span>断点：{state.checkpoint_id}</span>
    <p>可从断时记录继续已提交的搜索会话与条件草稿；地点、路线和续查游标属于进程内状态，需要重新检索。不会自动重新查源。</p>
    <button type="button" className="button outline" disabled={busy} onClick={()=>void continueFromBreak()}>
     {busy?'正在恢复断点…':'继续上次断点'}
    </button>
    {warning&&<p role="status">{warning}</p>}
    {error&&<p role="alert">{error}</p>}
   </div>;
  }
  if(!state.connected){
   return <div className="checkpoint-notice" role="status">
    <strong>{state.checkpoint_id?'断点已保存 · 等待恢复连接':'已断开 · 保存断点后恢复使用'}</strong>
    {state.disconnected_at&&<span>断时记录：{timeText(state.disconnected_at)}</span>}
    {state.checkpoint_id&&<span>断点：{state.checkpoint_id}</span>}
    <p>已提交的条件草稿与搜索会话会写入项目内一致快照；地点、路线和续查游标属于进程内状态，恢复连接后需重新检索。错误 schema 将拒绝恢复并保留现场。</p>
    {!state.checkpoint_id&&<button type="button" className="button outline" disabled={busy} onClick={()=>void disconnect()}>
     {busy?'正在保存断点…':'保存断点并标记断时'}
    </button>}
    {error&&<p role="alert">{error}</p>}
   </div>;
  }
  return <div className="checkpoint-notice checkpoint-notice-compact" role="status">
   <span>本机未保存断点</span>
   <button type="button" className="button outline" disabled={busy} onClick={()=>void disconnect()}>
    {busy?'正在保存断点…':'保存断点（标记断时）'}
   </button>
   {error&&<p role="alert">{error}</p>}
  </div>;
}
