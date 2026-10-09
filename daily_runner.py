"""Daily private ETF disclosures. Scheduled sends only; manual invocation is dry-run."""
import datetime as dt
from copy import deepcopy
import hashlib
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo
from etf_collector.collect import download
from etf_collector.core import FUNDS,parse_official,source_url
from cu_validation import fetch_evidence,current_profile,parse_cu,CUError
from daily_engine import new_state,ingest_collection,pending_parts,begin_send,finish_send,recover_inflight,recipient_key
from github_history import GitHubHistory,StateStore,HistoryError
from telegram_test_send import request,EXPECTED_BOT

LOOKBACK_DAYS=7
MAX_SEND_PARTS=8

def latest_collection(fund,today,fetch=download,cu_fetch=fetch_evidence,historical_profiles=None):
    attempts=[]
    for age in range(LOOKBACK_DAYS):
        date=(today-dt.timedelta(days=age)).isoformat()
        try:
            raw,headers=fetch(fund,date)
            digest=hashlib.sha256(raw).hexdigest()
            if headers.get('httpStatus')!=200:
                attempts.append({'date':date,'status':'http_error','httpStatus':headers.get('httpStatus'),'sha256':digest});continue
            parsed=parse_official(raw,fund,date,headers.get('contentDisposition',''))
            collection={'fund':fund,'requestedDate':date,'asOf':date,'status':'verified_no_baseline','provenanceMode':'live_issuer_probe',
                        'collectedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'sourceUrl':source_url(fund,date),'sha256':digest,
                        'originalKey':f'runtime/originals/{fund}/{date}/{digest}'+('.xlsx' if fund=='time' else '.xls'),
                        'response':headers,'errors':[],**parsed}
        except Exception:
            attempts.append({'date':date,'status':'download_or_validation_error'});continue
        attempts.append({'date':date,'status':'verified_source','sha256':digest})
        evidence=None
        cu_stage='cu_fetch_failed'
        try:
            evidence=cu_fetch(fund)
            cu_stage='cu_identity_schema_or_value_unverified'
            current_cu=parse_cu(fund,evidence)
            cu_stage='historical_cu_evidence_missing' if date!=today.isoformat() else 'cu_date_proof_unverified'
            try:profile=current_profile(fund,date,evidence)
            except CUError:
                previous=(historical_profiles or {}).get(date)
                if not isinstance(previous,dict) or str(previous.get('cu'))!=str(current_cu):raise
                profile=deepcopy(previous)
            collection['cu']=profile['cu']
            collection['cuDiagnostic']='fresh_date_profile' if date==today.isoformat() else 'stored_historical_profile'
        except Exception:
            profile=None
            collection['cuDiagnostic']=cu_stage
            if date!=today.isoformat():
                collection['status']='cu_unverified_historical';collection['errors']=['Historical CU evidence unavailable; not inferred from current undated page']
        return collection,raw,profile,evidence,attempts
    return {'fund':fund,'requestedDate':today.isoformat(),'asOf':None,'status':'source_error','errors':['No date-verified official disclosure in bounded lookback']},None,None,None,attempts


def validate_recipient(token,cid,call=request):
    if not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,100}',token or '') or not re.fullmatch(r'[1-9]\d{0,15}',cid or ''):
        raise ValueError('Required Telegram secrets are missing or invalid')
    me=call(token,'getMe',{})
    if not isinstance(me,dict) or me.get('is_bot') is not True or str(me.get('username','')).lower()!=EXPECTED_BOT:
        raise ValueError('Expected bot identity not confirmed')
    chat=call(token,'getChat',{'chat_id':int(cid)})
    if not isinstance(chat,dict) or chat.get('type')!='private' or type(chat.get('id')) is not int or chat['id']!=int(cid):
        raise ValueError('Configured private recipient not confirmed')


def dispatch_pending(state,store,token,cid,run_id,call=request,sleep=time.sleep,eligible_funds=None):
    sent=0
    for sequence in range(MAX_SEND_PARTS):
        eligible=[p for p in pending_parts(state,now=int(time.time())) if p['recipientKey']==recipient_key(cid) and (eligible_funds is None or p['fund'] in eligible_funds)]
        if not eligible:break
        part=eligible[0];attempt=f'{run_id}:{sequence}'
        claimed,ok=begin_send(state,part['eventKey'],part['partIndex'],now=int(time.time()),attempt_id=attempt)
        if not ok:break
        store.save(claimed)  # MUST succeed and read back before any submission.
        state=claimed
        try:
            result=call(token,'sendMessage',{'chat_id':int(cid),'text':part['text'],'link_preview_options':{'is_disabled':True},'allow_paid_broadcast':False})
            if not isinstance(result,dict) or type(result.get('message_id')) is not int or result['message_id']<=0 or not isinstance(result.get('chat'),dict) or result['chat'].get('id')!=int(cid):
                raise ValueError('Unverified acceptance')
            completed=finish_send(state,part['eventKey'],part['partIndex'],now=int(time.time()),attempt_id=attempt,outcome='sent',receipt={'messageId':result['message_id'],'recipientKey':recipient_key(cid)})
        except Exception:
            # Even explicit errors are conservatively quarantined here. No blind
            # retries; uncertainty blocks later events for the affected fund.
            completed=finish_send(state,part['eventKey'],part['partIndex'],now=int(time.time()),attempt_id=attempt,outcome='uncertain')
        store.save(completed) # If this fails, persisted sending -> uncertain next run.
        state=completed
        if completed['outbox'][part['eventKey']]['parts'][part['partIndex']]['state']=='sent':sent+=1
        sleep(1.2) # Per-recipient rate limit across both funds.
    return state,sent


def run(dry_run=True):
    now=dt.datetime.now(ZoneInfo('Asia/Seoul'));today=now.date()
    if not dry_run and today.weekday()>4:
        print('Outside approved Korean weekday; no collection or send');return 0
    token=os.getenv('TELEGRAM_BOT_TOKEN','') if not dry_run else ''
    cid=os.getenv('TELEGRAM_CHAT_ID','') if not dry_run else '1'
    history=store=None
    if dry_run:state=new_state()
    else:
        validate_recipient(token,cid)
        history=GitHubHistory(os.getenv('GITHUB_TOKEN',''));store=StateStore(history)
        loaded=store.load()
        state=new_state() if loaded is None else loaded
        state=recover_inflight(state,now=int(time.time()));store.save(state)
    run_id=os.getenv('GITHUB_RUN_ID','local')+'-'+os.getenv('GITHUB_RUN_ATTEMPT','1')
    if not re.fullmatch(r'[A-Za-z0-9-]+',run_id):raise ValueError('Invalid run identity')
    report={'runId':run_id,'checkedAt':now.isoformat(),'mode':'dry_run' if dry_run else 'scheduled','results':[]}
    for fund in FUNDS:
        prior={date:snap.get('cuProfile') for date,snap in state.get('funds',{}).get(fund,{}).get('snapshots',{}).items()}
        collection,raw,profile,evidence,attempts=latest_collection(fund,today,historical_profiles=prior)
        if history and raw is not None:
            history.immutable(collection['originalKey'],raw)
            if evidence is not None:
                key=f"runtime/cu-evidence/{fund}/{hashlib.sha256(evidence).hexdigest()}.bin"
                history.immutable(key,evidence)
                if profile and isinstance(profile.get('evidence'),dict) and profile['evidence'].get('sha256')==hashlib.sha256(evidence).hexdigest():
                    profile['evidence']['originalKey']=key
        state,outcome=ingest_collection(state,collection,cid,profile,now=int(time.time()))
        if store:store.save(state)
        report['results'].append({'fund':fund,'outcome':outcome,'attempts':attempts,'cuVerified':profile is not None,'cuDiagnostic':collection.get('cuDiagnostic','not_checked'),'sourceStatus':collection['status']})
        print(f"{fund}: {outcome['status']} · source {outcome.get('asOf') or 'unverified'}")
        print(json.dumps({'fund':fund,'sourceStatus':collection['status'],'cuDiagnostic':collection.get('cuDiagnostic','not_checked'),'reason':outcome.get('reason'),'attempts':[{k:a[k] for k in ('date','status','httpStatus') if k in a} for a in attempts]},ensure_ascii=False,sort_keys=True))
    report['acceptedMessages']=0
    if not dry_run:
        # No fresh changed disclosure this run: do not send old queued data on
        # a holiday, duplicate disclosure, or source-failure-only run.
        if any(r['outcome']['status']=='changed' for r in report['results']):
            state,report['acceptedMessages']=dispatch_pending(state,store,token,cid,run_id,eligible_funds={r['fund'] for r in report['results'] if r['outcome']['status']=='changed'})
        report['pendingBacklog']=sum(event['state'] in ('pending','retry_wait') for event in state['outbox'].values())
        report['outboxNeedsReview']=sum(event['state'] in ('uncertain','blocked') or bool(event.get('holdReason')) for event in state['outbox'].values())
        report['runUrl']='https://github.com/Sin-Yejun/etf-tracker/actions/runs/'+os.getenv('GITHUB_RUN_ID','')
        report_bytes=(json.dumps(report,ensure_ascii=False,sort_keys=True)+'\n').encode()
        history.immutable(f'runtime/runs/{run_id}.json',report_bytes)
        history.put('runtime/latest-run.json',report_bytes,history.read('runtime/latest-run.json'))
    print('No messages sent in dry-run' if dry_run else 'Scheduled run recorded; accepted messages: '+str(report['acceptedMessages']))
    failed=any(r['outcome']['status'] in ('source_error','held') or not r['cuVerified'] for r in report['results']) or report.get('outboxNeedsReview',0)>0 or report.get('pendingBacklog',0)>0
    return int(failed)


def main():
    try:
        event=os.getenv('GITHUB_EVENT_NAME')
        if os.getenv('GITHUB_REPOSITORY')!='Sin-Yejun/etf-tracker' or os.getenv('GITHUB_REF')!='refs/heads/main':
            raise ValueError('Exact private-repository main-branch workflow required')
        if event not in ('schedule','workflow_dispatch'):raise ValueError('Unsupported trigger')
        return run(dry_run=event!='schedule')
    except BaseException:
        print('Daily run stopped safely. Do not infer unchanged holdings or retry an uncertain send. Inspect persisted status; no secret diagnostics are printed.')
        return 1
if __name__=='__main__':sys.exit(main())
