import { createHash } from 'node:crypto';
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const clean = value => String(value).replace(/[\r\n\u0000-\u001f\u007f]/g,' ').trim();
const positive = value => /^\d+(\.\d+)?$/.test(value) && /[1-9]/.test(value);
// Pure planning only: no network, credentials, scheduling or database writes.
export function planNotification({before,after,cuBefore,cuAfter,recipientBinding,compare,includeUnchanged=false}) {
 if (!before || !after) return {state:'missing_data',messages:[]};
 if (!recipientBinding || before.fund!==after.fund || before.date>=after.date) throw Error('Invalid comparison or unverified recipient binding');
 if (cuBefore!==cuAfter || !Number.isSafeInteger(cuBefore) || cuBefore<=0) return {state:'incomparable_cu',messages:[]};
 for (const snapshot of [before,after]) {
  if(!snapshot.sha256 || !snapshot.sourceUrl || !Array.isArray(snapshot.rows) || !snapshot.rows.some(r=>r.kind==='stock')) return {state:'invalid_source',messages:[]};
  if(new Set(snapshot.rows.map(r=>r.id)).size!==snapshot.rows.length) throw Error('Duplicate source identity');
 }
 const equities=s=>s.rows.filter(r=>r.kind==='stock');
 const rows=compare(equities(before),equities(after)).filter(r=>r.delta!=='0').map(r=>({...r,event:!positive(r.before)&&positive(r.after)?'신규':positive(r.before)&&!positive(r.after)?'편출':'수량'}));
 rows.sort((a,b)=>(a.event==='신규'?0:a.event==='편출'?1:2)-(b.event==='신규'?0:b.event==='편출'?1:2)||a.id.localeCompare(b.id));
 const vector=s=>equities(s).map(r=>[r.id,r.quantity]).sort((a,b)=>a[0].localeCompare(b[0]));
 const key=hash({version:'telegram-quantity-v1',recipientBinding,fund:after.fund,dates:[before.date,after.date],cu:[cuBefore,cuAfter],vectors:[vector(before),vector(after)]});
 if(!rows.length&&!includeUnchanged)return {state:'unchanged',key,rows,messages:[]};
 const title=`${after.fund.toUpperCase()} 수량 공시 ${before.date} → ${after.date}`;
 const lines=[title,`CU ${cuAfter.toLocaleString('en-US')}좌 기준 · 실제 체결 내역 아님`,...rows.map(r=>`${r.event} ${clean(r.code||r.id)} | ${clean(r.name)}: ${r.before} → ${r.after} (${r.delta.startsWith('-')?'':'+'}${r.delta})`),...(!rows.length?['주식 수량 변화 없음']:[]),`공식 원본(이전): ${before.sourceUrl}`,`공식 원본(이후): ${after.sourceUrl}`];
 // UTF-16 length is conservative for Telegram's 4096-character limit.
 const chunks=[];let current='';for(const line of lines){if(line.length>3400)throw Error('Source field exceeds message safety limit');if((current+'\n'+line).length>3400){chunks.push(current);current=title+' (계속)';}current+=(current?'\n':'')+line;}if(current)chunks.push(current);
 return {state:rows.length?'changed':'unchanged',key,rows,messages:chunks.map((text,index)=>({partKey:hash([key,index]),text:`[${index+1}/${chunks.length}]\n${text}`,parse_mode:undefined,link_preview_options:{is_disabled:true},allow_paid_broadcast:false})),provenance:{beforeHash:before.sha256,afterHash:after.sha256}};
}
// Outbox transitions. Persist an atomic unique partKey claim in D1 before dispatch.
// In-flight lease expiry is uncertain, not eligible for blind retries.
export function deliveryOutcome({ok,messageId,errorCode,retryAfter,networkError}){
 if(ok===true&&Number.isSafeInteger(messageId))return {state:'sent',messageId};
 if(networkError || ok===undefined)return {state:'uncertain',autoRetry:false};
 if(errorCode===429&&Number.isFinite(retryAfter)&&retryAfter>=0)return {state:'retry_wait',delaySeconds:Math.max(1,Math.ceil(retryAfter))};
 if(errorCode>=500)return {state:'uncertain',autoRetry:false};
 return {state:'blocked',autoRetry:false};
}
export function canClaim(row,now){return !row || row.state==='retry_wait'&&row.notBefore<=now;}
