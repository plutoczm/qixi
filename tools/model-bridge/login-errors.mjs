export function loginFailure(error){
 const codes=new Set(['ECONNRESET','ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UNABLE_TO_VERIFY_LEAF_SIGNATURE','DEPTH_ZERO_SELF_SIGNED_CERT','EADDRINUSE','ABORT_ERR']);
 const messages=[];const cause_codes=[];let current=error;
 for(let n=0;n<4&&current;n++,current=current.cause){
  if(typeof current.message==='string')messages.push(current.message);
  if(codes.has(current.code))cause_codes.push(current.code);
 }
 const text=messages.join(' ');
 const http=/token (?:exchange|refresh|request) failed \((\d{3})\)/.exec(text);
 const reasons=[['invalid_grant','INVALID_GRANT'],['invalid_client','INVALID_CLIENT'],['access_denied','ACCESS_DENIED'],['State mismatch','STATE_MISMATCH'],['accountId','ACCOUNT_METADATA_MISSING'],['Login cancelled','CANCELLED'],['LOGIN_CANCELLED','CANCELLED'],['AUTH_STORAGE','AUTH_STORAGE_ERROR']];
 const reason=reasons.find(([needle])=>text.includes(needle))?.[1]??'UNKNOWN_LOGIN_FAILURE';
 return {reason,http_status:http?Number(http[1]):null,cause_codes:[...new Set(cause_codes)]};
}
