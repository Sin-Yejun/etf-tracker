import json,unittest
from cu_validation import parse_cu,current_profile,CUError
class CUTests(unittest.TestCase):
    def test_time_identity(self):
        self.assertEqual(parse_cu('time','<title>TIME 미국나스닥100액티브(426030)</title>'.encode()+b' <dt>\xec\x84\xa4\xec\xa0\x95\xeb\x8b\xa8\xec\x9c\x84(\xec\xa2\x8c)</dt><dd>20,000</dd>'),20000)
    def test_koact_identity_and_live_value(self):
        p={'fId':'2ETFQ1','stkTicker':'0015B0','fNm':'KoAct 미국나스닥성장기업액티브','qPerCu':'50000'}
        body=json.dumps({'info':{'product':p}}).encode();self.assertEqual(parse_cu('koact',body),50000)
        self.assertEqual(current_profile('koact','2026-10-09',body,observed_at='2026-10-09T08:30:00+09:00')['validThrough'],'2026-10-09')
        p['stkTicker']='wrong'
        with self.assertRaises(CUError):parse_cu('koact',json.dumps({'info':{'product':p}}).encode())
    def test_bad_schema(self):
        for fund in ['time','koact']:
            with self.assertRaises(CUError):parse_cu(fund,b'{}')
    def test_current_page_cannot_backdate(self):
        body=json.dumps({'info':{'product':{'fId':'2ETFQ1','stkTicker':'0015B0','fNm':'KoAct 미국나스닥성장기업액티브','qPerCu':'50000'}}}).encode()
        with self.assertRaises(CUError):current_profile('koact','2026-10-08',body,observed_at='2026-10-09T08:30:00+09:00')
    def test_changed_cu_rejected(self):
        body=json.dumps({'info':{'product':{'fId':'2ETFQ1','stkTicker':'0015B0','fNm':'KoAct 미국나스닥성장기업액티브','qPerCu':'60000'}}}).encode()
        with self.assertRaises(CUError):parse_cu('koact',body)
    def test_wrong_title_nav_reference_rejected(self):
        body='<title>다른 상품</title><nav>TIME 미국나스닥100액티브(426030)</nav><dt>설정단위(좌)</dt><dd>20,000</dd>'.encode()
        with self.assertRaises(CUError):parse_cu('time',body)
if __name__=='__main__':unittest.main()
