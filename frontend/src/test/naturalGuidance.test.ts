import {describe,it,expect} from 'vitest';
import {naturalGuidance} from '../naturalGuidance';
import {conversationFixture} from './conversation-fixtures';
import type {ConversationState} from '../types';

function incomplete(changes:Partial<ConversationState['draft']>,missing:string[]=[]):ConversationState {
  const base=conversationFixture('succeeded');
  return {...base,query:null,draft:{...base.draft,...changes},missing};
}

describe('progressive natural-language follow-up prompts',()=>{
  it('starts with one approachable question rather than requiring a complete sentence',()=>{
    const guide=naturalGuidance(null);
    expect(guide.stage).toBe('place');
    expect(guide.question).toContain('哪里');
    expect(guide.completed).toBe(0);
    expect(guide.ready).toBe(false);
  });
  it('keeps recognized destination while moving to check-in date and nights',()=>{
    const state=incomplete({check_in:null,check_out:null,nights:null,rooms:null,adults:null,children_count:null,children_ages:null},['入住日期']);
    const guide=naturalGuidance(state);
    expect(guide.stage).toBe('check_in');
    expect(guide.progress[0].complete).toBe(true);
    expect(guide.progress[1].complete).toBe(false);
    expect(guide.ready).toBe(false);
  });
  it('asks only for nights when a check-in is already known',()=>{
    const guide=naturalGuidance(incomplete({check_out:null,nights:null},['退房日期或晚数']));
    expect(guide.stage).toBe('check_out');
    expect(guide.examples).toContain('住2晚');
  });
  it('asks each room for adult occupancy rather than using totals',()=>{
    const guide=naturalGuidance(incomplete({rooms:null,adults:null},['房间数','每房成人数']));
    expect(guide.stage).toBe('occupancy');
    expect(guide.question).toContain('每间');
  });
  it('checks child ages and budget basis without guessing either value',()=>{
    const ages=naturalGuidance(incomplete({children_count:2,children_ages:[7]},['每房全部儿童年龄']));
    expect(ages.stage).toBe('ages');
    expect(ages.progress[3].complete).toBe(false);
    const budget=naturalGuidance(incomplete({budget_fen:25000,budget_basis:'stay_total'},['每房每晚预算口径']));
    expect(budget.stage).toBe('budget');
    expect(budget.ready).toBe(false);
  });
  it('treats invalid dates as an unresolved requirement rather than showing completion',()=>{
    const guide=naturalGuidance(incomplete({},['有效入住日期']));
    expect(guide.stage).toBe('invalid');
    expect(guide.ready).toBe(false);
    expect(guide.completed).toBe(3);
  });
  it('only presents ready when the server has returned a validated query',()=>{
    const guide=naturalGuidance(conversationFixture('succeeded'));
    expect(guide.stage).toBe('ready');
    expect(guide.completed).toBe(4);
    expect(guide.ready).toBe(true);
  });
});


describe('R107 occupancy and destination guidance',()=>{
  it('explains separate queries for unequal room groups',()=>{
    const guide=naturalGuidance(incomplete({rooms:2,adults:null},['每房成人数']));
    expect(guide.stage).toBe('occupancy');
    expect(guide.hint).toContain('分别查询');
    expect(guide.ready).toBe(false);
  });
  it('puts the mainland scope warning ahead of ordinary missing fields',()=>{
    const guide=naturalGuidance(incomplete({place:'东京新宿',check_in:null},['中国大陆目的地','入住日期']));
    expect(guide.stage).toBe('place');
    expect(guide.question).toContain('中国大陆');
    expect(guide.ready).toBe(false);
  });
});
