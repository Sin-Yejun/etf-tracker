"""Home-server KOACT relay: relayed bytes go through the unchanged parser/validation path."""
import base64,copy,datetime as dt,importlib.util,json,unittest
from pathlib import Path
from unittest.mock import patch
from daily_runner import latest_collection,load_relay,relay_sources
from daily_engine import new_state,ingest_collection
from etf_collector.core import source_url
from cu_validation import SOURCES
ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('relay_koact',ROOT/'home-server/relay_koact.py')
relay_koact=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay_koact)

XLS=(ROOT/'fixtures/originals/koact-2026-10-08.xls').read_bytes()
CU=json.dumps({'info':{'product':{'fId':'2ETFQ1','stkTicker':'0015B0','fNm':'KoAct 미국나스닥성장기업액티브','qPerCu':'50000'}}}).encode()
TODAY=dt.date(2026,10,8)
FETCHED='2026-10-07T23:00:05+00:00'  # 08:00:05 KST on TODAY

class FakeResponse:
 def __init__(self,url,status,body):self.url=url;self.status=status;self.body=body;self.headers={'Content-Type':'application/octet-stream'}
 def __enter__(self):return self
 def __exit__(self,*a):return False
 def geturl(self):return self.url
 def read(self,n):return self.body[:n]
class FakeOpener:
 def open(self,request,timeout):
  url=request.full_url
  if url==SOURCES['koact']:return FakeResponse(url,200,CU)
  if url==source_url('koact','2026-10-08'):return FakeResponse(url,200,XLS)
  return FakeResponse(url,403,b'blocked')

def built_relay():
 with patch.object(relay_koact.urllib.request,'build_opener',lambda *a:FakeOpener()):relay=relay_koact.build(TODAY)
 relay['fetchedAt']=FETCHED;relay['cuEvidence']['fetchedAt']=FETCHED
 return relay
class History:
 def __init__(self,relay,date=TODAY):self.doc=json.dumps(relay).encode();self.path=f'runtime/relay/koact/{date.isoformat()}.json'
 def read(self,path):return self.doc if path==self.path else None

class RelayTests(unittest.TestCase):
 def test_relayed_disclosure_validates_like_direct_fetch(self):
  relay=load_relay(History(built_relay()),'koact',TODAY)
  self.assertIsNotNone(relay)
  fetch,cu_fetch,observed=relay_sources(relay,'koact')
  collection,raw,profile,evidence,attempts=latest_collection('koact',TODAY,fetch,cu_fetch,cu_observed_at=observed)
  self.assertEqual(collection['asOf'],'2026-10-08');self.assertEqual(raw,XLS);self.assertEqual(evidence,CU)
  self.assertEqual(collection['cuDiagnostic'],'fresh_date_profile');self.assertEqual(profile['observedAt'],FETCHED)
  self.assertEqual(collection['response']['via'],'home_server_relay')
  state,outcome=ingest_collection(new_state(),collection,'123',profile,now=1800000000)
  self.assertNotIn(outcome['status'],('source_error','held'))
 def test_older_dates_walked_back_like_direct(self):
  relay=built_relay();later=dt.date(2026,10,9)
  relay['relayDate']=later.isoformat();relay['fetchedAt']=relay['cuEvidence']['fetchedAt']='2026-10-08T23:00:05+00:00'
  for e in relay['disclosures']:e['date']=(dt.date.fromisoformat(e['date'])+dt.timedelta(days=1)).isoformat();e['url']=source_url('koact',e['date'])
  # Shifted: the 2026-10-08 file now sits at the second entry; re-point it.
  for e in relay['disclosures']:
   if e['date']=='2026-10-08':e.update(body=base64.b64encode(XLS).decode(),sha256=__import__('hashlib').sha256(XLS).hexdigest(),httpStatus=200,responseUrl=e['url'])
  fetch,cu_fetch,observed=relay_sources(load_relay(History(relay,later),'koact',later),'koact')
  collection,*_=latest_collection('koact',later,fetch,cu_fetch,cu_observed_at=observed)
  self.assertEqual(collection['asOf'],'2026-10-08');self.assertEqual(collection['cuDiagnostic'],'historical_cu_evidence_missing')
 def test_stale_or_foreign_relay_ignored(self):
  relay=built_relay()
  self.assertIsNone(load_relay(History(relay),'koact',dt.date(2026,10,9)))
  self.assertIsNone(load_relay(History({**relay,'relayDate':'2026-10-07'},dt.date(2026,10,7)),'koact',dt.date(2026,10,7)))
  self.assertIsNone(load_relay(History({**relay,'fetchedAt':'2026-10-07T12:00:00+00:00'}),'koact',TODAY))
  self.assertIsNone(load_relay(History({**relay,'fund':'time'}),'koact',TODAY))
  class Missing:
   def read(self,path):return None
  self.assertIsNone(load_relay(Missing(),'koact',TODAY))
 def test_tampered_or_misaddressed_bytes_rejected(self):
  relay=built_relay()
  for e in relay['disclosures']:
   if e['date']=='2026-10-08':e['sha256']='0'*64
  fetch,cu_fetch,observed=relay_sources(load_relay(History(relay),'koact',TODAY),'koact')
  collection,*_=latest_collection('koact',TODAY,fetch,cu_fetch,cu_observed_at=observed)
  self.assertEqual(collection['status'],'source_error')
  relay=built_relay();relay['cuEvidence']['url']='https://example.com/cu'
  fetch,cu_fetch,observed=relay_sources(load_relay(History(relay),'koact',TODAY),'koact')
  collection,_,profile,*_=latest_collection('koact',TODAY,fetch,cu_fetch,cu_observed_at=observed)
  self.assertIsNone(profile);self.assertEqual(collection['cuDiagnostic'],'cu_fetch_failed')
 def test_relay_records_blocked_status_without_parsing(self):
  relay=built_relay()
  self.assertEqual(len(relay['disclosures']),7)
  self.assertEqual([e['httpStatus'] for e in relay['disclosures']],[200]+[403]*6)
  self.assertEqual(relay['cuEvidence']['httpStatus'],200)
if __name__=='__main__':unittest.main()
