import unittest
from github_history import GitHubHistory,HistoryError,StateStore
class FakeHistory(GitHubHistory):
    def __init__(self):self.files={};self.fail=False;self.writes=0;self.reject_puts=0;self.fail_gets=0;self.sleeps=[]
    def sleep(self,seconds):self.sleeps.append(seconds)
    def request(self,method,path,payload=None,raw=False):
        import base64
        if method=='GET':
            if self.fail_gets:self.fail_gets-=1;raise HistoryError('Transient read failure')
            return self.files.get(path)
        self.writes+=1
        if self.reject_puts:self.reject_puts-=1;raise HistoryError('Transient rejection, nothing written')
        old=self.files.get(path)
        if old is not None and payload.get('sha')!=self.blob_sha(old):raise HistoryError('CAS conflict')
        self.files[path]=base64.b64decode(payload['content'])
        if self.fail:raise HistoryError('Response lost')
        return {}
class HistoryTests(unittest.TestCase):
    def test_persistent_readback_and_restart(self):
        h=FakeHistory();store=StateStore(h);self.assertIsNone(store.load());store.save({'schemaVersion':1,'funds':{},'outbox':{}})
        self.assertEqual(StateStore(h).load(),{'schemaVersion':1,'funds':{},'outbox':{}})
    def test_uncertain_write_reconciled(self):
        h=FakeHistory();h.fail=True;h.put('runtime/a.json',b'yes');self.assertEqual(h.writes,1)
    def test_cas_conflict_no_overwrite(self):
        h=FakeHistory();h.files['runtime/a.json']=b'newer'
        with self.assertRaises(HistoryError):h.put('runtime/a.json',b'mine',b'older')
        self.assertEqual(h.files['runtime/a.json'],b'newer')
    def test_transient_rejection_retried_with_same_cas(self):
        h=FakeHistory();h.files['runtime/state.json']=b'old';h.reject_puts=2
        h.put('runtime/state.json',b'new',b'old')
        self.assertEqual(h.files['runtime/state.json'],b'new');self.assertEqual(h.writes,3);self.assertEqual(h.sleeps,[2,5])
    def test_persistent_rejection_gives_up(self):
        h=FakeHistory();h.files['runtime/state.json']=b'old';h.reject_puts=99
        with self.assertRaises(HistoryError):h.put('runtime/state.json',b'new',b'old')
        self.assertEqual(h.files['runtime/state.json'],b'old');self.assertEqual(h.writes,4)
    def test_conflict_is_not_retried(self):
        h=FakeHistory();h.files['runtime/a.json']=b'newer'
        with self.assertRaises(HistoryError):h.put('runtime/a.json',b'mine',b'older')
        self.assertEqual(h.writes,1);self.assertEqual(h.sleeps,[])
    def test_transient_read_failure_retried(self):
        h=FakeHistory();h.files['runtime/a.json']=b'x';h.fail_gets=2
        self.assertEqual(h.read('runtime/a.json'),b'x')
    def test_store_save_survives_transient_rejection(self):
        h=FakeHistory();store=StateStore(h);store.load();store.save({'schemaVersion':1,'funds':{},'outbox':{}})
        h.reject_puts=1;store.save({'schemaVersion':1,'funds':{'time':{}},'outbox':{}})
        self.assertEqual(StateStore(h).load()['funds'],{'time':{}})
    def test_no_silent_reset(self):
        h=FakeHistory();h.files['runtime/initialized.json']=b'{}'
        with self.assertRaises(HistoryError):StateStore(h).load()
    def test_corrupt_falsy_state_never_resets(self):
        for raw in [b'{}',b'[]',b'null',b'false',b'0']:
            h=FakeHistory();h.files['runtime/state.json']=raw
            with self.assertRaises(HistoryError):StateStore(h).load()
            self.assertEqual(h.files['runtime/state.json'],raw)
    def test_immutable_collision(self):
        h=FakeHistory();h.immutable('runtime/raw.xls',b'original')
        with self.assertRaises(HistoryError):h.immutable('runtime/raw.xls',b'changed')
if __name__=='__main__':unittest.main()
