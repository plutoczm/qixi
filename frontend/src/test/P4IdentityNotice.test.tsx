import {render,screen,fireEvent} from '@testing-library/react';
import {it,expect} from 'vitest';
import RecallCoverage from '../RecallCoverage';
import {mapFixture} from './recall-fixtures';

it.each([
 {reason:'source_city_unverified',message:'道旅未独立回显酒店所在城市'},
 {reason:'source_address_mismatch',message:'名称相同但道旅地址不同'},
 {reason:'source_name_mismatch',message:'地址相同但道旅酒店名称不同'},
])('explains map-first evidence gap: $reason',({reason,message})=>{
 const data=mapFixture();
 data.branches[0].hotel_targets[0].reason=reason;
 render(<RecallCoverage data={data} selected="" onSelect={()=>{}}/>);
 fireEvent.click(screen.getByText(/地图酒店与查名记录/));
 expect(screen.getByText(new RegExp(message))).toBeInTheDocument();
 expect(screen.getByText('保留待核对')).toBeInTheDocument();
 expect(screen.queryByText('同店字段一致，步行待核对')).toBeNull();
});
