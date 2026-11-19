/**
 * Friendly, deterministic follow-up prompts for the already committed draft.
 * This module never infers a missing value, writes a provider request, or marks
 * an unvalidated query ready.  Backend ConversationState remains authoritative.
 */
import type {ConversationState} from './types';

export type GuideStage = 'place' | 'place_type' | 'check_in' | 'check_out' |
  'occupancy' | 'children' | 'ages' | 'budget' | 'invalid' | 'ready';
export type GuidedQuestion = {
  stage:GuideStage;
  question:string;
  hint:string;
  placeholder:string;
  examples:readonly string[];
  progress:{label:string;complete:boolean}[];
  completed:number;
  ready:boolean;
};

export function naturalGuidance(state:ConversationState|null):GuidedQuestion {
  const draft=state?.draft;
  const missing=state?.missing??[];
  const hasPlace=Boolean(draft?.place&&draft.place_type)&&
    !missing.some(item=>item==='目的地'||item==='地点类型');
  const hasDates=Boolean(draft?.check_in&&draft.check_out)&&
    !missing.some(item=>/日期|晚数|区间|退房/.test(item));
  const hasOccupancy=Boolean(draft?.rooms&&draft.adults)&&
    !missing.some(item=>/房间数|成人数|入住条件/.test(item));
  const hasChildren=draft?.children_count===0 ||
    (typeof draft?.children_count==='number'&&draft.children_count>0&&
     draft.children_ages?.length===draft.children_count &&
     !missing.some(item=>/儿童/.test(item)));
  const progress=[
    {label:'目的地',complete:hasPlace},
    {label:'入住日期',complete:hasDates},
    {label:'房间人数',complete:hasOccupancy},
    {label:'儿童信息',complete:Boolean(hasChildren)},
  ];
  const completed=progress.filter(item=>item.complete).length;
  const response=(stage:GuideStage,question:string,hint:string,placeholder:string,examples:readonly string[]=[]):GuidedQuestion=>({
    stage,question,hint,placeholder,examples,progress,completed,ready:Boolean(state?.query),
  });
  if(missing.includes('中国大陆目的地')){
    return response('place','当前仅支持中国大陆住宿查询，请换一个目的地。',
      '境外及港澳台暂未接入酒店与地图查询；不会将它们当作国内地点继续。',
      '例如：广州', ['广州', '上海', '杭州']);
  }
  // Only the backend's validated SearchInput can produce a ready state.
  if(state?.query){
    if(draft?.visits?.length){
      return response('ready','住宿基本信息齐了，再核对一下想去的地点。',
        '我记下了行程地点，但尚未核验实际门店；可明确选择一个餐厅作为多门店查找线索，其他地点仍需分别核实。',
        '例如：海底捞想去荔胜广场那家');
    }
    return response('ready','基本信息都齐了，可以填入查询表单啦。',
      '还有预算、独立卫浴或交通偏好，也可以继续补充；最终房型和可订资格仍需另行核实。',
      '例如：再加一个每房每晚200元的参考预算', ['每房每晚200元以内']);
  }
  // Validation problems must take precedence over ordinary missing fields.
  if(missing.some(item=>/有效入住日期|有效入住区间|一致的退房|合法的日期和入住条件/.test(item))){
    return response('invalid','刚才的日期或入住组合还需要确认。',
      '请给出有效的入住日期和晚数；不会替你猜一个日期。',
      '例如：11月20日入住，住2晚');
  }
  if(!draft?.place){
    if(draft?.visits?.length){
      return response('place',`你准备去${draft.visits[0].name}，想在哪个城市找住宿？`,
        '行程地点不是住宿城市。先确认城市，再继续整理日期和人数。',
        '例如：广州', ['广州', '上海', '杭州']);
    }
    return response('place','先告诉我，想去哪里住？',
      '不用一次说全，城市、商圈、地铁站或附近的餐厅都可以。',
      '例如：广州花地湾附近', ['广州', '上海', '杭州']);
  }
  if(!draft.place_type){
    return response('place_type','这个目的地属于哪种地点？',
      '说清楚是城市、景点、地铁站或详细地址，就能继续。',
      '例如：这是城市', ['这是城市', '这是地铁站', '这是详细地址']);
  }
  if(!draft.check_in){
    return response('check_in','打算哪天入住、住几晚？',
      '不确定后面的条件也没关系，可以只补日期。',
      '例如：11月20日入住，住2晚', ['明天入住1晚', '后天入住2晚']);
  }
  if(!draft.check_out){
    return response('check_out','准备住几个晚上？',
      '也可以直接告诉我退房日期。',
      '例如：住2晚', ['住1晚', '住2晚', '住3晚']);
  }
  if(!draft.rooms||!draft.adults){
    return response('occupancy','需要几间房，每间住几位成人？',
      '如果是多间房，请说明每间房的人数；各房人数不同时，请按各房入住组合分别查询。',
      '例如：1间房，每房2成人',
      ['1间房，每房2成人', '1间房，每房1成人', '2间房，每房2成人']);
  }
  if(draft.children_count===null){
    return response('children','有儿童一起入住吗？',
      '有儿童的话，请一起告诉我每个孩子的年龄。',
      '例如：没有儿童，或1名6岁儿童',
      ['没有儿童', '1名6岁儿童']);
  }
  if(draft.children_count>0&&draft.children_ages?.length!==draft.children_count){
    return response('ages','还需要每位儿童的年龄。',
      '请逐一说明每房儿童的年龄，不要让系统猜测。',
      '例如：每房2名儿童，分别5岁和8岁');
  }
  if(draft.budget_fen!==null&&draft.budget_basis!=='room_night'){
    return response('budget','预算需要按“每房每晚”确认。',
      '整住期总预算不能直接当作每房每晚预算。可以改成每房每晚金额，或明确不设预算。',
      '例如：每房每晚200元以内', ['每房每晚200元以内', '取消预算限制']);
  }
  return response('invalid','还有一项入住信息需要核实。',
    state?.question??'可以补充具体日期、人数或地点。',
    '请继续补充你的入住条件');
}
