"""Pure daily ETF history, comparison, message, and durable-outbox state machine.

No network, filesystem, credentials, scheduler, or side effects live here. The
caller must persist original source bytes and the complete returned state using
compare-and-swap *before* submitting any message. Persist begin_send before HTTP;
persist finish_send after a validated provider receipt. A lost/ambiguous receipt
or an interrupted sending claim becomes uncertain and must never auto-retry.

History is per actual verified issuer date, never execution date. The first
verified snapshot of each fund is only a baseline. CU evidence is explicit and
bounded by validFrom/validThrough: the collector's configured CU alone does not
qualify. Unverified CU snapshots and same-date quantity corrections are retained
for review, without changing the accepted baseline. An unresolved earlier hold
blocks later comparisons rather than silently skipping an unreviewed day.

JSON schema version 1: funds[fund] contains immutable snapshots[date], held[date]
(candidate source snapshots and review reasons), corrections[date], and the last
observation. outbox[eventKey] contains an immutable comparison plus independently
tracked message parts. Recipient identifiers are SHA-256 fingerprints, not chat
IDs. Part/event states are pending, sending, sent, uncertain, retry_wait, blocked.
Only definitive provider rejection 429 with retry_at enables automatic retry.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

from etf_collector.core import FUNDS, MAX_ROWS, compare, decimal, source_url, valid_date

SCHEMA_VERSION = 1
# Same ticker→Korean name map the dashboard uses; unknown tickers keep the source name.
KO_NAMES = {t: v['ko'] for t, v in json.loads(
    (Path(__file__).with_name('stocks.json')).read_text(encoding='utf-8'))['stocks'].items()}
MAX_MESSAGE_CHARS = 3500
VERIFIED_STATUSES = frozenset({
    'verified_changed', 'verified_unchanged', 'verified_no_baseline',
    'verified_cu_review_required',
})


def new_state():
    return {'schemaVersion': SCHEMA_VERSION, 'funds': {}, 'outbox': {}}


def _copy_state(state):
    if (not isinstance(state, dict) or type(state.get('schemaVersion')) is not int
            or state['schemaVersion'] != SCHEMA_VERSION
            or not isinstance(state.get('funds'), dict)
            or not isinstance(state.get('outbox'), dict)):
        raise ValueError('Unsupported or malformed durable state; do not reset it')
    # Reject non-JSON objects/non-finite numbers rather than storing unstable keys.
    json.dumps(state, allow_nan=False)
    return deepcopy(state)


def _time(value):
    if type(value) is not int or value < 0:
        raise ValueError('now/retry_at must be a nonnegative Unix timestamp integer')
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def recipient_key(recipient):
    """Fingerprint a verified private recipient; caller supplies the actual ID."""
    if not isinstance(recipient, str) or not re.fullmatch(r'[1-9]\d{0,15}', recipient):
        raise ValueError('Recipient must be a verified positive private-chat ID string')
    return _hash({'channel': 'telegram', 'recipient': recipient})


def quantity_vector(rows):
    """Canonical full stock vector; zero and absent positions are equivalent.

    Every nonzero stock quantity participates, including unchanged holdings and
    negative positions; cash, names, prices, values and row order cannot affect it.
    """
    seen, result = set(), []
    for row in rows:
        identifier = row['id']
        if identifier in seen:
            raise ValueError('Duplicate source identifier')
        seen.add(identifier)
        if row['kind'] == 'stock':
            quantity = decimal(row['quantity'])
            if quantity != '0':
                result.append([identifier, quantity])
    return sorted(result)


def event_key(recipient, fund, before_date, after_date, before_rows, after_rows, cu):
    if fund not in FUNDS:
        raise ValueError('Unknown fund')
    valid_date(before_date)
    valid_date(after_date)
    if before_date >= after_date:
        raise ValueError('A daily event requires a strictly later source date')
    return _hash({'schemaVersion': SCHEMA_VERSION, 'recipientKey': recipient_key(recipient),
                  'fund': fund, 'beforeDate': before_date, 'afterDate': after_date,
                  'beforeVector': quantity_vector(before_rows),
                  'afterVector': quantity_vector(after_rows), 'cu': decimal(cu)})


def _source_snapshot(collection):
    fund, date = collection['fund'], collection.get('asOf')
    valid_date(date)
    if collection.get('requestedDate') != date:
        raise ValueError('Source date differs from requested date; never relabel a stale source')
    if collection.get('provenanceMode') != 'live_issuer_probe':
        raise ValueError('Archived fixtures are not live daily disclosures')
    expected = source_url(fund, date)
    response = collection.get('response', {})
    if response.get('httpStatus') != 200 or response.get('responseUrl', expected) != expected:
        raise ValueError('Official source response is not a successful exact issuer URL')
    if collection.get('sourceUrl') != expected:
        raise ValueError('Source URL mismatch')
    evidence = collection.get('dateEvidence', {})
    if fund == 'time':
        value = evidence.get('value', '')
        if (evidence.get('type') != 'response_filename' or not isinstance(value, str)
                or not re.search(r'(?<!\d)' + re.escape(date) + r'(?!\d)', value)):
            raise ValueError('TIME issuer filename date is not verified')
    elif (evidence.get('type') != 'workbook_date'
          or str(evidence.get('value', '')).replace('/', '-') != date):
        raise ValueError('KOACT workbook date is not verified')
    if not isinstance(collection.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', collection['sha256']):
        raise ValueError('Source SHA-256 is missing or malformed')
    original = collection.get('originalKey')
    if not isinstance(original, str) or not original or len(original) > 1024:
        raise ValueError('Immutable original reference is missing or malformed')
    rows = collection.get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError('Missing or oversized source holdings')
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or row.get('kind') not in {'stock', 'cash', 'other'}:
            raise ValueError('Unrecognized holding classification')
        if any(not isinstance(row.get(k), str) or not row[k] for k in ('id', 'name')):
            raise ValueError('Source identifier and name are required')
        if not isinstance(row.get('quantity'), str):
            raise ValueError('Parsed quantities must be exact decimal strings')
        item = deepcopy(row)
        item['quantity'] = decimal(item['quantity'])
        normalized.append(item)
    if sum(r['kind'] == 'stock' for r in normalized) < 10:
        raise ValueError('Disclosure incomplete: fewer than 10 equity rows')
    vector = quantity_vector(normalized)
    return {'fund': fund, 'date': date, 'sourceUrl': expected,
            'sha256': collection['sha256'], 'originalKey': original,
            'dateEvidence': deepcopy(evidence), 'collectedAt': collection.get('collectedAt'),
            'rows': normalized, 'quantityDigest': _hash(vector)}


def _profile(profile, fund, date):
    if not isinstance(profile, dict) or profile.get('verified') is not True or profile.get('fund') != fund:
        raise ValueError('An explicitly verified fund CU profile is required')
    start, end = valid_date(profile.get('validFrom')), valid_date(profile.get('validThrough'))
    if not start <= date <= end:
        raise ValueError('Verified CU profile does not cover the actual disclosure date')
    cu = decimal(profile.get('cu'))
    if cu == '0' or cu.startswith('-') or '.' in cu:
        raise ValueError('CU must be a positive whole ETF-share count')
    evidence = profile.get('evidence')
    if isinstance(evidence, str):
        if not evidence.strip():
            raise ValueError('CU verification evidence is required')
    elif isinstance(evidence, dict):
        if (not isinstance(evidence.get('sourceUrl'), str)
                or not evidence['sourceUrl'].startswith('https://')
                or not isinstance(evidence.get('sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', evidence['sha256'])
                or not evidence.get('basis')):
            raise ValueError('Structured CU evidence needs sourceUrl, sha256 and basis')
    else:
        raise ValueError('CU verification evidence is required')
    result = {'verified': True, 'fund': fund, 'cu': cu, 'validFrom': start,
              'validThrough': end, 'evidence': deepcopy(evidence)}
    if 'observedAt' in profile:
        result['observedAt'] = profile['observedAt']
    return result


def _plain(value):
    # Source text stays data: no HTML/Markdown parse mode is used by the sender.
    return re.sub(r'[\x00-\x20\x7f]+', ' ', str(value)).strip()


def _units(text):
    # UTF-16 length is conservative for Telegram and supplementary Unicode.
    return len(text.encode('utf-16-le')) // 2


def _split_text(text, limit):
    """Split at newlines when practical, without dropping even one character."""
    chunks, current = [], ''
    for line in text.splitlines(keepends=True):
        if _units(current + line) <= limit:
            current += line
            continue
        if current:
            chunks.append(current)
            current = ''
        for char in line:
            if _units(current + char) > limit:
                chunks.append(current)
                current = ''
            current += char
    if current:
        chunks.append(current)
    return chunks


def notification_parts(fund, before, after, changes, cu, *, max_chars=MAX_MESSAGE_CHARS):
    """Korean plain text with every exact change, source dates and CU caveat."""
    if type(max_chars) is not int or not 800 <= max_chars <= MAX_MESSAGE_CHARS:
        raise ValueError('Message limit must be between 800 and 3500 UTF-16 units')
    order = {'added': 0, 'removed': 3}
    changed = sorted((r for r in changes if r['delta'] != '0'),
                     key=lambda r: (order.get(r['status'], 1 if not r['delta'].startswith('-') else 2), r['id']))
    if not changed:
        return []
    added = sum(r['status'] == 'added' for r in changed)
    removed = sum(r['status'] == 'removed' for r in changed)
    base = (f"{FUNDS[fund]['name']} 보유수량 변동\n"
            f"공시 기준일: {before['date']} → {after['date']}\n"
            f"변경 주식 {len(changed)}개 · 신규 {added} · 제외 {removed}\n"
            f"기준: 1 CU = {decimal(cu)} ETF좌당 공시수량\n"
            "ETF 전체 보유량·실제 매매내역이 아니며, 현금은 제외합니다.\n")
    footer = f"\n공식 원본(전): {before['sourceUrl']}\n공식 원본(후): {after['sourceUrl']}"
    lines = []
    for row in changed:
        mark = ('🆕' if row['status'] == 'added' else '❌' if row['status'] == 'removed'
                else '🔻' if row['delta'].startswith('-') else '🔺')
        delta = row['delta'] if row['delta'].startswith('-') else '+' + row['delta']
        name = KO_NAMES.get(str(row.get('code') or row['id']).split(' ')[0].upper(), row['name'])
        lines.append(f"{mark} {_plain(name)}  {decimal(row['before'])} → {decimal(row['after'])} ({delta})\n")
    budget = max_chars - _units(base) - _units(footer) - 40
    if budget < 1:
        raise ValueError('Source metadata exceeds the message size budget')
    body_chunks = _split_text(''.join(lines), budget)
    result = [f'{base}({i}/{len(body_chunks)})\n\n{body}{footer}'
              for i, body in enumerate(body_chunks, 1)]
    if any(_units(text) > max_chars for text in result):
        raise ValueError('Message exceeds its size budget')
    return result


def _hold(records, date, snapshot, reason, now):
    entry = records.setdefault(date, {'status': 'review_required', 'firstSeenAt': now,
                                      'reasons': [], 'candidates': []})
    if reason not in entry['reasons']:
        entry['reasons'].append(reason)
    if not any(s['sha256'] == snapshot['sha256'] and s['quantityDigest'] == snapshot['quantityDigest']
               for s in entry['candidates']):
        entry['candidates'].append(deepcopy(snapshot))
    entry['lastSeenAt'] = now


def ingest_collection(state, collection, recipient, cu_profile, *, now):
    """Return (state, outcome); input objects are unchanged, including on errors.

    outcome.status is baseline/changed/unchanged/duplicate/stale/source_error or
    held. A held outcome has reason. Only changed can introduce a pending event.
    A previously CU-held date can be ingested again with sufficient evidence;
    quantity corrections require explicit review outside this automatic engine.
    """
    result = _copy_state(state)
    _time(now)
    recipient_key(recipient)
    if not isinstance(collection, dict) or collection.get('fund') not in FUNDS:
        raise ValueError('Known fund collection is required')
    fund = collection['fund']
    record = result['funds'].setdefault(fund, {'snapshots': {}, 'held': {}, 'corrections': {}})

    def outcome(status, **extra):
        value = {'fund': fund, 'status': status, 'asOf': collection.get('asOf'), **extra}
        record['lastObservation'] = {**value, 'observedAt': now}
        result['updatedAt'] = now
        return result, value

    if collection.get('status') not in VERIFIED_STATUSES or collection.get('errors'):
        return outcome('source_error', reason='source_not_verified',
                       sourceStatus=collection.get('status'))
    try:
        snapshot = _source_snapshot(collection)
    except (ValueError, TypeError, KeyError) as error:
        return outcome('source_error', reason='source_validation_failed', detail=str(error))
    date = snapshot['date']
    existing = record['snapshots'].get(date)
    if existing:
        if date in record['corrections']:
            _hold(record['corrections'], date, snapshot, 'unresolved_source_correction', now)
            return outcome('held', reason='unresolved_source_correction')
        if existing['quantityDigest'] == snapshot['quantityDigest']:
            variants = existing.setdefault('equivalentSources', [])
            if (snapshot['sha256'] != existing['sha256']
                    and not any(s['sha256'] == snapshot['sha256'] for s in variants)):
                variants.append({k: deepcopy(snapshot[k]) for k in
                                 ('sha256', 'originalKey', 'dateEvidence', 'collectedAt')})
            return outcome('duplicate', beforeDate=existing.get('beforeDate'))
        _hold(record['corrections'], date, snapshot, 'same_date_quantity_correction', now)
        # Prevent an already queued comparison involving a now-disputed source.
        for event in result['outbox'].values():
            if event['fund'] == fund and event['afterDate'] >= date:
                event['holdReason'] = 'same_date_quantity_correction'
        return outcome('held', reason='same_date_quantity_correction')
    # Preserve distinct same-date candidates even if initial snapshot was CU-held.
    prior_held = record['held'].get(date)
    if prior_held and any(s['quantityDigest'] != snapshot['quantityDigest'] for s in prior_held['candidates']):
        _hold(record['corrections'], date, snapshot, 'same_date_quantity_correction', now)
        return outcome('held', reason='same_date_quantity_correction')
    if record['snapshots'] and date < max(record['snapshots']):
        _hold(record['held'], date, snapshot, 'older_than_latest_accepted_source', now)
        return outcome('stale', reason='older_than_latest_accepted_source')
    if any(d <= date for d in record['corrections']):
        _hold(record['held'], date, snapshot, 'unresolved_source_correction', now)
        return outcome('held', reason='unresolved_source_correction')
    # A stale historical probe is not an unresolved intervening day.
    intervening = [d for d, h in record['held'].items() if d < date
                   and 'older_than_latest_accepted_source' not in h['reasons']]
    if intervening:
        _hold(record['held'], date, snapshot, 'earlier_source_awaits_review', now)
        return outcome('held', reason='earlier_source_awaits_review', blockedBy=min(intervening))
    try:
        profile = _profile(cu_profile, fund, date)
        configured_cu = collection.get('cu')
        if configured_cu is not None and decimal(configured_cu) != profile['cu']:
            raise ValueError('Collector CU disagrees with verified CU profile')
    except (ValueError, TypeError) as error:
        _hold(record['held'], date, snapshot, 'cu_review_required', now)
        return outcome('held', reason='cu_review_required', detail=str(error))
    previous_dates = [d for d in record['snapshots'] if d < date]
    before = record['snapshots'][max(previous_dates)] if previous_dates else None
    if before:
        try:
            old_profile = _profile(before.get('cuProfile'), fund, before['date'])
            if old_profile['cu'] != profile['cu']:
                raise ValueError('CU sizes differ across comparison dates')
        except (ValueError, TypeError) as error:
            _hold(record['held'], date, snapshot, 'cu_mismatch', now)
            return outcome('held', reason='cu_mismatch', detail=str(error))
    snapshot['cuProfile'] = profile
    snapshot['acceptedAt'] = now
    if not before:
        snapshot['status'] = 'baseline'
        record['snapshots'][date] = snapshot
        record['held'].pop(date, None)
        return outcome('baseline')
    stock = lambda s: [r for r in s['rows'] if r['kind'] == 'stock']
    changes = [r for r in compare(stock(before), stock(snapshot)) if r['delta'] != '0']
    snapshot['beforeDate'] = before['date']
    snapshot['status'] = 'changed' if changes else 'unchanged'
    record['snapshots'][date] = snapshot
    record['held'].pop(date, None)
    if not changes:
        return outcome('unchanged', beforeDate=before['date'], changedCount=0)
    key = event_key(recipient, fund, before['date'], date, before['rows'], snapshot['rows'], profile['cu'])
    if key not in result['outbox']:
        messages = notification_parts(fund, before, snapshot, changes, profile['cu'])
        result['outbox'][key] = {
            'eventKey': key, 'recipientKey': recipient_key(recipient), 'fund': fund,
            'beforeDate': before['date'], 'afterDate': date,
            'beforeQuantityDigest': before['quantityDigest'], 'afterQuantityDigest': snapshot['quantityDigest'],
            'beforeSha256': before['sha256'], 'afterSha256': snapshot['sha256'],
            'cu': profile['cu'], 'changes': deepcopy(changes), 'createdAt': now, 'state': 'pending',
            'parts': [{'partKey': f'{key}:{i}', 'index': i, 'text': text, 'state': 'pending',
                       'attempts': []} for i, text in enumerate(messages)],
        }
    return outcome('changed', beforeDate=before['date'], changedCount=len(changes), eventKey=key)


def _aggregate(event):
    states = {part['state'] for part in event['parts']}
    if states == {'sent'}:
        return 'sent'
    for state in ('uncertain', 'sending', 'blocked', 'retry_wait', 'pending'):
        if state in states:
            return state
    raise ValueError('Malformed outbox state')


def pending_parts(state, *, now):
    """Only the next eligible part per recipient/fund, in source-date order.

    Uncertain/blocked/held older events stop that fund's later events. Sent parts
    cannot be resent. Other funds can proceed independently.
    """
    _copy_state(state)
    _time(now)
    result, seen_groups = [], set()
    for key, event in sorted(state['outbox'].items(), key=lambda kv: (kv[1]['afterDate'], kv[0])):
        if _aggregate(event) == 'sent':
            continue
        group = (event['recipientKey'], event['fund'])
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if event.get('holdReason'):
            continue
        for part in event['parts']:
            if part['state'] == 'sent':
                continue
            if part['state'] == 'pending' or (part['state'] == 'retry_wait' and part['retryAt'] <= now):
                result.append({'eventKey': key, 'partIndex': part['index'],
                               'partKey': part['partKey'], 'recipientKey': event['recipientKey'],
                               'fund': event['fund'], 'text': part['text']})
            break
    return result


def begin_send(state, event_key, part_index, *, now, attempt_id):
    """Return (new_state, claimed). Durable CAS must succeed before HTTP send."""
    result = _copy_state(state)
    _time(now)
    if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', attempt_id):
        raise ValueError('A unique non-secret attempt ID is required')
    if type(part_index) is not int:
        raise ValueError('part_index must be an integer')
    eligible = any(p['eventKey'] == event_key and p['partIndex'] == part_index
                   for p in pending_parts(state, now=now))
    if not eligible:
        return result, False
    event = result['outbox'][event_key]
    part = event['parts'][part_index]
    if any(a['attemptId'] == attempt_id for a in part['attempts']):
        raise ValueError('An attempt ID cannot be reused')
    part.update(state='sending', attemptId=attempt_id, startedAt=now)
    part['attempts'].append({'attemptId': attempt_id, 'startedAt': now, 'outcome': 'sending'})
    event['state'] = _aggregate(event)
    result['updatedAt'] = now
    return result, True


def finish_send(state, event_key, part_index, *, now, attempt_id, outcome,
                receipt=None, retry_at=None, error_code=None):
    """Commit an unambiguous send outcome for the exact claimed attempt.

    sent requires receipt={'messageId': positive int, 'recipientKey': fingerprint}.
    Only a definitive 429 rejection with future retry_at becomes retry_wait.
    Other explicit rejections become blocked; timeouts/5xx/malformed acceptance
    are uncertain, not rejections. No automatic operation resolves uncertainty.
    """
    result = _copy_state(state)
    _time(now)
    if type(part_index) is not int or part_index < 0:
        raise ValueError('Invalid part_index')
    event = result['outbox'][event_key]
    part = event['parts'][part_index]
    if part['state'] != 'sending' or part['attemptId'] != attempt_id:
        raise ValueError('Only the exact persisted sending attempt may complete')
    if now < part['startedAt']:
        raise ValueError('Completion cannot precede the claim')
    if outcome == 'sent':
        if (not isinstance(receipt, dict) or type(receipt.get('messageId')) is not int
                or receipt['messageId'] <= 0 or receipt.get('recipientKey') != event['recipientKey']):
            raise ValueError('A verified receipt for the configured recipient is required')
        if retry_at is not None or error_code is not None:
            raise ValueError('Acceptance cannot also be a rejection')
        part.update(state='sent', sentAt=now, messageId=receipt['messageId'])
    elif outcome == 'rejected':
        if receipt is not None or type(error_code) is not int or not 400 <= error_code < 500:
            raise ValueError('Explicit 4xx provider rejection evidence is required')
        if retry_at is not None:
            if error_code != 429 or _time(retry_at) <= now:
                raise ValueError('Only an explicit 429 with a future retryAt permits automatic retry')
            part.update(state='retry_wait', retryAt=retry_at, errorCode=error_code)
        else:
            part.update(state='blocked', errorCode=error_code)
    elif outcome == 'uncertain':
        if receipt is not None or retry_at is not None:
            raise ValueError('An uncertain result cannot have a receipt or retry time')
        part['state'] = 'uncertain'
    else:
        raise ValueError('Unknown delivery outcome')
    part['attempts'][-1].update(outcome=part['state'], finishedAt=now)
    part['updatedAt'] = now
    event['state'] = _aggregate(event)
    result['updatedAt'] = now
    return result


def recover_inflight(state, *, now):
    """After runner interruption, permanently quarantine every orphaned claim.

    Caller must ensure exclusive execution. A sending claim is never reclaimed
    after a lease/timeout because Telegram provides no sendMessage idempotency.
    """
    result = _copy_state(state)
    _time(now)
    for event in result['outbox'].values():
        for part in event['parts']:
            if part['state'] == 'sending':
                part.update(state='uncertain', updatedAt=now)
                part['attempts'][-1].update(outcome='uncertain', finishedAt=now,
                                           reason='runner_interrupted')
        event['state'] = _aggregate(event)
    result['updatedAt'] = now
    return result
