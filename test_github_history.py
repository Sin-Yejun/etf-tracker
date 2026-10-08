import unittest
from github_history import GitHubHistory,HistoryError,StateStore
class FakeHistory(GitHubHistory):
    def __init__(self):self.files={};self.fail=False;self.writes=0
    def request(self,method,path,payload=None,raw=False):
        import base64
        if method=='GET':return self.files.get(path)
        self.writes+=1
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
