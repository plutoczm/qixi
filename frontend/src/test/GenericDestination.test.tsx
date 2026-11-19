import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,expect,it,vi} from 'vitest';
import SearchForm from '../SearchForm';
import {selectedVisit} from '../tripIntent';

describe('general destination and waypoint semantics',()=>{
  it('starts without a forced city or an implicit supplier call',()=>{
    const submit=vi.fn();
    render(<SearchForm loading={false} onSubmit={submit}/>);
    expect(screen.getByLabelText('希望住在哪个地方附近？')).toHaveValue('');
    expect(screen.getByText('中国大陆')).toBeInTheDocument();
    expect(screen.queryByRole('textbox',{name:/国家/})).toBeNull();
    expect(screen.queryByText('比较多家餐厅周边住宿')).toBeNull();
    expect(submit).not.toHaveBeenCalled();
  });

  it('sends mainland destination and explicit route notes without changing the anchor',async()=>{
    const submit=vi.fn().mockResolvedValue(undefined);
    render(<SearchForm loading={false} onSubmit={submit} sourceName="道旅"/>);
    fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'上海迪士尼乐园'}});
    fireEvent.change(screen.getByLabelText('地点类型'),{target:{value:'景点'}});
    fireEvent.click(screen.getByRole('button',{name:/添加途经点/}));
    fireEvent.change(screen.getByLabelText('途经点 1'),{target:{value:'苏州北站'}});
    fireEvent.click(screen.getByRole('button',{name:/添加途经点/}));
    fireEvent.change(screen.getByLabelText('途经点 2'),{target:{value:'无锡东站'}});
    fireEvent.submit(screen.getByRole('form'));
    await waitFor(()=>expect(submit).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({
      place:'上海迪士尼乐园',place_type:'景点',country_region:'中国大陆',
      waypoints:['苏州北站','无锡东站'],poi_name:null,
    }));
  });

  it('prevents empty and duplicate stops, allows removal, and never invokes the old branch tool',async()=>{
    const submit=vi.fn().mockResolvedValue(undefined),recall=vi.fn();
    render(<SearchForm loading={false} onSubmit={submit} onRecall={recall}/>);
    fireEvent.change(screen.getByLabelText('希望住在哪个地方附近？'),{target:{value:'成都天府广场'}});
    fireEvent.click(screen.getByRole('button',{name:/添加途经点/}));
    fireEvent.submit(screen.getByRole('form'));
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('途经点');
    fireEvent.change(screen.getByLabelText('途经点 1'),{target:{value:'春熙路'}});
    fireEvent.click(screen.getByRole('button',{name:/添加途经点/}));
    fireEvent.change(screen.getByLabelText('途经点 2'),{target:{value:'春熙路'}});
    fireEvent.submit(screen.getByRole('form'));
    expect(screen.getByRole('alert')).toHaveTextContent('不能重复');
    fireEvent.click(screen.getByRole('button',{name:'移除途经点 2'}));
    fireEvent.submit(screen.getByRole('form'));
    await waitFor(()=>expect(submit).toHaveBeenCalledWith(expect.objectContaining({waypoints:['春熙路']})));
    expect(recall).not.toHaveBeenCalled();
  });

  it('can explicitly choose a landmark rather than a restaurant as the hotel anchor',()=>{
    const visits=[
      {name:'海底捞',category:'餐饮' as const,area_hint:null,stay_near:false},
      {name:'广州塔',category:'景点' as const,area_hint:null,stay_near:true},
    ];
    expect(selectedVisit(visits,'广州塔')?.name).toBe('广州塔');
    expect(selectedVisit(visits,'未提及地点')).toBeNull();
    const submit=vi.fn();
    render(<SearchForm loading={false} onSubmit={submit} trip={{visits,restaurantAnchor:null,stayAnchor:'广州塔'}}/>);
    expect(screen.getByLabelText('希望住在哪个地方附近？')).toHaveValue('广州塔');
    expect(screen.getByLabelText('地点类型')).toHaveValue('景点');
    expect(screen.getByLabelText('途经点 1')).toHaveValue('海底捞');
    expect(submit).not.toHaveBeenCalled();
  });
});
