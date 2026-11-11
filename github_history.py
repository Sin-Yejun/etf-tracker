"""Repository-scoped durable files with GitHub blob-SHA compare-and-swap.

Only the user's scheduled runner supplies GITHUB_TOKEN. No token is persisted.
"""
import base64
import hashlib
import http.client
import json
import re
import ssl
import time

REPOSITORY='Sin-Yejun/etf-tracker'
# Bot commits go to their own branch so main keeps only human history.
BRANCH='runtime'
MAX_STATE=32*1024*1024
# Backoff before re-sending a write that verifiably did not land.
RETRY_DELAYS=(2,5,10)

class HistoryError(Exception): pass

class GitHubHistory:
    def __init__(self,token):
        if not token:raise HistoryError('Repository runtime token unavailable')
        self.token=token
    def request(self,method,path,payload=None,raw=False):
        if not re.fullmatch(r'runtime/[A-Za-z0-9_./-]+',path) or '..' in path:
            raise HistoryError('Unsafe history path')
        c=None
        try:
            c=http.client.HTTPSConnection('api.github.com',timeout=20,context=ssl.create_default_context())
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github.raw+json' if raw else 'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'etf-private-history','Content-Type':'application/json'}
            c.request(method,'/repos/'+REPOSITORY+'/contents/'+path+('?ref='+BRANCH if method=='GET' else ''),body=None if payload is None else json.dumps(payload).encode(),headers=headers)
            r=c.getresponse();body=r.read(MAX_STATE+1)
            if len(body)>MAX_STATE:raise HistoryError('History size limit reached; stop for maintenance')
            if r.status==404 and method=='GET':return None
            if r.status not in (200,201):raise HistoryError('Repository history request rejected; no notification is safe')
            return body if raw else json.loads(body)
        except HistoryError:raise
        except Exception:raise HistoryError('Repository history request failed or outcome uncertain; no notification is safe') from None
        finally:
            if c:
                try:c.close()
                except Exception:pass
    sleep=staticmethod(time.sleep)
    def read(self,path):
        # GETs are side-effect free, so transient failures are retried.
        for delay in RETRY_DELAYS:
            try:return self.request('GET',path,raw=True)
            except HistoryError:self.sleep(delay)
        return self.request('GET',path,raw=True)
    @staticmethod
    def blob_sha(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    def put(self,path,data,previous=None):
        if len(data)>MAX_STATE:raise HistoryError('History size limit reached')
        payload={'message':'Persist ETF disclosure and notification state','branch':BRANCH,'content':base64.b64encode(data).decode()}
        if previous is not None:payload['sha']=self.blob_sha(previous)
        # Ambiguous writes are reconciled by a fresh read. A write is re-sent
        # only when the read proves it did not land (content still equals the
        # expected previous version), so a retry never overwrites another
        # writer's data: the compare-and-swap SHA is unchanged and still valid.
        for attempt in range(len(RETRY_DELAYS)+1):
            try:
                self.request('PUT',path,payload);break
            except HistoryError:
                current=self.read(path)
                if current==data:break
                if current!=previous or attempt==len(RETRY_DELAYS):raise
                self.sleep(RETRY_DELAYS[attempt])
        if self.read(path)!=data:raise HistoryError('History write readback mismatch')
    def immutable(self,path,data):
        previous=self.read(path)
        if previous is not None:
            if previous!=data:raise HistoryError('Immutable history collision')
            return
        self.put(path,data)

class StateStore:
    def __init__(self,history):self.history=history;self.previous=None
    def load(self):
        self.previous=self.history.read('runtime/state.json')
        if self.previous is None:
            if self.history.read('runtime/initialized.json') is not None:
                raise HistoryError('History marker exists but state is missing; refuse baseline reset')
            return None
        try:
            value=json.loads(self.previous)
            if not isinstance(value,dict) or value.get('schemaVersion')!=1 or not isinstance(value.get('funds'),dict) or not isinstance(value.get('outbox'),dict):
                raise ValueError()
            return value
        except Exception:raise HistoryError('Invalid durable state; refuse baseline reset') from None
    def save(self,state):
        data=(json.dumps(state,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
        self.history.put('runtime/state.json',data,self.previous)
        self.previous=data
        self.history.immutable('runtime/initialized.json',b'{"version":1}\n')
