import {useEffect,useRef,useState} from 'react';
import {createConversation,getConversation,interpretConditions,cancelInterpretation} from './api';
import {money} from './format';
import {naturalGuidance} from './naturalGuidance';
import {selectedVisit} from './tripIntent';
import type {TripHandoff} from './tripIntent';
import type {ConversationState,SearchInput} from './types';
const activeStatus=(status:string)=>['queued','running','validating'].includes(status);
export default function NaturalQuery({onApply,offline=false,resumeState=null}:{onApply:(query:SearchInput,trip:TripHandoff)=>void;offline?:boolean;resumeState?:ConversationState|null}){
 const [text,setText]=useState('');const [state,setState]=useState<ConversationState|null>(null);
 const [busy,setBusy]=useState(false);const [starting,setStarting]=useState(false);
 const [error,setError]=useState('');const [ack,setAck]=useState(false);
 const [stayAnchor,setStayAnchor]=useState('');
 const [messages,setMessages]=useState<string[]>([]);
 const cid=useRef<string|null>(null);const clientSequence=useRef(0);const epoch=useRef(0);
 const pending=useRef<string[]>([]);const abort=useRef<AbortController|null>(null);
 const timer=useRef<number|null>(null);
 const current=useRef<{cid:string;operation:string;sequence:number}|null>(null);
 function clearTimer(){if(timer.current!==null)window.clearTimeout(timer.current);timer.current=null;}
 useEffect(()=>()=>{epoch.current++;abort.current?.abort();clearTimer();const operation=current.current;
  if(operation)void fetch('/api/conversations/'+operation.cid+'/cancel',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({operation_id:operation.operation,client_sequence:operation.sequence})}).catch(()=>{});
 },[]);
 useEffect(()=>{
  if(!resumeState)return;
  epoch.current++;abort.current?.abort();clearTimer();
  cid.current=resumeState.conversation_id;
  clientSequence.current=Math.max(clientSequence.current,resumeState.client_sequence);
  pending.current=[];current.current=null;
  setState(resumeState);setBusy(false);setStarting(false);setMessages([]);setAck(false);setText('');setStayAnchor('');
  setError(activeStatus(resumeState.status)?'断点中存在未完成的条件理解；已保留最后已提交草稿，请重新开始或等待状态明确后再继续。':'');
 },[resumeState]);
 function accept(data:ConversationState,version:number){
  if(epoch.current!==version)return;
  setState(data);clientSequence.current=Math.max(clientSequence.current,data.client_sequence);
  if(!activeStatus(data.status)){
   setBusy(false);current.current=null;
   if(data.status==='succeeded')pending.current=[];
  }
 }
 async function poll(conversationId:string,version:number,until:number){
  if(epoch.current!==version)return;
  if(Date.now()>=until){void stop();setError('理解超时，已请求停止。可以调整条件或直接填写表单。');return;}
  try{
   const data=await getConversation(conversationId);
   if(epoch.current!==version)return;
   accept(data,version);
   if(activeStatus(data.status))timer.current=window.setTimeout(()=>void poll(conversationId,version,until),600);
  }catch{
   if(epoch.current!==version)return;
   setError('与本地服务的连接暂时中断，后台操作状态未确认；正在尝试重新获取，请勿沿用未完成草稿。');
   timer.current=window.setTimeout(()=>void poll(conversationId,version,until),1000);
  }
 }
 async function send(){
  const message=text.trim();if(!message||starting)return;
  const inputs=[...pending.current,message];
  if(inputs.length>6||new TextEncoder().encode(inputs.join('')).length>12000){setError('未完成的修改过多，请先停止或重新开始后描述完整条件。');return;}
  const version=++epoch.current;abort.current?.abort();clearTimer();
  const controller=new AbortController();abort.current=controller;
  setStarting(true);setBusy(true);setError('');setAck(false);setText('');
  setMessages(old=>[...old,message].slice(-5));pending.current=inputs;
  try{
   let conversationId=cid.current;
   if(!conversationId){
    const created=await createConversation(controller.signal);
    if(epoch.current!==version)return;
    conversationId=created.conversation_id;cid.current=conversationId;setState(created);
   }
   const operation=crypto.randomUUID();const sequence=++clientSequence.current;
   current.current={cid:conversationId,operation,sequence};
   const data=await interpretConditions(conversationId,{operation_id:operation,expected_revision:state?.revision??0,client_sequence:sequence,user_inputs:inputs,provider:'deepseek'},controller.signal);
   if(epoch.current!==version)return;
   accept(data,version);
   if(activeStatus(data.status))timer.current=window.setTimeout(()=>void poll(conversationId!,version,Date.now()+65000),300);
  }catch(reason){
   if(epoch.current!==version)return;
   setBusy(false);current.current=null;setText(message);
   setError(reason instanceof TypeError
    ?'暂时无法连接本地服务，后台是否完成尚未确认；请恢复连接并核对状态，或直接填写表单。'
    :reason instanceof Error?reason.message:'理解暂时不可用，请使用表单。');
   if(cid.current){
    try{const latest=await getConversation(cid.current);if(epoch.current===version){setState(latest);clientSequence.current=Math.max(clientSequence.current,latest.client_sequence);if(latest.revision!==(state?.revision??0))pending.current=[];}}
    catch{/* Keep local conditions; never invent a completion. */}
   }
  }finally{if(epoch.current===version)setStarting(false);}
 }
 async function stop(){
  const version=++epoch.current;abort.current?.abort();clearTimer();
  const operation=current.current;current.current=null;pending.current=[];
  setBusy(false);setStarting(false);setError('');
  if(operation){
   try{accept(await cancelInterpretation(operation.cid,{operation_id:operation.operation,client_sequence:operation.sequence}),version);}
   catch{if(epoch.current===version)setError('停止请求暂未确认；后台仍受截止时间保护，请勿沿用未完成草稿。');}
  }
 }
 function restart(){
  const operation=current.current;epoch.current++;abort.current?.abort();clearTimer();
  if(operation)void cancelInterpretation(operation.cid,{operation_id:operation.operation,client_sequence:operation.sequence}).catch(()=>{});
  current.current=null;cid.current=null;clientSequence.current=0;pending.current=[];
  setState(null);setBusy(false);setStarting(false);setText('');setMessages([]);setAck(false);setStayAnchor('');setError('');
 }
 const draft=state?.draft;const notes=draft?.notes??[];const visits=draft?.visits??[];
 const chosenStay=selectedVisit(visits,stayAnchor);
 const guidance=naturalGuidance(state);
 const usable=state?.query&&!busy&&!text.trim()&&(!notes.length||ack);
 return <section className="natural-query" aria-label="自然语言查询">
  <div className="natural-title"><span className="section-kicker">一步一步整理</span><h2>不用一次说全，慢慢告诉我</h2><p>当前仅支持中国大陆住宿查询。可以分几次补充需求，我会保留已经整理好的条件。</p></div>
  <div className="natural-progress" aria-label="基本入住信息进度">
   <span className="natural-progress-caption">基本信息 <strong>{guidance.completed}/4 项已整理</strong></span>
   <ol className="natural-steps">{guidance.progress.map(item=><li key={item.label} className={item.complete?'is-complete':''}><span aria-hidden="true">{item.complete?'✓':'○'}</span>{item.label}</li>)}</ol>
  </div>
  {messages.length>0&&<details className="natural-history"><summary>回看之前的补充（{messages.length}条）</summary>{messages.map((message,index)=><p key={index}>{message}</p>)}</details>}
  <div className="natural-assistant" aria-live="polite">
   <span className="natural-avatar" aria-hidden="true">栖</span>
   <div><strong>{guidance.ready?'已可以填写表单':'接下来，我们只说这一项'}</strong><p>{guidance.question}</p><small>{guidance.hint}</small></div>
  </div>
  <label htmlFor="natural-message">回复小助手（一次补充一点即可）</label>
  <textarea id="natural-message" rows={3} maxLength={2000} value={text} onChange={e=>setText(e.target.value)} placeholder={guidance.placeholder} onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();void send();}}}/>
  {!text.trim()&&!busy&&!starting&&guidance.examples.length>0&&<div className="natural-suggestions"><span>可选回答 · 选中后再点击发送</span><div>{guidance.examples.map(example=><button key={example} type="button" onClick={()=>setText(example)}>{example}</button>)}</div></div>}
  <div className="natural-models"><span>{offline?'本地演示 · 不会联网':'点击发送才使用 DeepSeek · 不会自动查价'}</span><span>Ctrl + Enter 发送</span></div>
  <div className="natural-actions"><button type="button" className="button primary" disabled={!text.trim()||starting} onClick={()=>void send()}>{busy?'用新回答替换':messages.length?'发送这条补充':'发送给小助手'}</button>{busy&&<button type="button" className="button outline" onClick={()=>void stop()}>停止理解</button>}</div>
  {busy&&<p className="natural-status" role="status"><span className="spinner"/>正在整理这条回答。可以停止，或发送新回答替换本次操作。</p>}
  {error&&<p className="natural-error" role="alert">{error}</p>}
  {state&&<>
   {(offline||state.data_mode==='test')&&<p className="test-banner">{offline?'模拟条件草稿 · 本地规则解析 · 0 外部请求':'测试数据 · 仅用于界面验证'}</p>}
   <div className="natural-draft"><div className="natural-draft-head"><strong>已整理的条件</strong><span>仅供核对 · 尚未查价</span></div><dl>
    <div><dt>目的地</dt><dd>{draft?.place??'待补充'}{draft?.place_type?' · '+draft.place_type:''}</dd></div>
    <div><dt>入住和退房</dt><dd>{draft?.check_in??'待补充'} → {draft?.check_out??'待补充'}</dd></div>
    <div><dt>房间与成人</dt><dd>{draft?.rooms??'待补充'}间 · 每房{draft?.adults??'待补充'}成人</dd></div>
    <div><dt>儿童</dt><dd>{draft?.children_count===0?'无儿童':draft?.children_count===null?'待确认':draft?.children_count+'人，年龄：'+(draft?.children_ages?.join('、')||'待补充')}</dd></div>
    <div><dt>参考预算（可不填）</dt><dd>{draft?.budget_fen===null?'不限制': '¥'+money(draft?.budget_fen??null)+(draft?.budget_basis==='room_night'?'/房晚':'（口径待确认）')}</dd></div>
   </dl></div>
   {!busy&&(state.status==='cancelled'||state.error)&&<p className="natural-question">{state.status==='cancelled'?'已停止；以下保留最后已提交的条件。':state.error?.message}</p>}
   {visits.length>0&&<div className="natural-visits" aria-label="LLM识别的行程地点">
    <div className="natural-draft-head"><strong>你计划去的地方</strong><span>AI从原话提取 · 非地图核验</span></div>
    <ul>{visits.map(visit=><li key={visit.name}>
      <span className="natural-visit-kind">{visit.category}</span>
      <div><strong>{visit.name}</strong><small>{visit.area_hint?'你提到：'+visit.area_hint:'具体门店或位置尚未确定'}{visit.stay_near?' · 希望住宿靠近这里':''}</small></div>
    </li>)}</ul>
    {state.query&&<div className="natural-visit-choice">
      <label htmlFor="natural-stay-anchor">这次希望住在哪个地点附近？</label>
      <select id="natural-stay-anchor" value={chosenStay?.name??''} onChange={event=>setStayAnchor(event.target.value)}>
       <option value="">不指定，保留原先填写的目的地</option>
       {visits.map(visit=><option key={visit.name} value={visit.name}>{visit.name} · {visit.category}{visit.area_hint?'（'+visit.area_hint+'）':''}</option>)}
      </select>
      <p>{chosenStay?'会将“'+chosenStay.name+'”作为待核对的住宿目的地；其他地点仅记录为途经点。':'不自动拿餐厅或景点替换你的目的地。'}{visits.length>1?' 多站路线仍需地图核实，不能把途经点当作已验证距离。':''}</p>
    </div>}
   </div>}
   {notes.length>0&&<div className="natural-notes"><strong>尚未自动用于筛选</strong>{notes.map(note=><p key={note}>{note}</p>)}<label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>我知道这些附加条件仍需另外核实</label></div>}
   {state.warnings.map(w=><p className="quiet" key={w}>{w}</p>)}
   {state.query?<button type="button" className="button outline natural-apply" disabled={!usable} onClick={()=>{if(usable&&state.query)onApply({...state.query},{visits:[...visits],restaurantAnchor:null,stayAnchor:chosenStay?.name??null});}}>核对后填入查询表单</button>:<p className="natural-pending-note">还差几项基本信息。整理齐后，就能一键填入查询表单。</p>}
   <button type="button" className="text-button natural-reset" onClick={restart}>重新开始理解</button>
  </>}
  <p className="natural-footnote">{offline?'这是不联网的演示；系统仅整理条件，不会自动查价或预订。':'你的输入会发送至 DeepSeek，用于整理住宿条件。请确认后再搜索酒店；不会自动预订或叫车。'} <a className="natural-direct-link" href="#query-panel">或者直接手填表单</a></p>
 </section>;
}
