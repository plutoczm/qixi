import {render,screen} from '@testing-library/react';
import {expect,it} from 'vitest';
import QuoteEvidence from '../QuoteEvidence';
import {blockedQuotesFixture,qualifiedQuotesFixture,quotesFixture} from './fixtures';

it('matched occupancy, link and on-request=false never imply privacy, inventory or total',()=>{
 const data=quotesFixture();data.occupancy_status='matched';data.booking_status='matched';
 data.context.rooms=2;data.context.check_in='2026-11-06';data.context.check_out='2026-11-09';
 data.quotes[0].on_request=false;data.quotes[0].room_name='独卫整间双床房';
 render(<QuoteEvidence data={data}/>);
 // The gate drives the panel now: two presumptions plus one verified item,
 // and the wording must never upgrade a presumption to a verification.
 expect(screen.getAllByText('推定').length).toBeGreaterThanOrEqual(2);
 expect(screen.getByText('已核实')).toBeInTheDocument();
 expect(screen.getByText(/3晚2间的实付总价仍需到道旅核对/)).toBeInTheDocument();
 expect(screen.getByText(/可订层不接受任何推定/)).toBeInTheDocument();
 expect(screen.queryByText(/已验证|库存充足|合格可订/)).toBeNull();
});
it('on-request is a warning for returned quotes, not a count of available rooms',()=>{
 const data=quotesFixture();data.quotes[0].on_request=true;
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/1个已返回报价标为需二次确认/)).toBeInTheDocument();
 expect(screen.getByText(/其他报价也未证明足量库存/)).toBeInTheDocument();
});
it('an empty detail response does not claim city-wide unavailability or zero cost',()=>{
 const data=quotesFixture();data.quotes=[];
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/不代表整个城市无房/)).toBeInTheDocument();
 expect(screen.queryByText(/¥0|免费|库存充足/)).toBeNull();
});

it('counts supplier rate plans and on-request states rather than available rooms',()=>{
 const data=quotesFixture();
 const original=data.quotes[0];
 data.quotes=[
   {...original,id:'true-plan',on_request:true,max_occupancy:4},
   {...original,id:'false-plan',on_request:false,max_occupancy:4},
   {...original,id:'unknown-plan',on_request:null,max_occupancy:null},
 ];
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/道旅返回3条报价方案（不是可订房间数）/)).toBeInTheDocument();
 expect(screen.getByText(/需二次确认1条、未标记需申请1条、申请状态未知1条/)).toBeInTheDocument();
 expect(screen.getByText(/未标记需申请也不代表有库存/)).toBeInTheDocument();
 expect(screen.getByText(/其中1条缺少单间人数上限/)).toBeInTheDocument();
 expect(screen.getAllByText('推定').length).toBeGreaterThanOrEqual(2);
 expect(screen.queryByText(/已锁价|库存充足|立即预订/)).toBeNull();
});


it('reports only fixed sanitized context field issues and keeps booking disabled',()=>{
 const data=quotesFixture();
 data.booking_url=null;
 data.booking_status='mismatch';
 data.occupancy_status='unverified';
 data.occupancy_context_issues=['adultCount_missing','roomCount_missing'];
 data.booking_context_issues=['adultCount_mismatch','roomCount_mismatch','hotelId_missing'];
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/来源入住人数\/房数回显：未完整核实/)).toBeInTheDocument();
 expect(screen.getByText(/成人数缺少回显/)).toBeInTheDocument();
 expect(screen.getByText(/安全来源跳转：链接上下文不匹配，已停用/)).toBeInTheDocument();
 expect(screen.getByText(/房间数与本次条件不一致/)).toBeInTheDocument();
 expect(screen.getByText(/不会改写来源链接/)).toBeInTheDocument();
 expect(screen.queryByText(/立即预订|库存充足|已验证|secret/)).toBeNull();
});

it('unknown diagnostic codes never reveal arbitrary backend strings',()=>{
 const data=quotesFixture();
 data.booking_status='unverified';
 data.booking_context_issues=['someTokenValue_leaked'];
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/其他上下文字段待核实/)).toBeInTheDocument();
 expect(screen.queryByText(/someTokenValue/)).toBeNull();
});


it('zero-child URL with unparseable age encoding remains blocked without leaking raw values',()=>{
 const data=quotesFixture();
 data.context.children_ages=[];
 data.booking_url=null;
 data.booking_status='unverified';
 data.booking_context_issues=['childAgeDetails_unparseable'];
 data.occupancy_status='unverified';
 data.occupancy_context_issues=['adultCount_missing','roomCount_missing'];
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/安全来源跳转：链接上下文未完整核实/)).toBeInTheDocument();
 expect(screen.getByText(/儿童年龄无法核验格式/)).toBeInTheDocument();
 expect(screen.getByText(/成人数缺少回显/)).toBeInTheDocument();
 expect(screen.getByText(/不会改写来源链接/)).toBeInTheDocument();
 expect(screen.queryByRole('link',{name:/预订|立即/})).toBeNull();
 expect(screen.getAllByText('推定').length).toBeGreaterThanOrEqual(2);
});

it('a blocked quote is named as blocked and never shown as a reference price',()=>{
 const data=blockedQuotesFixture();
 render(<QuoteEvidence data={data}/>);
 // The panel never says "passed" here: it names the blocked rows instead.
 expect(screen.getByText(/该报价被拦下的项目/)).toBeInTheDocument();
 expect(screen.getByText(/被拦下时不展示为参考价/)).toBeInTheDocument();
 // Both the whole-room and the bathroom rows must read as blocked. Inventory
 // may still be verified: a dormitory bed can genuinely have stock while the
 // room form disqualifies it, and the two facts must not be conflated.
 expect(screen.getAllByText('不满足').length).toBe(2);
 expect(screen.queryByText(/库存充足|合格可订|立即预订/)).toBeNull();
});

it('an unevaluated quote is reported as unevaluated rather than passing',()=>{
 const data=quotesFixture();
 data.quotes[0].reference=null;
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/本次未评估参考层资格/)).toBeInTheDocument();
 expect(screen.queryByText('已核实')).toBeNull();
 expect(screen.queryByText('推定')).toBeNull();
});

it('reports the qualified count from the server verdict, never recomputed here',()=>{
 const data=qualifiedQuotesFixture();
 render(<QuoteEvidence data={data}/>);
 const line=screen.getByText(/本轮1条报价中/);
 expect(line).toHaveTextContent('1条合格（含推定标注）');
 expect(line).toHaveTextContent('最低合格报价为第1条，已知费用折算每房每晚¥188');
 expect(line).toHaveTextContent('不代表可订');
});
it('says so when nothing qualifies instead of falling back to the cheapest quote',()=>{
 const data=quotesFixture();
 render(<QuoteEvidence data={data}/>);
 const line=screen.getByText(/本轮没有合格报价/);
 expect(line).toHaveTextContent('1条仅参考');
 expect(line).toHaveTextContent('0条已排除');
 expect(screen.queryByText(/最低合格报价/)).toBeNull();
});
it('does not invent a qualified count when the quote was never evaluated',()=>{
 const data=quotesFixture();data.quotes[0].qualification=null;data.qualified_lowest_id=null;
 render(<QuoteEvidence data={data}/>);
 expect(screen.getByText(/本轮没有合格报价/)).toHaveTextContent('1条未评估');
});
