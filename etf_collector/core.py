"""Port of the existing site's parser/quantity contract, with bounded readers."""
from __future__ import annotations
import datetime as dt
import io
import math
import posixpath
import re
import zipfile
from decimal import Decimal
from email.message import Message
from urllib.parse import unquote
from xml.etree import ElementTree as ET
import xlrd

FUNDS = {
    'time': {'name': 'TIME 미국나스닥100액티브', 'code': '426030', 'cu': 20000},
    'koact': {'name': 'KOACT 미국나스닥성장기업액티브', 'code': '0015B0', 'cu': 50000},
}
CU_PROFILE_AS_OF = '2026-10-08'
MAX_BYTES = 4 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024
MAX_ROWS = 2000
MAX_COLS = 32
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def valid_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Date must be YYYY-MM-DD')
    if dt.date.fromisoformat(value).isoformat() != value:
        raise ValueError('Invalid calendar date')
    return value


def source_url(fund, date):
    valid_date(date)
    if fund == 'time':
        return f'https://timeetf.co.kr/pdf_excel.php?idx=2&pdfDate={date}'
    if fund == 'koact':
        return f'https://www.samsungactive.co.kr/excel_pdf.do?fId=2ETFQ1&gijunYMD={date.replace("-", "")}'
    raise ValueError('Unknown fund')


def decimal(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a decimal quantity')
    # BIFF stores numeric cells as IEEE-754 doubles. Shortest round-trip decimal
    # matches the source parser; arithmetic below never uses floats or rounding.
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Non-finite numeric cell')
        value = format(Decimal(str(value)), 'f')
    s = str(value).replace(',', '').strip()
    if len(s) > 128 or not re.fullmatch(r'-?\d+(\.\d+)?', s):
        raise ValueError('Invalid decimal quantity')
    sign = '-' if s.startswith('-') else ''
    whole, _, fraction = s.lstrip('-').partition('.')
    whole = whole.lstrip('0') or '0'
    fraction = fraction.rstrip('0')
    if whole == '0' and not fraction:
        sign = ''
    return sign + whole + ('.' + fraction if fraction else '')


def subtract(a, b):
    a, b = decimal(a), decimal(b)
    scale = max(len(a.partition('.')[2]), len(b.partition('.')[2]))
    def integer(s):
        whole, _, fraction = s.lstrip('-').partition('.')
        return int(whole + fraction.ljust(scale, '0')) * (-1 if s.startswith('-') else 1)
    result = integer(a) - integer(b)
    digits = str(abs(result)).zfill(scale + 1)
    return decimal(('-' if result < 0 else '') + (digits[:-scale] + '.' + digits[-scale:] if scale else digits))


def compare(old, new):
    def indexed(rows):
        result = {r['id']: r for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate source identifier')
        return result
    a, b = indexed(old), indexed(new)
    result = []
    for key in dict.fromkeys([*a, *b]):
        before, after = a.get(key, {}).get('quantity', '0'), b.get(key, {}).get('quantity', '0')
        delta = subtract(after, before)
        positive = lambda x: decimal(x) != '0' and not decimal(x).startswith('-')
        status = 'unchanged' if delta == '0' else 'added' if decimal(before) == '0' and positive(after) else 'removed' if positive(before) and decimal(after) == '0' else 'changed'
        result.append({**b.get(key, a.get(key)), 'before': before, 'after': after, 'delta': delta, 'status': status})
    return result


def _xml(data):
    # Reject entity declarations/DOCTYPE before the standard-library parser.
    # UTF-8 only is sufficient for these official fixtures and fails closed.
    text = data.decode('utf-8-sig')
    if '\x00' in text or re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('XML entity declarations are prohibited')
    return ET.fromstring(text)


def workbook_rows(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Workbook empty or exceeds 4 MiB')
    if data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        book = xlrd.open_workbook(file_contents=data, on_demand=True)
        try:
            sheet = book.sheet_by_index(0)
            if sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLS:
                raise ValueError('Workbook dimensions exceed safety limits')
            return [sheet.row_values(i) for i in range(sheet.nrows)]
        finally:
            book.release_resources()
    if not data.startswith(b'PK\x03\x04'):
        raise ValueError('Response is not an official Excel workbook (possibly unpublished)')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 100 or sum(x.file_size for x in entries) > MAX_EXPANDED:
            raise ValueError('Workbook expanded size exceeds safety limits')
        if len({x.filename for x in entries}) != len(entries):
            raise ValueError('Duplicate ZIP member')
        wb = _xml(archive.read('xl/workbook.xml'))
        sheets = wb.findall('m:sheets/m:sheet', NS)
        if not sheets:
            raise ValueError('Workbook has no sheets')
        relationship = sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels = _xml(archive.read('xl/_rels/workbook.xml.rels'))
        matches = [r for r in rels if r.get('Id') == relationship]
        if len(matches) != 1 or matches[0].get('TargetMode') == 'External':
            raise ValueError('Invalid first-sheet relationship')
        target = matches[0].get('Target', '')
        path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
        if not re.fullmatch(r'xl/worksheets/[A-Za-z0-9_-]+\.xml', path):
            raise ValueError('Unexpected worksheet path')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = _xml(archive.read('xl/sharedStrings.xml'))
            strings = [''.join(t.text or '' for t in si.findall('.//m:t', NS)) for si in root]
        root = _xml(archive.read(path))
        rows, seen = [], set()
        for row in root.findall('m:sheetData/m:row', NS):
            index = int(row.get('r', '0'))
            if not 1 <= index <= MAX_ROWS or index in seen:
                raise ValueError('Invalid or duplicate row index')
            seen.add(index)
            while len(rows) < index:
                rows.append([])
            out, columns = rows[index - 1], set()
            for cell in row.findall('m:c', NS):
                match = re.fullmatch(r'([A-Z]+)(\d+)', cell.get('r', ''))
                if not match or int(match[2]) != index:
                    raise ValueError('Invalid cell coordinate')
                col = 0
                for letter in match[1]:
                    col = col * 26 + ord(letter) - 64
                if not 1 <= col <= MAX_COLS or col in columns:
                    raise ValueError('Invalid or duplicate column index')
                columns.add(col)
                while len(out) < col:
                    out.append('')
                if cell.find('m:f', NS) is not None:
                    raise ValueError('Unexpected formula; cached quantities are not trusted')
                kind = cell.get('t', 'n')
                value = cell.findtext('m:v', default='', namespaces=NS)
                if kind == 's':
                    position = int(value)
                    if not 0 <= position < len(strings):
                        raise ValueError('Invalid shared string index')
                    value = strings[position]
                elif kind == 'inlineStr':
                    value = ''.join(t.text or '' for t in cell.findall('.//m:t', NS))
                elif kind not in ('n', 'str'):
                    raise ValueError('Unsupported cell type')
                # Preserve numeric XML text directly, without float conversion.
                out[col - 1] = value
        return rows


def parse_official(data, fund, date, disposition=''):
    valid_date(date)
    if fund not in FUNDS:
        raise ValueError('Unknown fund')
    rows = workbook_rows(data)
    def at(row, column):
        return rows[row][column] if row < len(rows) and column < len(rows[row]) else ''
    if fund == 'koact':
        if '미국나스닥성장기업액티브' not in str(at(0, 0)):
            raise ValueError('Wrong KOACT fund')
        if str(at(1, 0)).replace('/', '-') != date:
            raise ValueError('Workbook as-of date differs from requested date')
        if at(2, 3) != '종목코드' or at(2, 4) != '수량':
            raise ValueError('KOACT column schema changed')
        evidence = {'type': 'workbook_date', 'value': str(at(1, 0)), 'description': '파일 내부 기준일'}
    else:
        message = Message()
        message['content-disposition'] = disposition
        filename = unquote(message.get_filename() or '')
        if not re.search(r'(?<!\d)' + re.escape(date) + r'(?!\d)', filename):
            raise ValueError('TIME response filename has no matching as-of date')
        if at(0, 0) != '종목코드' or at(0, 2) != '수량':
            raise ValueError('TIME column schema changed')
        evidence = {'type': 'response_filename', 'value': filename, 'description': '공식 다운로드 파일명 (내부 날짜 없음)'}
    holdings, ids = [], set()
    for row in rows[1 if fund == 'time' else 3:]:
        row = row + [''] * max(0, 7 - len(row))
        name = str(row[1]).strip()
        if not name:
            continue
        code = str(row[0 if fund == 'time' else 3]).strip()
        kind = 'stock' if re.search(r'\bEQUITY$', code, re.I) else 'cash' if re.search(r'현금|CASH', name + ' ' + code) else 'other'
        identifier = str(row[2]).strip() if fund == 'koact' else re.sub(r'\s+', ' ', code.upper()) or 'CASH:' + name
        if not identifier or identifier in ids:
            raise ValueError('Missing or duplicate source identifier')
        ids.add(identifier)
        holding = {'id': identifier, 'code': code, 'name': name, 'quantity': decimal(row[2 if fund == 'time' else 4]), 'valueKrw': decimal(row[3 if fund == 'time' else 6]), 'kind': kind}
        if fund == 'koact':
            holding['isin'] = str(row[2])
        holdings.append(holding)
    if sum(r['kind'] == 'stock' for r in holdings) < 10:
        raise ValueError('Disclosure empty or incomplete: fewer than 10 equity rows')
    return {'rows': holdings, 'dateEvidence': evidence}
