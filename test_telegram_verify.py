import unittest
from unittest.mock import patch
from telegram_verify import verify, api, VerificationError
TOKEN='123456789:'+('x'*35)
PHRASE='ETF-CHECK-'+('a'*24)
NOW=1800000000

def message(cid=123, **extra):
    return {'message':{'text':PHRASE,'chat':{'id':cid,'type':'private'},'from':{'id':cid,'is_bot':False},'date':NOW,**extra}}

def caller(updates=None, bot='daniel45_etf_bot',url=''):
    calls=[]
    def call(token,method,body):
        calls.append((method,body))
        return {'getMe':{'is_bot':True,'username':bot},'getWebhookInfo':{'url':url},'getUpdates':updates if updates is not None else [message()]}[method]
    return call,calls

class VerificationTests(unittest.TestCase):
    def test_minimal_nonconsuming_calls(self):
        call,calls=caller(); self.assertEqual(verify(TOKEN,PHRASE,call,NOW),123)
        self.assertEqual(calls,[('getMe',{}),('getWebhookInfo',{}),('getUpdates',{'limit':100,'timeout':0})])
    def test_wrong_bot_stops_before_updates(self):
        call,calls=caller(bot='other_bot')
        with self.assertRaises(VerificationError):verify(TOKEN,PHRASE,call,NOW)
        self.assertEqual(len(calls),1)
    def test_webhook_preserved(self):
        call,calls=caller(url='https://existing.invalid/private')
        with self.assertRaises(VerificationError):verify(TOKEN,PHRASE,call,NOW)
        self.assertEqual(len(calls),2)
    def test_reject_ambiguous_stale_forwarded_group_or_mismatch(self):
        for updates in [[message(123),message(456)],[message(date=NOW-901)],[message(forward_origin={})],[message(forward_from_chat={})],[message(forward_date=NOW)],[message(is_automatic_forward=True)],[message(chat={'id':123,'type':'group'})],[message(text='wrong')],[message(**{'from':{'id':456,'is_bot':False}})]]:
            call,_=caller(updates)
            with self.assertRaises(VerificationError):verify(TOKEN,PHRASE,call,NOW)
    def test_missing_secret_and_bad_phrase(self):
        for token,phrase in [('',PHRASE),(TOKEN,'anything')]:
            with self.assertRaises(VerificationError):verify(token,phrase,lambda *a:self.fail('network'),NOW)
    def test_no_error_url_or_token_leak(self):
        with patch('telegram_verify.http.client.HTTPSConnection',side_effect=RuntimeError('https://api.telegram.org/bot'+TOKEN)):
            with self.assertRaises(VerificationError) as error:api(TOKEN,'getMe',{})
            self.assertNotIn(TOKEN,str(error.exception));self.assertNotIn('https:',str(error.exception))
if __name__=='__main__': unittest.main()
