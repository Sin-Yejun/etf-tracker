import copy,datetime as dt,hashlib,json,unittest
from pathlib import Path
from unittest.mock import patch
from daily_runner import latest_collection,dispatch_pending,validate_recipient,notice_text,send_notice,notice_already_sent
from daily_engine import new_state,ingest_collection,pending_parts,recover_inflight
from github_history import HistoryError
ROOT=Path(__file__).parent
SEED=json.loads((ROOT/'fixtures/seed.json').read_text())

def collection(date):
 s=copy.deepcopy(next(s for s in SEED if s['fund']=='koact' and s['date']==date))
 s.update(asOf=date,requestedDate=date,status='verified_no_baseline',provenanceMode='live_issuer_probe',response={'httpStatus':200,'responseUrl':s['sourceUrl']},dateEvidence={'type':'workbook_date','value':date.replace('-','/')},cu=50000,errors=[])
 return s

def profile(date):return {'verified':True,'fund':'koact','cu':50000,'validFrom':date,'validThrough':date,'evidence':{'sourceUrl':'https://www.samsungactive.co.kr/api/v1/product/etf/2ETFQ1.do','sha256':'a'*64,'basis':'test fixture official CU evidence'}}

def queued():
 state=new_state()
 for i,date in enumerate(['2026-10-07','2026-10-08']):state,_=ingest_collection(state,collection(date),'123',profile(date),now=1800000000+i)
 return state
class MemoryStore:
 def __init__(self,state,fail_at=0):self.state=copy.deepcopy(state);self.calls=0;self.fail_at=fail_at
 def save(self,state):
  self.calls+=1
  if self.calls==self.fail_at:raise HistoryError('simulated write failure')
  self.state=copy.deepcopy(state)
class RunnerTests(unittest.TestCase):
 def test_newest_real_date_no_relabel(self):
  source=collection('2026-10-08');raw=(ROOT/'fixtures/originals/koact-2026-10-08.xls').read_bytes()
  def fetch(fund,date):
   if date!='2026-10-08':return b'not published',{'httpStatus':404}
   return raw,{'httpStatus':200,'responseUrl':source['sourceUrl'],'contentDisposition':''}
  body=json.dumps({'info':{'product':{'fId':'2ETFQ1','stkTicker':'0015B0','fNm':'KoAct 미국나스닥성장기업액티브','qPerCu':'50000'}}}).encode()
  result,*_=latest_collection('koact',dt.date(2026,10,9),fetch,lambda f:body)
  self.assertEqual(result['asOf'],'2026-10-08');self.assertEqual(result['requestedDate'],'2026-10-08');self.assertEqual(result['cuDiagnostic'],'historical_cu_evidence_missing')
 def test_no_data_is_error_not_unchanged(self):
  result,*_=latest_collection('koact',dt.date(2026,10,9),lambda *a:(b'',{'httpStatus':522}),lambda f:b'')
  self.assertEqual(result['status'],'source_error');self.assertIsNone(result['asOf'])
 def test_persist_claim_before_send_and_restart_no_duplicate(self):
  state=queued();store=MemoryStore(state);calls=[]
  def call(token,method,payload):
   self.assertTrue(any(e['state']=='sending' for e in store.state['outbox'].values()))
   calls.append(method);return {'message_id':len(calls),'chat':{'id':123}}
  with patch('daily_runner.time.time',return_value=1800000002):state,sent=dispatch_pending(state,store,'fake','123','run1',call,lambda _:None)
  self.assertGreater(sent,0)
  before=len(calls)
  with patch('daily_runner.time.time',return_value=1800000003):state,sent=dispatch_pending(store.state,store,'fake','123','run2',call,lambda _:None)
  self.assertEqual(len(calls),before);self.assertEqual(sent,0)
 def test_failed_claim_never_sends(self):
  state=queued();store=MemoryStore(state,fail_at=1)
  with patch('daily_runner.time.time',return_value=1800000002),self.assertRaises(HistoryError):dispatch_pending(state,store,'fake','123','run',lambda *a:self.fail('send'),lambda _:None)
 def test_acceptance_then_state_failure_quarantines(self):
  state=queued();store=MemoryStore(state,fail_at=2);calls=[]
  def call(*a):calls.append(1);return {'message_id':1,'chat':{'id':123}}
  with patch('daily_runner.time.time',return_value=1800000002),self.assertRaises(HistoryError):dispatch_pending(state,store,'fake','123','run',call,lambda _:None)
  recovered=recover_inflight(store.state,now=1800000003)
  self.assertEqual(len(calls),1);self.assertEqual(pending_parts(recovered,now=1800000003),[])
 def test_timeout_no_retry(self):
  state=queued();store=MemoryStore(state);calls=[]
  def call(*a):calls.append(1);raise TimeoutError()
  with patch('daily_runner.time.time',return_value=1800000002):state,sent=dispatch_pending(state,store,'fake','123','run',call,lambda _:None)
  self.assertEqual(len(calls),1);self.assertEqual(sent,0);self.assertEqual(pending_parts(state,now=1800000003),[])
 def test_unchanged_fund_backlog_not_flushed(self):
  state=queued();store=MemoryStore(state)
  with patch('daily_runner.time.time',return_value=1800000002):
   result,sent=dispatch_pending(state,store,'fake','123','run',lambda *a:self.fail('unchanged fund send'),lambda _:None,eligible_funds={'time'})
  self.assertEqual(sent,0);self.assertEqual(store.calls,0)
 def test_unchanged_notice_text_and_verified_send(self):
  notice={'fund':'koact','kind':'unchanged','beforeDate':'2026-10-07','afterDate':'2026-10-08','cu':50000}
  text=notice_text(notice)
  self.assertIn('보유수량 변동 없음',text);self.assertIn('2026-10-07 → 2026-10-08',text);self.assertNotIn('http',text)
  calls=[]
  def call(token,method,payload):calls.append(payload);return {'message_id':7,'chat':{'id':123}}
  self.assertEqual(send_notice(notice,'fake','123',call),7);self.assertEqual(calls[0]['text'],text);self.assertEqual(calls[0]['chat_id'],123)
 def test_no_new_disclosure_notice_does_not_claim_unchanged(self):
  notice={'fund':'koact','kind':'no_new_disclosure','afterDate':'2026-10-08','today':'2026-10-09'}
  text=notice_text(notice)
  self.assertIn('오늘 새 공시 없음',text);self.assertIn('최신 공시 기준일: 2026-10-08',text);self.assertNotIn('수량은 그대로',text)
 def test_notice_send_to_wrong_recipient_is_rejected(self):
  notice={'fund':'koact','kind':'unchanged','beforeDate':'2026-10-07','afterDate':'2026-10-08','cu':50000}
  with self.assertRaises(ValueError):send_notice(notice,'fake','123',lambda *a:{'message_id':1,'chat':{'id':999}})
 def test_notice_already_sent_matches_same_day_and_disclosure(self):
  class H:
   def __init__(self,doc):self.doc=doc
   def read(self,path):return self.doc
  doc=json.dumps({'checkedAt':'2026-10-09T08:30:00+09:00','notices':[{'fund':'koact','kind':'unchanged','afterDate':'2026-10-08','status':'sent'}]}).encode()
  notice={'fund':'koact','kind':'no_new_disclosure','afterDate':'2026-10-08','today':'2026-10-09'}
  self.assertTrue(notice_already_sent(H(doc),dt.date(2026,10,9),notice))
  self.assertFalse(notice_already_sent(H(doc),dt.date(2026,10,10),notice))
  self.assertFalse(notice_already_sent(H(None),dt.date(2026,10,9),notice))
  self.assertFalse(notice_already_sent(H(doc),dt.date(2026,10,9),{**notice,'fund':'time'}))
  rerun=json.dumps({'checkedAt':'2026-10-09T09:00:00+09:00','notices':[{'fund':'koact','kind':'no_new_disclosure','afterDate':'2026-10-08','status':'already_sent_today'}]}).encode()
  self.assertTrue(notice_already_sent(H(rerun),dt.date(2026,10,9),notice))
  failed=json.dumps({'checkedAt':'2026-10-09T09:00:00+09:00','notices':[{'fund':'koact','kind':'no_new_disclosure','afterDate':'2026-10-08','status':'send_failed'}]}).encode()
  self.assertFalse(notice_already_sent(H(failed),dt.date(2026,10,9),notice))
 def test_cu_fetch_failure_is_per_fund_hold_not_crash(self):
  raw=(ROOT/'fixtures/originals/koact-2026-10-08.xls').read_bytes()
  def cu_fetch(f):raise TimeoutError()
  result,body,cu,evidence,attempts=latest_collection('koact',dt.date(2026,10,8),lambda *a:(raw,{'httpStatus':200,'contentDisposition':''}),cu_fetch)
  self.assertIsNone(cu);self.assertIsNone(evidence);self.assertEqual(result['asOf'],'2026-10-08');self.assertEqual(result['cuDiagnostic'],'cu_fetch_failed')
if __name__=='__main__':unittest.main()
