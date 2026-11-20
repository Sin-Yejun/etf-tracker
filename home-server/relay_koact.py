"""Home-server relay for KOACT disclosures.

samsungactive.co.kr answers GitHub-hosted runners with HTTP 403 (since
2026-11-19) but serves the home server normally. Before the 08:00 KST
dispatch, this script fetches the same exact issuer URLs the runner would
(7-day lookback + the official CU product API) and stores the raw responses,
unparsed, as runtime/relay/koact/<KST date>.json on the runtime branch.
The workflow parses and validates them exactly as it does a direct fetch.

Standard library only (the home server has no xlrd); token is the same
fine-grained PAT the dispatch trigger uses.
"""
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from github_history import GitHubHistory  # noqa: E402

FUND = 'koact'
LOOKBACK_DAYS = 7
MAX_BYTES = 4 * 1024 * 1024
CU_URL = 'https://www.samsungactive.co.kr/api/v1/product/etf/2ETFQ1.do'
UA = 'ETFDisclosureDryRun/1.0'


def disclosure_url(date):
    return f'https://www.samsungactive.co.kr/excel_pdf.do?fId=2ETFQ1&gijunYMD={date.replace("-", "")}'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect refused')


def fetch(url, accept):
    entry = {'url': url, 'fetchedAt': dt.datetime.now(dt.timezone.utc).isoformat()}
    request = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept})
    try:
        try:
            response = urllib.request.build_opener(NoRedirect()).open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError('response too large')
            entry.update(httpStatus=response.status, responseUrl=response.geturl(),
                         contentType=response.headers.get('Content-Type', ''),
                         contentDisposition=response.headers.get('Content-Disposition', ''),
                         sha256=hashlib.sha256(body).hexdigest(),
                         body=base64.b64encode(body).decode())
    except Exception as error:
        entry.update(httpStatus=None, error=type(error).__name__)
    return entry


def build(today):
    dates = [(today - dt.timedelta(days=age)).isoformat() for age in range(LOOKBACK_DAYS)]
    return {'version': 1, 'fund': FUND, 'relayDate': today.isoformat(),
            'fetchedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
            'disclosures': [dict(fetch(disclosure_url(d), 'application/vnd.ms-excel,*/*'), date=d) for d in dates],
            'cuEvidence': fetch(CU_URL, 'application/json')}


def main():
    token_file = Path(os.getenv('TOKEN_FILE', Path.home() / '.config/etf-tracker/github-token'))
    token = ''.join(token_file.read_text().split())
    today = dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    relay = build(today)
    data = (json.dumps(relay, sort_keys=True) + '\n').encode()
    history = GitHubHistory(token)
    path = f'runtime/relay/{FUND}/{today.isoformat()}.json'
    history.put(path, data, history.read(path))
    ok = sum(d.get('httpStatus') == 200 for d in relay['disclosures'])
    print(f"relayed {path}: disclosures 200={ok}/{len(relay['disclosures'])}, cu={relay['cuEvidence'].get('httpStatus')}")
    return 0 if ok and relay['cuEvidence'].get('httpStatus') == 200 else 1


if __name__ == '__main__':
    sys.exit(main())
