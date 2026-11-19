import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyProviderHttpError} from './provider-errors.mjs';

test('DeepSeek official HTTP statuses yield safe distinct reasons', () => {
  for (const [status, code] of [
    [401,'AUTHENTICATION_FAILED'], [402,'INSUFFICIENT_BALANCE'],
    [403,'ACCESS_DENIED'], [429,'RATE_LIMITED'],
    [500,'PROVIDER_UNAVAILABLE'], [503,'PROVIDER_UNAVAILABLE'],
    [400,'PROVIDER_ERROR'], [422,'PROVIDER_ERROR']
  ]) {
    assert.equal(classifyProviderHttpError('deepseek',status),code);
  }
});

test('other providers or relay statuses never inherit DeepSeek-specific balance meaning', () => {
  assert.equal(classifyProviderHttpError('relay', 402),'PROVIDER_ERROR');
  assert.equal(classifyProviderHttpError('pi_chatgpt', 402),'PROVIDER_ERROR');
  assert.equal(classifyProviderHttpError('deepseek', undefined),'PROVIDER_ERROR');
  assert.equal(classifyProviderHttpError('deepseek', '402'),'PROVIDER_ERROR');
});
