import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from etf_collector.core import *
from etf_collector.collect import ROOT, NoRedirect, collect_one, load_archive, run


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = load_archive()

    def rows(self, fund, date):
        return next(s['rows'] for s in self.archive if s['fund'] == fund and s['date'] == date)

    def test_all_originals_and_hashes(self):
        self.assertEqual(len(self.archive), 35)
        for s in self.archive:
            data = (ROOT / 'fixtures/originals' / Path(s['originalKey']).name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s['sha256'])
            self.assertEqual(parse_official(data, s['fund'], s['date'], f'attachment; filename="{s["date"]}.xlsx"')['rows'], s['rows'])

    def test_actual_oct7_oct8(self):
        for fund, count, total in [('time', 0, 41), ('koact', 44, 45)]:
            changes = compare(self.rows(fund, '2026-10-07'), self.rows(fund, '2026-10-08'))
            stocks = [r for r in changes if r['kind'] == 'stock']
            self.assertEqual(len(stocks), total)
            self.assertEqual(sum(r['delta'] != '0' for r in stocks), count)
            if fund == 'koact':
                self.assertEqual(sum(r['status'] == 'added' for r in stocks), 1)
                self.assertEqual(sum(r['delta'].startswith('-') for r in stocks), 43)
                self.assertEqual(next(r['delta'] for r in stocks if r['code'] == 'SPCX US Equity'), '63')
                self.assertEqual(next(r['delta'] for r in stocks if r['code'] == 'AMAT US Equity'), '-0.01')

    def test_exact_decimal_no_precision_context(self):
        self.assertEqual(subtract('290.59', '303.06'), '-12.47')
        self.assertEqual(subtract('1.0600', '1.06'), '0')
        self.assertEqual(subtract('0.000000001', '0'), '0.000000001')
        self.assertEqual(subtract('999999999999999999999999999999.01', '999999999999999999999999999999'), '0.01')
        self.assertEqual(decimal('-0.000'), '0')
        for value in [True, 'NaN', '', '1e3', float('inf')]:
            with self.assertRaises(ValueError): decimal(value)

    def test_identity_add_remove_and_duplicate(self):
        row = {'id': 'US1', 'name': 'A', 'quantity': '1', 'kind': 'stock'}
        self.assertEqual(compare([], [row])[0]['status'], 'added')
        self.assertEqual(compare([row], [])[0]['delta'], '-1')
        with self.assertRaises(ValueError): compare([row, row], [])

    def test_exact_zero_position_events(self):
        row = {'id': 'A', 'quantity': '0', 'kind': 'stock'}
        positive = {**row, 'quantity': '0.000000001'}
        self.assertEqual(compare([], [row])[0]['status'], 'unchanged')
        self.assertEqual(compare([row], [positive])[0]['status'], 'added')
        self.assertEqual(compare([positive], [row])[0]['status'], 'removed')
        self.assertEqual(compare([row], [])[0]['status'], 'unchanged')

    def test_future_cu_review_blocks_comparison(self):
        data = (ROOT / 'fixtures/originals/time-2026-10-08.xlsx').read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            result = collect_one('time', '2026-10-09', self.archive, Path(folder), True, lambda *_: (data, {'httpStatus': 200, 'contentDisposition': 'attachment; filename=2026-10-09.xlsx'}))
            self.assertEqual(result['status'], 'verified_cu_review_required')
            self.assertEqual(result['asOf'], '2026-10-09')
            self.assertNotIn('comparison', result)
            self.assertEqual(result['baselineKind'], 'archived_fixture')

    def test_wrong_date_filename_html_and_schema(self):
        k = (ROOT / 'fixtures/originals/koact-2026-10-08.xls').read_bytes()
        t = (ROOT / 'fixtures/originals/time-2026-10-08.xlsx').read_bytes()
        with self.assertRaises(ValueError): parse_official(k, 'koact', '2026-10-07')
        with self.assertRaises(ValueError): parse_official(t, 'time', '2026-10-08', '')
        with self.assertRaises(ValueError): parse_official(b'<html>not published</html>', 'time', '2026-10-08')
        with self.assertRaises(ValueError): parse_official(t, 'koact', '2026-10-08')
        with self.assertRaises(ValueError): parse_official(t, 'time', '2026-10-08', 'attachment; filename="2026-10-080.xlsx"')
        self.assertEqual(parse_official(t, 'time', '2026-10-08', "attachment; filename*=UTF-8''TIME%202026-10-08.xlsx")['dateEvidence']['type'], 'response_filename')

    def test_input_restrictions_and_limits(self):
        for date in ['2026-02-30', '2026-1-01', '2026-10-08;echo bad', '../../secret']:
            with self.assertRaises(ValueError): source_url('time', date)
        with self.assertRaises(ValueError): source_url('other', '2026-10-08')
        with self.assertRaises(ValueError): workbook_rows(b'0' * (MAX_BYTES + 1))
        with self.assertRaises(ValueError): NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com')

    def test_xlsx_entity_bomb_and_formula_rejected(self):
        original = (ROOT / 'fixtures/originals/time-2026-10-08.xlsx').read_bytes()
        for replacement in [b'<!DOCTYPE test [<!ENTITY x "abc">]><test/>', b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>']:
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(output, 'w') as dst:
                for name in src.namelist(): dst.writestr(name, replacement if name == 'xl/worksheets/sheet1.xml' else src.read(name))
            with self.assertRaises(ValueError): workbook_rows(output.getvalue())

    def test_failed_probe_not_unchanged_preserves_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            result = collect_one('time', '2026-10-08', self.archive, Path(folder), True, lambda *_: (b'<html>not published</html>', {'httpStatus': 200, 'contentDisposition': ''}))
            self.assertEqual(result['status'], 'validation_error')
            self.assertIsNone(result['asOf'])
            self.assertEqual(result['baselineAsOf'], '2026-10-07')
            self.assertNotIn('comparison', result)
            self.assertEqual((Path(folder) / result['originalKey']).read_bytes(), b'<html>not published</html>')

    def test_network_failure_and_missing_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            def fail(*_): raise TimeoutError('test timeout')
            result = collect_one('time', '2026-10-08', self.archive, Path(folder), True, fail)
            self.assertEqual(result['status'], 'download_error')
            self.assertIsNone(result['asOf'])
            missing = collect_one('time', '2026-10-09', self.archive, Path(folder))
            self.assertEqual(missing['status'], 'fixture_error')
            self.assertEqual(missing['baselineAsOf'], '2026-10-08')

    def test_offline_run_output_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Offline run attempted network')):
            directory = Path(folder) / 'run'
            self.assertEqual(run('2026-10-08', directory), 0)
            report = json.loads((directory / 'report.json').read_text())
            self.assertFalse(report['deliveryEnabled'])
            self.assertEqual([r['status'] for r in report['results']], ['verified_unchanged', 'verified_changed', 'verified_changed'])
            self.assertEqual([r['cu'] for r in report['results']], [20000, 20000, 50000])
            self.assertEqual(report['results'][0]['dateEvidence']['type'], 'archive_record')
            with self.assertRaises(FileExistsError): run('2026-10-08', directory)


if __name__ == '__main__':
    unittest.main()
