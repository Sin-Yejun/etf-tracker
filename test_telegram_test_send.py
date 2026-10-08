import unittest
from unittest.mock import patch
from telegram_test_send import send_once,request,TestSendError,DeliveryUncertain,TEST_TEXT
TOKEN='123456789:'+('x'*35)
class SendTests(unittest.TestCase):
    def fake(self,bot='daniel45_etf_bot',private=True,uncertain=False):
        calls=[]
        def call(token,method,payload):
            calls.append((method,payload))
            if method=='getMe':return {'is_bot':True,'username':bot}
            if method=='getChat':return {'id':123,'type':'private' if private else 'group'}
            if uncertain:raise DeliveryUncertain('Unknown')
            return {'message_id':1,'chat':{'id':123}}
        return call,calls
    def test_exact_one_approved_message(self):
        call,calls=self.fake();self.assertTrue(send_once(TOKEN,'123','SEND_ONE_TEST',call))
        self.assertEqual([m for m,p in calls],['getMe','getChat','sendMessage'])
        self.assertEqual(calls[-1][1]['text'],TEST_TEXT)
        self.assertFalse(calls[-1][1]['allow_paid_broadcast'])
    def test_wrong_bot_or_group_never_sends(self):
        for kwargs in ({'bot':'other_bot'},{'private':False}):
            call,calls=self.fake(**kwargs)
            with self.assertRaises(TestSendError):send_once(TOKEN,'123','SEND_ONE_TEST',call)
            self.assertNotIn('sendMessage',[m for m,p in calls])
    def test_missing_confirmation_or_secret(self):
        for token,cid,confirm in [('', '123','SEND_ONE_TEST'),(TOKEN,'-1','SEND_ONE_TEST'),(TOKEN,'123','')]:
            with self.assertRaises(TestSendError):send_once(token,cid,confirm,lambda *a:self.fail('Network'))
    def test_uncertain_never_retries(self):
        call,calls=self.fake(uncertain=True)
        with self.assertRaises(DeliveryUncertain):send_once(TOKEN,'123','SEND_ONE_TEST',call)
        self.assertEqual(sum(m=='sendMessage' for m,p in calls),1)
    def test_transport_failure_never_leaks_token(self):
        with patch('telegram_test_send.http.client.HTTPSConnection',side_effect=RuntimeError('https://api.telegram.org/bot'+TOKEN)):
            with self.assertRaises(DeliveryUncertain) as error:request(TOKEN,'sendMessage',{})
            self.assertNotIn(TOKEN,str(error.exception))
            self.assertNotIn('https:',str(error.exception))
if __name__=='__main__':unittest.main()
