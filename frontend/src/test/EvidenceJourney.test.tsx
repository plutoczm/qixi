import {render,screen,within} from '@testing-library/react';
import {describe,expect,it} from 'vitest';
import EvidenceJourney from '../EvidenceJourney';
import {searchFixture} from './fixtures';

const liveResult = () => ({...searchFixture(),data_mode:'live' as const});

describe('journey is a progress cue, never a verification claim',()=>{
  it('starts without presenting source requests or room checks as complete',()=>{
    render(<EvidenceJourney result={null} loading={false} offline={false}/>);
    const journey=within(screen.getByRole('navigation',{name:'查询证据进度'}));
    expect(journey.getByText('尚未调用')).toBeInTheDocument();
    expect(journey.getByText('等待结果')).toBeInTheDocument();
    expect(journey.getByText('打开后核验')).toBeInTheDocument();
    expect(journey.getByText('另行查源')).toBeInTheDocument();
    expect(journey.queryByText(/已核实可订/)).toBeNull();
  });

  it('shows pending source activity but does not invent candidates',()=>{
    render(<EvidenceJourney result={null} loading offline={false}/>);
    const journey=within(screen.getByRole('navigation',{name:'查询证据进度'}));
    expect(journey.getByText('正在请求')).toHaveAttribute('aria-current','step');
    expect(journey.queryByText(/家参考/)).toBeNull();
  });

  it('marks candidate counts as references while leaving room and travel unverified',()=>{
    render(<EvidenceJourney result={liveResult()} loading={false} offline={false}/>);
    const journey=within(screen.getByRole('navigation',{name:'查询证据进度'}));
    expect(journey.getByText('已收到响应')).toBeInTheDocument();
    expect(journey.getByText('1 家参考')).toBeInTheDocument();
    expect(journey.getByText('打开后核验')).toBeInTheDocument();
    expect(journey.getByText('另行查源')).toBeInTheDocument();
    expect(journey.queryByText(/已核实可订/)).toBeNull();
  });

  it('distinguishes empty partial, unavailable, and offline observations',()=>{
    const partial={...liveResult(),status:'partial' as const,hotels:[]};
    const {rerender}=render(<EvidenceJourney result={partial} loading={false} offline={false}/>);
    expect(screen.getByText('部分响应')).toBeInTheDocument();
    expect(screen.getByText('本次无候选')).toBeInTheDocument();
    rerender(<EvidenceJourney result={{...partial,status:'unavailable'}} loading={false} offline={false}/>);
    expect(screen.getByText('来源不可用')).toBeInTheDocument();
    rerender(<EvidenceJourney result={searchFixture()} loading={false} offline/>);
    expect(screen.getByText('模拟读取')).toBeInTheDocument();
    expect(screen.getByText('1 家模拟')).toBeInTheDocument();
  });
});
