"""Manual read-only probe. Immutable outputs only; no push, sender, or schedule."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import signal
import urllib.error
import urllib.request
from .core import CU_PROFILE_AS_OF, FUNDS, MAX_BYTES, compare, parse_official, source_url, valid_date

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 25


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused: only the exact allowlisted issuer URL is permitted')


def download(fund, date):
    url = source_url(fund, date)
    # No user-controlled host/path, authorization headers, cookies, or URL override.
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'ETFDisclosureDryRun/1.0', 'Accept': 'application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
    def timed_out(*_):
        raise TimeoutError('Issuer request exceeded the 25-second total timeout')
    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
    try:
        try:
            response = opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error  # Preserve bounded error responses for diagnosis.
        with response:
            if response.geturl() != url:
                raise ValueError('Unexpected response URL')
            length = response.headers.get('Content-Length')
            if length and int(length) > MAX_BYTES:
                raise ValueError('Response Content-Length exceeds 4 MiB')
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError('Response exceeds 4 MiB')
            return data, {'httpStatus': response.status, 'contentType': response.headers.get('Content-Type', ''), 'contentDisposition': response.headers.get('Content-Disposition', ''), 'responseUrl': response.geturl()}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def raw_copy(directory, data):
    digest = hashlib.sha256(data).hexdigest()
    extension = '.xlsx' if data.startswith(b'PK\x03\x04') else '.xls' if data.startswith(b'\xd0\xcf') else '.bin'
    relative = Path('originals') / (digest + extension)
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise ValueError('Existing immutable original differs from its digest')
    if not path.exists():
        path.write_bytes(data)
    return digest, str(relative)


def load_archive():
    archive = json.loads((ROOT / 'fixtures/seed.json').read_text(encoding='utf-8'))
    seen = set()
    for s in archive:
        if s['id'] in seen or s['id'] != f"{s['fund']}:{s['date']}":
            raise ValueError('Archive identity mismatch')
        seen.add(s['id'])
        path = ROOT / 'fixtures/originals' / Path(s['originalKey']).name
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != s['sha256']:
            raise ValueError('Archive original SHA-256 mismatch')
        if s['sourceUrl'] != source_url(s['fund'], s['date']):
            raise ValueError('Archive source URL mismatch')
        parsed = parse_official(data, s['fund'], s['date'], f'attachment; filename="{path.name}"')
        if parsed['rows'] != s['rows']:
            raise ValueError('Archive rows disagree with the original workbook')
    return archive


def collect_one(fund, date, archive, directory, probe=False, downloader=download):
    previous = sorted((s for s in archive if s['fund'] == fund and s['date'] < date), key=lambda s: s['date'])
    baseline = previous[-1] if previous else None
    result = {'fund': fund, 'name': FUNDS[fund]['name'], 'requestedDate': date, 'asOf': None,
              'sourceUrl': source_url(fund, date), 'status': 'pending',
              'baselineAsOf': baseline['date'] if baseline else None,
              'baselineKind': 'archived_fixture', 'cuProfileAsOf': CU_PROFILE_AS_OF,
              'cu': FUNDS[fund]['cu'], 'cuEvidence': 'Configured issuer profile, not independently present in these workbook cells',
              'quantityMeaning': 'Published holdings per one creation unit (CU); not fund-wide holdings or executed trades',
              'collectedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
              'provenanceMode': 'live_issuer_probe' if probe else 'archived_fixture', 'errors': []}
    stage = 'download_error' if probe else 'fixture_error'
    try:
        if probe:
            data, headers = downloader(fund, date)
        else:
            matching = [s for s in archive if s['fund'] == fund and s['date'] == date]
            if not matching:
                raise ValueError('No archived fixture for the requested date; opt in to the issuer probe or choose an archived date')
            selected = matching[0]
            path = ROOT / 'fixtures/originals' / Path(selected['originalKey']).name
            data = path.read_bytes()
            headers = {'httpStatus': None, 'contentDisposition': f'attachment; filename="{path.name}"'}
            result['archivedCollectedAt'] = selected['collectedAt']
        result['sha256'], result['originalKey'] = raw_copy(directory, data)
        result['response'] = headers
        stage = 'validation_error'
        if probe and headers['httpStatus'] != 200:
            raise ValueError(f"Issuer returned HTTP {headers['httpStatus']}; this is not 'unchanged'")
        parsed = parse_official(data, fund, date, headers.get('contentDisposition', ''))
        if not probe and FUNDS[fund]['issuer'] == 'time':
            parsed['dateEvidence'] = {'type': 'archive_record', 'value': date, 'description': 'Archived snapshot records issuer filename evidence; the original HTTP header was not retained. Local fixture filename is reconstructed.'}
        result.update(parsed)
        result['asOf'] = date
        if date > CU_PROFILE_AS_OF:
            result['status'] = 'verified_cu_review_required'
            result['warning'] = 'Source date verified, but current CU profile must be reviewed before quantity comparison'
        elif baseline:
            # All comparisons use the same configured CU within a fund. If the
            # issuer changes CU size, verify/update both profiles before comparing.
            stock = lambda s: [r for r in s if r['kind'] == 'stock']
            changes = compare(stock(baseline['rows']), stock(result['rows']))
            result['comparison'] = {'baselineKind': 'archived_fixture', 'beforeDate': baseline['date'], 'afterDate': date,
                'beforeSha256': baseline['sha256'], 'afterSha256': result['sha256'],
                'cuBefore': FUNDS[fund]['cu'], 'cuAfter': FUNDS[fund]['cu'],
                'changedCount': sum(r['delta'] != '0' for r in changes),
                'addedCount': sum(r['status'] == 'added' for r in changes),
                'removedCount': sum(r['status'] == 'removed' for r in changes), 'rows': changes}
            result['status'] = 'verified_changed' if result['comparison']['changedCount'] else 'verified_unchanged'
        else:
            result['status'] = 'verified_no_baseline'
    except Exception as error:
        result['status'] = stage
        result['asOf'] = None
        result.pop('comparison', None)
        result['errors'].append({'type': type(error).__name__, 'message': str(error)})
    return result


def run(date, directory, probe=False):
    valid_date(date)
    archive = load_archive()
    # A run directory is never reused or silently overwritten.
    directory.mkdir(parents=True, exist_ok=False)
    results = [collect_one(fund, date, archive, directory, probe=probe) for fund in FUNDS]
    report = {'schemaVersion': 1, 'mode': 'issuer_probe' if probe else 'fixture_dry_run',
              'requestedDate': date, 'deliveryEnabled': False, 'results': results}
    write_json(directory / 'report.json', report)
    summary = ['ETF quantity dry run', f'Requested date: {date}', f'Mode: {report["mode"]}', 'Delivery: disabled', '']
    for item in results:
        summary += [f'{item["name"]}: {item["status"]}', f'As of: {item["asOf"] or "unverified"}; baseline: {item["baselineAsOf"] or "none"}; CU: {item["cu"]}']
        if 'comparison' in item:
            summary.append(f'Changed equities: {item["comparison"]["changedCount"]}; new: {item["comparison"]["addedCount"]}; removed: {item["comparison"]["removedCount"]}')
        if item.get('warning'): summary.append(f'Warning: {item["warning"]}')
        summary += [f'Error: {e["message"]}' for e in item['errors']]
        summary.append('')
    text = '\n'.join(summary) + '\n'
    (directory / 'summary.txt').write_text(text, encoding='utf-8')
    print(text)
    return 1 if any(r['errors'] for r in results) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, help='Official as-of date, YYYY-MM-DD')
    parser.add_argument('--output', default='output/manual-run', type=Path, help='New directory; never overwrites an existing run')
    parser.add_argument('--probe', action='store_true', help='Opt in to GET requests to the two fixed issuer endpoints')
    args = parser.parse_args()
    return run(args.date, args.output, args.probe)


if __name__ == '__main__':
    raise SystemExit(main())
