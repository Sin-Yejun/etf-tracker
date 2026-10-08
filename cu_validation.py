"""Fresh official product CU evidence, separate from holdings publication dates."""
import datetime as dt
import hashlib
import json
import re
import urllib.request
from zoneinfo import ZoneInfo
from etf_collector.core import FUNDS,valid_date

SOURCES={'time':'https://timeetf.co.kr/m11_view.php?idx=2','koact':'https://www.samsungactive.co.kr/api/v1/product/etf/2ETFQ1.do'}
LIMIT=2*1024*1024
class CUError(Exception):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise CUError('CU source redirect refused')

def fetch_evidence(fund):
    try:
        with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(SOURCES[fund]),timeout=25) as r:
            body=r.read(LIMIT+1)
            if r.status!=200 or r.geturl()!=SOURCES[fund] or len(body)>LIMIT:raise CUError('Invalid CU source response')
            return body
    except Exception:raise CUError('Official current CU source unavailable') from None

def parse_cu(fund,body):
    if not body or len(body)>LIMIT:raise CUError('CU evidence size invalid')
    try:
        if fund=='koact':
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result:raise ValueError('Duplicate JSON key')
                    result[key]=value
                return result
            product=json.loads(body,object_pairs_hook=unique)['info']['product']
            if product['fId']!='2ETFQ1' or product['stkTicker']!='0015B0' or '미국나스닥성장기업액티브' not in product['fNm']:
                raise ValueError()
            raw=str(product['qPerCu'])
        else:
            html=body.decode('utf-8')
            titles=re.findall(r'<title[^>]*>(.*?)</title>',html,re.I|re.S)
            if len(titles)!=1 or re.sub(r'\s+','',titles[0])!='TIME미국나스닥100액티브(426030)':raise ValueError()
            found=re.findall(r'<dt[^>]*>\s*설정단위\(좌\)\s*</dt>\s*<dd[^>]*>\s*([0-9,]+)\s*</dd>',html)
            if len(found)!=1:raise ValueError()
            raw=found[0].replace(',','')
        if not re.fullmatch(r'[1-9]\d{0,7}',raw):raise ValueError()
        if int(raw)!=FUNDS[fund]['cu']:raise CUError('CU changed; explicit review required')
        return int(raw)
    except Exception:raise CUError('Official CU identity or schema could not be verified') from None

def current_profile(fund,source_date,body,observed_at=None):
    valid_date(source_date)
    cu=parse_cu(fund,body)
    observed=observed_at or dt.datetime.now(dt.timezone.utc).isoformat()
    timestamp=dt.datetime.fromisoformat(observed.replace('Z','+00:00'))
    if timestamp.tzinfo is None or timestamp.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()!=source_date:
        raise CUError('An undated current product page cannot certify a past disclosure CU')
    return {'verified':True,'fund':fund,'cu':cu,'validFrom':source_date,'validThrough':source_date,
            'observedAt':observed,'evidence':{'sourceUrl':SOURCES[fund],'sha256':hashlib.sha256(body).hexdigest(),
            'basis':'Current official product CU observed when this dated disclosure was collected; not an embedded workbook CU or NAV date'}}
