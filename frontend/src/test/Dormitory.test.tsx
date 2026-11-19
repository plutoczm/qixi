import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,expect,it,vi} from 'vitest';
import SearchForm from '../SearchForm';
import RoomQuoteRow,{QualificationPanel} from '../RoomQuoteRow';
import type {Qualification,RoomQuote} from '../types';
import {quotesFixture} from './fixtures';

/**
 * AC14 on the page: the bed-place switch is off by default, reached by an
 * explicit tick, and the relaxed rows say "接受后通过" -- never "通过（推定）",
 * because the user's decision and our presumption must not share a word.
 */

describe('bed-place switch',()=>{
  it('is off by default and submits false',async()=>{
    const submit=vi.fn().mockResolvedValue(undefined);
    render(<SearchForm loading={false} onSubmit={submit}/>);
    expect(screen.getByLabelText(/床位/)).not.toBeChecked();
    fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
    fireEvent.submit(screen.getByRole('form'));
    await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({accepts_dormitory:false})));
  });

  it('submits true only after an explicit tick',async()=>{
    const submit=vi.fn().mockResolvedValue(undefined);
    render(<SearchForm loading={false} onSubmit={submit}/>);
    fireEvent.click(screen.getByLabelText(/床位/));
    fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'广州'}});
    fireEvent.submit(screen.getByRole('form'));
    await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({accepts_dormitory:true})));
  });

  it('states exactly what the tick relaxes',()=>{
    render(<SearchForm loading={false} onSubmit={vi.fn()}/>);
    expect(screen.getByText(/只放宽两项：整间客房、房内独卫/)).toBeInTheDocument();
    expect(screen.getByText(/不勾选时床位房一律排除/)).toBeInTheDocument();
  });
});

function acceptedQualification():Qualification {
  const base=quotesFixture().quotes[0].qualification!;
  return {
    ...base,
    tier:'qualified',
    reasons:[],
    conditions:[
      {condition:'room_form',label:'整间客房',verdict:'pass',basis:'user_accepted_bed:床位',detail:'房型标注“床位”，你已明确接受床位，不再按整间过夜客房要求。',presumed:false,accepted:true},
      {condition:'private_bathroom',label:'房内独立卫浴',verdict:'pass',basis:'user_accepted_shared_bathroom:床位',detail:'房型标注“床位”；你接受床位即同时接受这类房型常见的共享卫浴。',presumed:false,accepted:true},
    ],
  };
}

describe('relaxed rows on the quote card',()=>{
  it('marks a relaxed row as 接受后通过, not as a presumption',()=>{
    render(<QualificationPanel qualification={acceptedQualification()}/>);
    expect(screen.getAllByText('接受后通过')).toHaveLength(2);
    expect(screen.queryByText('通过（推定）')).not.toBeInTheDocument();
  });

  it('says the relaxation is the user choice, not evidence or our guess',()=>{
    render(<QualificationPanel qualification={acceptedQualification()}/>);
    expect(screen.getByText(/2项因你接受床位而放宽：整间客房、房内独立卫浴/)).toBeInTheDocument();
    expect(screen.getByText(/这不是来源核实，也不是我们的推定/)).toBeInTheDocument();
  });

  it('shows nothing about beds when the user never accepted one',()=>{
    const base=quotesFixture().quotes[0].qualification!;
    render(<QualificationPanel qualification={base}/>);
    expect(screen.queryByText(/因你接受床位而放宽/)).not.toBeInTheDocument();
  });
});

describe('bed price caption',()=>{
  function bedQuote():{quote:RoomQuote;context:ReturnType<typeof quotesFixture>['context']} {
    const fixture=quotesFixture();
    const quote:RoomQuote={...fixture.quotes[0]};
    const base=quote.qualification!;
    quote.qualification={
      ...base,
      tier:'qualified',
      conditions:acceptedQualification().conditions,
      fees:{
        ...base.fees,
        room_charge_fen:12000,
        total_known_fen:12000,
        per_room_night_fen:12000,
        lines:base.fees.lines.map(line=>line.kind==='room_charge'
          ?{...line,per:'person_night' as const,amount_fen:6000,total_fen:12000}
          :line),
      },
    };
    return {quote,context:fixture.context};
  }

  it('says 床位单价 · 每人每晚 instead of 房型参考价',()=>{
    const {quote,context}=bedQuote();
    render(<RoomQuoteRow quote={quote} context={context} index={0} sourceName="途牛"/>);
    expect(screen.getByText(/床位单价 · 每人每晚 · 非最终总价/)).toBeInTheDocument();
    expect(screen.queryByText(/途牛房型参考价 · 非最终总价/)).not.toBeInTheDocument();
  });

  it('keeps the ordinary caption for a per-room price',()=>{
    const {quote,context}=bedQuote();
    const qualification=quote.qualification!;
    const perRoom:Qualification={
      ...qualification,
      fees:{
        ...qualification.fees,
        lines:qualification.fees.lines.map(line=>line.kind==='room_charge'
          ?{...line,per:'room_night' as const}
          :line),
      },
    };
    render(<RoomQuoteRow quote={{...quote,qualification:perRoom}} context={context} index={0} sourceName="途牛"/>);
    expect(screen.queryByText(/床位单价/)).not.toBeInTheDocument();
    expect(screen.getByText(/非最终总价/)).toBeInTheDocument();
  });
});
