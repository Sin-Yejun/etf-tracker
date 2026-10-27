"""Offline exact-comparison, history and interrupted-delivery regression tests."""
from copy import deepcopy
import hashlib
import json
import unittest
from pathlib import Path

from daily_engine import (
    new_state, ingest_collection, quantity_vector, event_key, notification_parts,
    recipient_key, pending_parts, begin_send, finish_send, recover_inflight,
)
from etf_collector.core import FUNDS, compare, source_url

RECIPIENT = '123456789'
BEFORE, AFTER = '2026-10-07', '2026-10-08'


def stock(identifier='A', quantity='1', name=None, **extra):
    return {'id': identifier, 'name': name or identifier, 'code': identifier + ' US Equity',
            'quantity': quantity, 'kind': 'stock', 'valueKrw': '100', **extra}


def rows(quantity='1'):
    return [stock('A', quantity)] + [stock('FILLER' + str(i)) for i in range(10)]


def profile(fund='time', date=AFTER, cu=None):
    return {'verified': True, 'fund': fund, 'cu': cu or FUNDS[fund]['cu'],
            'validFrom': date, 'validThrough': date,
            'evidence': {'sourceUrl': 'https://example.test/official-fund',
                         'sha256': 'b' * 64, 'basis': 'Issuer creation-unit profile'},
            'observedAt': '2026-10-08T00:00:00Z'}


def collection(date=AFTER, fund='time', holdings=None):
    holdings = rows() if holdings is None else holdings
    raw_hash = hashlib.sha256(json.dumps([fund, date, holdings], sort_keys=True).encode()).hexdigest()
    return {'fund': fund, 'requestedDate': date, 'asOf': date,
            'status': 'verified_changed', 'provenanceMode': 'live_issuer_probe', 'errors': [],
            'sourceUrl': source_url(fund, date), 'sha256': raw_hash,
            'originalKey': f'runtime/originals/{fund}/{date}/{raw_hash}.xlsx',
            'collectedAt': '2026-10-08T00:00:00Z', 'cu': FUNDS[fund]['cu'],
            'dateEvidence': ({'type': 'response_filename', 'value': f'time-{date}.xlsx'} if FUNDS[fund]['issuer'] == 'time'
                             else {'type': 'workbook_date', 'value': date.replace('-', '/')}),
            'response': {'httpStatus': 200, 'responseUrl': source_url(fund, date)},
            'rows': holdings}


def ingest(state, data, cu_profile='default', now=100):
    if cu_profile == 'default':
        cu_profile = profile(data['fund'], data['asOf'])
    return ingest_collection(state, data, RECIPIENT, cu_profile, now=now)


def changed_state(holdings=None):
    initial, _ = ingest(new_state(), collection(BEFORE))
    return ingest(initial, collection(AFTER, holdings=holdings or rows('1.000000001')), now=200)


class HistoryTests(unittest.TestCase):
    def test_baseline_is_per_fund_and_has_no_notification(self):
        state = new_state()
        for fund in FUNDS:
            state, outcome = ingest(state, collection(fund=fund))
            self.assertEqual(outcome['status'], 'baseline')
        self.assertEqual(state['outbox'], {})
        self.assertEqual(set(state['funds']), set(FUNDS))

    def test_previous_actual_date_advances_after_unchanged(self):
        state, _ = ingest(new_state(), collection('2026-10-01'))
        state, outcome = ingest(state, collection('2026-10-07'))
        self.assertEqual(outcome['status'], 'unchanged')
        state, outcome = ingest(state, collection(AFTER, holdings=rows('2')))
        self.assertEqual(outcome['beforeDate'], BEFORE)
        event = state['outbox'][outcome['eventKey']]
        self.assertEqual(event['changes'][0]['delta'], '1')
        self.assertEqual(len(state['funds']['time']['snapshots']), 3)

    def test_collector_fixture_comparison_is_ignored(self):
        state, _ = ingest(new_state(), collection(BEFORE, holdings=rows('10')))
        data = collection(AFTER, holdings=rows('12'))
        data['comparison'] = {'beforeDate': '1999-01-01', 'rows': [], 'changedCount': 0}
        data['baselineAsOf'] = '1999-01-01'
        state, outcome = ingest(state, data)
        event = state['outbox'][outcome['eventKey']]
        self.assertEqual((event['beforeDate'], event['changes'][0]['delta']), (BEFORE, '2'))

    def test_same_date_quantity_duplicate_cash_names_and_reordering(self):
        state, _ = changed_state()
        count = len(state['outbox'])
        data = collection(AFTER, holdings=list(reversed(rows('1.000000001'))))
        data['rows'][0]['name'] = 'New name'
        data['rows'][0]['valueKrw'] = '99999'
        data['rows'].append({'id': 'cash', 'name': '현금', 'kind': 'cash', 'quantity': '9'})
        state, outcome = ingest(state, data)
        self.assertEqual(outcome['status'], 'duplicate')
        self.assertEqual(len(state['outbox']), count)
        self.assertEqual(len(state['funds']['time']['snapshots'][AFTER]['equivalentSources']), 1)

    def test_same_date_correction_is_held_and_blocks_existing_event(self):
        state, event_outcome = changed_state()
        original = deepcopy(state['funds']['time']['snapshots'][AFTER])
        state, outcome = ingest(state, collection(AFTER, holdings=rows('7')))
        self.assertEqual(outcome['reason'], 'same_date_quantity_correction')
        self.assertEqual(state['funds']['time']['snapshots'][AFTER], original)
        self.assertEqual(len(state['outbox']), 1)
        self.assertEqual(pending_parts(state, now=300), [])
        self.assertEqual(state['outbox'][event_outcome['eventKey']]['holdReason'], outcome['reason'])
        state, next_outcome = ingest(state, collection('2026-10-09', holdings=rows('8')))
        self.assertEqual(next_outcome['reason'], 'unresolved_source_correction')

    def test_correction_reversion_does_not_hide_pending_review(self):
        state, _ = changed_state()
        state, _ = ingest(state, collection(AFTER, holdings=rows('7')))
        state, outcome = ingest(state, collection(AFTER, holdings=rows('1.000000001')))
        self.assertEqual(outcome['reason'], 'unresolved_source_correction')
        self.assertEqual(pending_parts(state, now=400), [])

    def test_zero_and_absent_have_identical_semantics(self):
        state, _ = ingest(new_state(), collection(BEFORE, holdings=rows('0')))
        state, outcome = ingest(state, collection(BEFORE, holdings=rows('0') + [stock('ZERO', '0.00')]))
        self.assertEqual(outcome['status'], 'duplicate')
        state, outcome = ingest(state, collection(AFTER, holdings=rows('0.000000001')))
        change = state['outbox'][outcome['eventKey']]['changes'][0]
        self.assertEqual((change['status'], change['delta']), ('added', '0.000000001'))
        state, outcome = ingest(state, collection('2026-10-09', holdings=rows('0')))
        change = state['outbox'][outcome['eventKey']]['changes'][0]
        self.assertEqual((change['status'], change['delta']), ('removed', '-0.000000001'))

    def test_missing_rows_are_exits_and_new_rows_are_entries(self):
        before = rows() + [stock('EXIT', '0.000000001')]
        state, _ = ingest(new_state(), collection(BEFORE, holdings=before))
        state, outcome = ingest(state, collection(AFTER, holdings=rows() + [stock('NEW', '3')]))
        statuses = {r['id']: r['status'] for r in state['outbox'][outcome['eventKey']]['changes']}
        self.assertEqual(statuses, {'EXIT': 'removed', 'NEW': 'added'})

    def test_cash_value_and_name_changes_do_not_emit(self):
        before = rows() + [{'id': 'CASH', 'name': 'Cash', 'kind': 'cash', 'quantity': '99'}]
        after = deepcopy(before)
        after[-1]['quantity'] = '0'
        after[0].update(name='Renamed', valueKrw='999999')
        state, _ = ingest(new_state(), collection(BEFORE, holdings=before))
        state, outcome = ingest(state, collection(AFTER, holdings=after))
        self.assertEqual(outcome['status'], 'unchanged')
        self.assertEqual(state['outbox'], {})

    def test_stale_source_is_never_relabelled(self):
        state, _ = ingest(new_state(), collection(AFTER))
        data = collection(BEFORE)
        data['requestedDate'] = AFTER
        state, outcome = ingest(state, data)
        self.assertEqual(outcome['status'], 'source_error')
        self.assertEqual(set(state['funds']['time']['snapshots']), {AFTER})
        state, outcome = ingest(state, collection(BEFORE))
        self.assertEqual(outcome['status'], 'stale')
        self.assertEqual(state['outbox'], {})

    def test_source_failures_never_mean_unchanged(self):
        for status in ('download_error', 'validation_error', 'fixture_error', 'pending'):
            with self.subTest(status=status):
                data = collection()
                data.update(status=status, asOf=None)
                state, outcome = ingest(new_state(), data, None)
                self.assertEqual(outcome['status'], 'source_error')
                self.assertEqual(state['funds']['time']['snapshots'], {})

    def test_invalid_live_provenance_fails_closed(self):
        mutations = [lambda c: c.update(provenanceMode='archived_fixture'),
                     lambda c: c['response'].update(httpStatus=500),
                     lambda c: c['response'].update(responseUrl='https://evil.test/'),
                     lambda c: c.update(sourceUrl='https://evil.test/'),
                     lambda c: c.update(sha256='bad'),
                     lambda c: c.update(originalKey=None),
                     lambda c: c['dateEvidence'].update(type='archive_record'),
                     lambda c: c['rows'][0].update(quantity=1.1),
                     lambda c: c['rows'].append(deepcopy(c['rows'][0])),
                     lambda c: c.update(rows=[stock()])]
        for mutation in mutations:
            data = collection()
            mutation(data)
            state, outcome = ingest(new_state(), data)
            self.assertEqual(outcome['status'], 'source_error')
            self.assertEqual(state['outbox'], {})

    def test_cu_requires_own_date_verification_and_recovers_same_held_date(self):
        data = collection()
        state, outcome = ingest(new_state(), data, None)
        self.assertEqual(outcome['reason'], 'cu_review_required')
        self.assertEqual(state['funds']['time']['snapshots'], {})
        state, outcome = ingest(state, data)
        self.assertEqual(outcome['status'], 'baseline')
        self.assertEqual(state['funds']['time']['held'], {})

    def test_cu_independent_date_windows_can_match(self):
        state, _ = ingest(new_state(), collection(BEFORE))
        state, outcome = ingest(state, collection(AFTER, holdings=rows('2')))
        self.assertEqual(outcome['status'], 'changed')
        self.assertEqual(state['funds']['time']['snapshots'][BEFORE]['cuProfile']['validThrough'], BEFORE)

    def test_cu_mismatch_and_expired_evidence_hold(self):
        state, _ = ingest(new_state(), collection(BEFORE))
        data = collection(AFTER, holdings=rows('2'))
        data['cu'] = 10000
        blocked, outcome = ingest(state, data, profile(cu=10000))
        self.assertEqual(outcome['reason'], 'cu_mismatch')
        self.assertEqual(blocked['outbox'], {})
        _, expired = ingest(state, collection(AFTER), profile(date=BEFORE))
        self.assertEqual(expired['reason'], 'cu_review_required')

    def test_unverified_baseline_and_missing_evidence_hold(self):
        state, _ = ingest(new_state(), collection(BEFORE))
        state['funds']['time']['snapshots'][BEFORE]['cuProfile']['verified'] = False
        _, outcome = ingest(state, collection(AFTER, holdings=rows('2')))
        self.assertEqual(outcome['reason'], 'cu_mismatch')
        missing = profile()
        missing['evidence'] = {}
        _, outcome = ingest(new_state(), collection(), missing)
        self.assertEqual(outcome['reason'], 'cu_review_required')

    def test_intervening_held_date_is_not_silently_skipped(self):
        state, _ = ingest(new_state(), collection('2026-10-06'))
        state, _ = ingest(state, collection(BEFORE), None)
        state, outcome = ingest(state, collection(AFTER, holdings=rows('3')))
        self.assertEqual(outcome['reason'], 'earlier_source_awaits_review')
        state, outcome = ingest(state, collection(BEFORE))
        self.assertEqual(outcome['status'], 'unchanged')
        state, outcome = ingest(state, collection(AFTER, holdings=rows('3')))
        self.assertEqual((outcome['status'], outcome['beforeDate']), ('changed', BEFORE))

    def test_same_date_correction_of_cu_held_source_stays_held(self):
        state, _ = ingest(new_state(), collection(), None)
        state, outcome = ingest(state, collection(holdings=rows('2')))
        self.assertEqual(outcome['reason'], 'same_date_quantity_correction')
        self.assertEqual(state['funds']['time']['snapshots'], {})

    def test_input_state_and_collection_are_not_mutated_and_json_round_trip(self):
        state = new_state()
        data = collection()
        original = deepcopy([state, data])
        result, _ = ingest(state, data)
        self.assertEqual([state, data], original)
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)
        with self.assertRaises(ValueError):
            ingest({'schemaVersion': 99, 'funds': {}, 'outbox': {}}, data)

    def test_offline_real_fixtures_oct7_oct8(self):
        archive = json.loads((Path(__file__).parent / 'fixtures/seed.json').read_text())
        for fund, expected in [('time', 0), ('koact', 44)]:
            state = new_state()
            for date in (BEFORE, AFTER):
                source = next(s for s in archive if s['fund'] == fund and s['date'] == date)
                data = collection(date, fund, source['rows'])
                state, outcome = ingest(state, data)
            self.assertEqual(outcome['changedCount'], expected)
            if fund == 'koact':
                changes = state['outbox'][outcome['eventKey']]['changes']
                self.assertEqual(next(r['delta'] for r in changes if r['code'] == 'AMAT US Equity'), '-0.01')
                self.assertEqual(next(r['status'] for r in changes if r['code'] == 'SPCX US Equity'), 'added')


class EventAndTextTests(unittest.TestCase):
    def test_keys_are_canonical_recipient_date_and_full_vector_specific(self):
        a, b = rows(), rows('2')
        key = event_key(RECIPIENT, 'time', BEFORE, AFTER, a, b, 20000)
        self.assertEqual(key, event_key(RECIPIENT, 'time', BEFORE, AFTER, list(reversed(a)), b, '20000.00'))
        modified = deepcopy(b)
        modified[1]['quantity'] = '2'
        for different in [event_key('987654321', 'time', BEFORE, AFTER, a, b, 20000),
                          event_key(RECIPIENT, 'koact', BEFORE, AFTER, a, b, 50000),
                          event_key(RECIPIENT, 'time', BEFORE, '2026-10-09', a, b, 20000),
                          event_key(RECIPIENT, 'time', BEFORE, AFTER, a, modified, 20000)]:
            self.assertNotEqual(key, different)
        self.assertEqual(len(key), 64)

    def test_all_changes_are_in_numbered_bounded_korean_messages(self):
        before_rows = [stock(f'S{i:04}', '100', '종목' + str(i) + '😀' * 15) for i in range(300)]
        after_rows = [dict(r, quantity='200.00000000000001' if i % 2 else '50') for i, r in enumerate(before_rows)]
        before = {'date': BEFORE, 'sourceUrl': source_url('time', BEFORE)}
        after = {'date': AFTER, 'sourceUrl': source_url('time', AFTER)}
        parts = notification_parts('time', before, after, compare(before_rows, after_rows))
        self.assertGreater(len(parts), 1)
        whole = ''.join(parts)
        for i in range(300):
            self.assertIn('종목' + str(i) + '😀', whole)
        self.assertEqual(whole.count('(+100.00000000000001)'), 150)
        self.assertEqual(whole.count('(-50)'), 150)
        self.assertNotIn('비례 조정', whole)
        for i, text in enumerate(parts, 1):
            self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 3500)
            for term in (BEFORE, AFTER, f'({i}/{len(parts)})'):
                self.assertIn(term, text)
            self.assertNotIn('http', text)

    def test_known_tickers_use_korean_names_without_ticker(self):
        before = [stock('AAPL', '50', 'Apple Inc'), stock('ZZZZ', '5', 'Unknown Co')]
        after = [stock('AAPL', '70', 'Apple Inc'), stock('ZZZZ', '3', 'Unknown Co')]
        text = ''.join(notification_parts('time', {'date': BEFORE, 'sourceUrl': source_url('time', BEFORE)},
                                          {'date': AFTER, 'sourceUrl': source_url('time', AFTER)},
                                          compare(before, after)))
        self.assertIn('🔺 애플  50 → 70 (+20)', text)
        self.assertIn('🔻 Unknown Co  5 → 3 (-2)', text)
        self.assertNotIn('EQUITY', text.upper())
        self.assertNotIn('(1/1)', text)
        self.assertNotIn('http', text)
        self.assertLess(text.index('애플'), text.index('Unknown Co'))

    def test_fund_wide_rescale_is_one_line_but_trades_are_listed(self):
        # Shape of KOACT 2026-10-16 -> 10-19: everything +1.78% at 0.01 precision,
        # plus a real sell, a new position and an exit.
        names = [f'R{i:02}' for i in range(10)]
        before = [stock(n, q) for n, q in zip(names, ('11.32', '33.62', '26.91', '29.42', '7.63',
                                                    '1.06', '178.1', '20.43', '40.17', '41.67'))]
        before += [stock('DELL', '57.99'), stock('GONE', '5')]
        after = [stock(n, q) for n, q in zip(names, ('11.52', '34.22', '27.4', '29.95', '7.76',
                                                   '1.07', '181.29', '20.79', '40.89', '42.42'))]
        after += [stock('DELL', '16.41'), stock('AAPL', '40.59', 'Apple Inc')]
        text = ''.join(notification_parts('koact', {'date': BEFORE}, {'date': AFTER}, compare(before, after)))
        self.assertIn('변경 주식 3개 · 신규 1 · 제외 1', text)
        self.assertIn('🆕 애플  0 → 40.59 (+40.59)', text)
        self.assertIn('🔻 델 테크놀로지스  57.99 → 16.41 (-41.58)', text)
        self.assertIn('❌ GONE  5 → 0 (-5)', text)
        self.assertIn('➖ 10종목 일괄 +1.78% (비례 조정)', text)
        self.assertNotIn('R0', text)

    def test_integer_one_share_change_is_not_hidden(self):
        before = [stock(f'S{i}', '50') for i in range(5)]
        after = [stock('S0', '51')] + before[1:]
        text = ''.join(notification_parts('time', {'date': BEFORE}, {'date': AFTER}, compare(before, after)))
        self.assertIn('🔺 S0  50 → 51 (+1)', text)
        self.assertNotIn('비례 조정', text)

    def test_oversize_single_source_name_is_split_without_loss(self):
        long_name = 'Z' * 20000
        before, after = rows(), rows('2')
        after[0]['name'] = long_name
        parts = notification_parts('time', {'date': BEFORE, 'sourceUrl': source_url('time', BEFORE)},
                                   {'date': AFTER, 'sourceUrl': source_url('time', AFTER)}, compare(before, after))
        self.assertEqual(sum(p.count('Z') for p in parts), 20000)
        self.assertTrue(all(len(p) <= 3500 for p in parts))

    def test_recipient_ids_not_persisted(self):
        state, _ = changed_state()
        self.assertNotIn(RECIPIENT, json.dumps(state))
        self.assertEqual(len(recipient_key(RECIPIENT)), 64)
        for invalid in ('', '-123', '@channel', '1.2', 123, True):
            with self.assertRaises(ValueError): recipient_key(invalid)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.state, outcome = changed_state()
        self.key = outcome['eventKey']

    def claim(self, state=None, attempt='run.1', now=300):
        return begin_send(state or self.state, self.key, 0, now=now, attempt_id=attempt)

    def test_persisted_claim_before_http_and_no_duplicate_claim(self):
        original = deepcopy(self.state)
        claimed, ok = self.claim()
        self.assertTrue(ok)
        self.assertEqual(self.state, original)
        self.assertEqual(claimed['outbox'][self.key]['state'], 'sending')
        self.assertFalse(self.claim(claimed)[1])
        self.assertEqual(pending_parts(claimed, now=999), [])

    def test_verified_receipt_sends_once(self):
        claimed, _ = self.claim()
        sent = finish_send(claimed, self.key, 0, now=301, attempt_id='run.1', outcome='sent',
                           receipt={'messageId': 17, 'recipientKey': recipient_key(RECIPIENT)})
        self.assertEqual(sent['outbox'][self.key]['state'], 'sent')
        self.assertEqual(pending_parts(sent, now=999), [])
        self.assertFalse(self.claim(sent)[1])
        replayed, outcome = ingest(sent, collection(AFTER, holdings=rows('1.000000001')))
        self.assertEqual(outcome['status'], 'duplicate')
        self.assertEqual(pending_parts(replayed, now=999), [])

    def test_crash_and_ambiguous_send_are_never_retried(self):
        claimed, _ = self.claim()
        for state in [recover_inflight(claimed, now=301),
                      finish_send(claimed, self.key, 0, now=301, attempt_id='run.1', outcome='uncertain')]:
            self.assertEqual(state['outbox'][self.key]['state'], 'uncertain')
            self.assertEqual(pending_parts(state, now=10**12), [])
            self.assertFalse(self.claim(state, now=10**12)[1])

    def test_only_definitive_429_retries_after_due_time(self):
        claimed, _ = self.claim()
        retry = finish_send(claimed, self.key, 0, now=301, attempt_id='run.1',
                            outcome='rejected', error_code=429, retry_at=320)
        self.assertEqual(retry['outbox'][self.key]['state'], 'retry_wait')
        self.assertEqual(pending_parts(retry, now=319), [])
        self.assertEqual(len(pending_parts(retry, now=320)), 1)
        self.assertFalse(self.claim(retry, 'run.2', 319)[1])
        again, ok = self.claim(retry, 'run.2', 320)
        self.assertTrue(ok)
        self.assertEqual(len(again['outbox'][self.key]['parts'][0]['attempts']), 2)
        with self.assertRaises(ValueError): self.claim(retry, 'run.1', 320)

    def test_nonretryable_rejection_is_blocked(self):
        claimed, _ = self.claim()
        blocked = finish_send(claimed, self.key, 0, now=301, attempt_id='run.1',
                              outcome='rejected', error_code=403)
        self.assertEqual(blocked['outbox'][self.key]['state'], 'blocked')
        self.assertEqual(pending_parts(blocked, now=999), [])

    def test_invalid_completion_and_wrong_recipient_fail_closed(self):
        claimed, _ = self.claim()
        invalid = [dict(outcome='sent', receipt={'messageId': True, 'recipientKey': recipient_key(RECIPIENT)}),
                   dict(outcome='sent', receipt={'messageId': 1, 'recipientKey': recipient_key('999')}),
                   dict(outcome='sent'), dict(outcome='rejected', error_code=500),
                   dict(outcome='rejected', error_code=400, retry_at=400),
                   dict(outcome='rejected', error_code=429, retry_at=300),
                   dict(outcome='uncertain', retry_at=400)]
        for kwargs in invalid:
            with self.assertRaises(ValueError):
                finish_send(claimed, self.key, 0, now=301, attempt_id='run.1', **kwargs)
        with self.assertRaises(ValueError):
            finish_send(claimed, self.key, 0, now=301, attempt_id='wrong', outcome='uncertain')
        with self.assertRaises(ValueError):
            finish_send(self.state, self.key, 0, now=301, attempt_id='run.1', outcome='uncertain')

    def test_uncertain_event_blocks_future_same_fund_but_not_other_fund(self):
        claimed, _ = self.claim()
        state = recover_inflight(claimed, now=301)
        state, _ = ingest(state, collection('2026-10-09', holdings=rows('3')))
        state, _ = ingest(state, collection(BEFORE, 'koact'))
        state, _ = ingest(state, collection(AFTER, 'koact', rows('2')))
        self.assertEqual([p['fund'] for p in pending_parts(state, now=500)], ['koact'])

    def test_parts_are_sequential_and_partial_sent_parts_are_not_repeated(self):
        holdings = [stock(f'S{i}', str(i + 1), '회사' * 30) for i in range(100)]
        state, outcome = changed_state(holdings)
        key = outcome['eventKey']
        self.assertGreater(len(state['outbox'][key]['parts']), 1)
        self.assertFalse(begin_send(state, key, 1, now=300, attempt_id='run.1')[1])
        state, ok = begin_send(state, key, 0, now=300, attempt_id='run.1')
        self.assertTrue(ok)
        state = finish_send(state, key, 0, now=301, attempt_id='run.1', outcome='sent',
                            receipt={'messageId': 8, 'recipientKey': recipient_key(RECIPIENT)})
        self.assertEqual(pending_parts(state, now=302)[0]['partIndex'], 1)
        self.assertFalse(begin_send(state, key, 0, now=303, attempt_id='run.2')[1])
        state, _ = begin_send(state, key, 1, now=303, attempt_id='run.2')
        state = recover_inflight(state, now=304)
        self.assertEqual(state['outbox'][key]['parts'][0]['state'], 'sent')
        self.assertEqual(pending_parts(state, now=999), [])


if __name__ == '__main__':
    unittest.main()
