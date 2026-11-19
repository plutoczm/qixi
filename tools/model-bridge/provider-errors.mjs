/** Fail closed on unsupported or ambiguous provider statuses; no raw response bodies. */
export function classifyProviderHttpError(provider, status) {
  if (!Number.isInteger(status)) return 'PROVIDER_ERROR';
  if (status === 401) return 'AUTHENTICATION_FAILED';
  if (status === 403) return 'ACCESS_DENIED';
  // Official DeepSeek API: 402 is specifically "Insufficient Balance".
  // Do not generalize this to arbitrary relays or other providers.
  if (status === 402 && provider === 'deepseek') return 'INSUFFICIENT_BALANCE';
  if (status === 429) return 'RATE_LIMITED';
  if (status === 500 || status === 503 || status === 502 || status === 504 || status === 529) {
    return 'PROVIDER_UNAVAILABLE';
  }
  return 'PROVIDER_ERROR';
}
