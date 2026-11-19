import {afterEach, describe, expect, it, vi} from 'vitest';
import {searchHotels} from '../api';
import {searchFixture} from './fixtures';

function reply(status:number, data:unknown, invalidJson=false):Response {
  return {
    ok:status>=200&&status<300,
    status,
    json:()=>invalidJson?Promise.reject(new SyntaxError('not JSON')):Promise.resolve(data),
  } as unknown as Response;
}
afterEach(()=>vi.unstubAllGlobals());

describe('personal API errors are actionable and never automatically retried',()=>{
  it('turns a browser network failure into a clear manual-recovery message, without retry',async()=>{
    const fetch=vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch',fetch);
    await expect(searchHotels(searchFixture().context,new AbortController().signal))
      .rejects.toThrow(/网络连接失败.*检查网络/);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('reports HTTP 429 as a rate limit with status preserved when backend has no message',async()=>{
    const fetch=vi.fn().mockResolvedValue(reply(429,{}));
    vi.stubGlobal('fetch',fetch);
    await expect(searchHotels(searchFixture().context,new AbortController().signal))
      .rejects.toMatchObject({status:429,message:expect.stringMatching(/请求频繁.*手动重试/)});
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('handles a non-JSON HTTP 503 failure with a useful service message',async()=>{
    const fetch=vi.fn().mockResolvedValue(reply(503,null,true));
    vi.stubGlobal('fetch',fetch);
    await expect(searchHotels(searchFixture().context,new AbortController().signal))
      .rejects.toMatchObject({status:503,message:expect.stringMatching(/服务暂时不可用/)});
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('preserves a supplier-provided error message and code without inventing success',async()=>{
    const fetch=vi.fn().mockResolvedValue(reply(429,{error:{message:'这个账号今天的查询额度已用完。',code:'QUOTA_EXHAUSTED'}}));
    vi.stubGlobal('fetch',fetch);
    await expect(searchHotels(searchFixture().context,new AbortController().signal))
      .rejects.toMatchObject({status:429,code:'QUOTA_EXHAUSTED',message:'这个账号今天的查询额度已用完。'});
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('keeps AbortError intact so stop and timeout logic can distinguish it',async()=>{
    const aborted=new DOMException('Aborted','AbortError');
    const fetch=vi.fn().mockRejectedValue(aborted);
    vi.stubGlobal('fetch',fetch);
    await expect(searchHotels(searchFixture().context,new AbortController().signal)).rejects.toBe(aborted);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
