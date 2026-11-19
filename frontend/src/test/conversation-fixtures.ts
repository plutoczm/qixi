import type {ConversationState} from '../types';
import {context} from './fixtures';
export function conversationFixture(status:ConversationState['status']='idle',place='广州'):ConversationState{
 const ready=status==='succeeded';
 return {conversation_id:'test-conversation',revision:ready?1:0,generation:ready?1:0,client_sequence:ready?1:0,operation_id:ready?'test-operation':null,status,
 draft:ready?{place,place_type:'城市',check_in:context.check_in,check_out:context.check_out,nights:1,adults:2,rooms:1,children_count:0,children_ages:[],budget_fen:20000,budget_basis:'room_night',notes:[],visits:[]}:{place:null,place_type:null,check_in:null,check_out:null,nights:null,adults:null,rooms:null,children_count:null,children_ages:null,budget_fen:null,budget_basis:null,notes:[],visits:[]},
 query:ready?{...context,place,budget_fen:20000}:null,missing:ready?[]:['目的地'],question:ready?'条件已整理，请核对后填入查询表单。':'请补充目的地。',warnings:[],error:null,provider:ready?'deepseek':null,model:ready?'deepseek-flash':null,data_mode:'test'};
}
