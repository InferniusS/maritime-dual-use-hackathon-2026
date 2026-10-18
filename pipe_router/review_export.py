"""Offline review workbook. Centerline legs are not purchased pipe parts."""
from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


def challenge_panel(result):
    review = result.get('challenge_review')
    if not review:
        return ''
    rows = []
    summaries = []
    candidate_label = 'Suggested' if result.get('candidate') else 'Rejected candidate'
    for label, key in [('Original', 'original'), (candidate_label, 'candidate')]:
        assessment = review.get(key)
        if not assessment:
            continue
        counts = assessment['counts']
        summaries.append(f"{label}: {counts['pass']} passed, {counts['fail']} failed, {counts['needs_review']} need review")
        for finding in assessment['findings']:
            values = [label, finding.get('id', finding.get('rule_id', '')).replace('_', ' ').capitalize(),
                      finding['status'].replace('_', ' ').capitalize(),
                      'Mandatory' if finding['severity'] == 'error' else 'Preference', finding['message']]
            rows.append('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in values) + '</tr>')
    return ('<section class="panel"><h2>Challenge rule review</h2>'
            '<p>V3 checks. Missing evidence is marked needs_review. A pass covers the named check and supplied evidence only. '
            'This is not a finished pipe assembly or full standards approval.</p>'
            '<p>' + html.escape(' · '.join(summaries)) + '</p>'
            '<p><a href="route-review.xlsx">Download Excel review</a></p>'
            '<details><summary>Show all rule findings</summary><div style="overflow-x:auto"><table><thead><tr><th>Route</th><th>Rule</th><th>Result</th><th>Rule type</th><th>Finding</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></details></section>')


def _text(value):
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    # XML 1.0 cannot contain control characters. All strings are literal cells,
    # never formulas, including values beginning with '=' from a source drawing.
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767]


def _column(index):
    result = ''
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def _sheet(rows):
    body = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for index, value in enumerate(row, 1):
            address = f'{_column(index)}{row_index}'
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                cells.append(f'<c r="{address}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{address}" t="inlineStr"><is><t xml:space="preserve">{escape(_text(value))}</t></is></c>')
        body.append(f'<row r="{row_index}">' + ''.join(cells) + '</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            '<sheetData>' + ''.join(body) + '</sheetData></worksheet>')


def write_review_xlsx(layout, result, destination):
    """Write literal XLSX cells without installing another runtime dependency."""
    summary = [
        ['Item', 'Value'], ['Drawing / layout', layout.get('name', '')],
        ['Rule profile', result.get('rule_profile', 'prototype-unverified')],
        ['Result', result['status']], ['Coordinate units', 'mm'],
        ['Coordinate frame', 'Source drawing coordinates. Confirm alignment before implementation.'],
        ['Scope', 'Review data only. Centerline segments are not a fitting catalog or fabricated parts list.'],
        ['Original length mm', result['original']['length']],
        ['Suggested length mm', result['candidate']['length'] if result.get('candidate') else 'No accepted suggestion'],
        ['Source handles', layout.get('cad_context', {}).get('source_handles', [])],
        ['Source hash', layout.get('cad_context', {}).get('source_sha256', '')],
        ['Model context', layout.get('model_context', {}).get('coordinate_frame', {})],
        ['Full engineering compliance', 'Not verified'],
    ]
    findings = [['Route', 'Rule', 'Status', 'Severity', 'Finding', 'Measured', 'Required', 'Source', 'Component references']]
    review = result.get('challenge_review', {})
    for key in ('original', 'candidate'):
        for item in (review.get(key) or {}).get('findings', []):
            label = 'rejected_candidate' if key == 'candidate' and not result.get('candidate') else key
            findings.append([label, item.get('id', item.get('rule_id', '')), item['status'], item['severity'], item['message'],
                             item.get('measured'), item.get('required'), item.get('source'), item.get('source_refs', [])])
    points = [['Route', 'Point', 'X mm', 'Y mm', 'Z mm']]
    segments = [['Route', 'Leg', 'Start X mm', 'Start Y mm', 'Start Z mm', 'End X mm', 'End Y mm', 'End Z mm', 'Centerline length mm', 'Outer diameter mm', 'Component status']]
    for key in ('original', 'candidate'):
        route = result.get(key)
        if not route:
            continue
        for i, point in enumerate(route['points'], 1):
            points.append([key, i, *point])
        for i, (a, b) in enumerate(zip(route['points'], route['points'][1:]), 1):
            segments.append([key, i, *a, *b, math.dist(a, b), layout['pipe']['diameter'], 'Centerline leg only. Fitting and fabrication design required.'])
    parts = [['Original declared piece', 'Declared length mm', 'Bends (declared data)', 'Source references', 'Route binding', 'Proposed replacement']]
    engineering = layout.get('engineering', {})
    for piece in engineering.get('pieces', []):
        parts.append([piece.get('id', ''), piece.get('centerline_length_mm', ''), piece.get('bends', []), piece.get('source_refs', []),
                      'Matches original' if engineering.get('route_points') == result['original']['points'] else 'Not verified',
                      'Not generated. Do not order from this review.'])
    sheets = [('Summary', summary), ('Rule findings', findings), ('Route points', points), ('Route legs', segments), ('Declared pieces', parts)]
    main_ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel_ns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    package_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
    content_types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    content_types += ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1)) + '</Types>'
    workbook = f'<workbook xmlns="{main_ns}" xmlns:r="{rel_ns}"><sheets>' + ''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>'
    rels = f'<Relationships xmlns="{package_ns}">' + ''.join(f'<Relationship Id="rId{i}" Type="{rel_ns}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1)) + '</Relationships>'
    with ZipFile(Path(destination), 'w', ZIP_DEFLATED) as book:
        book.writestr('[Content_Types].xml', content_types)
        book.writestr('_rels/.rels', f'<Relationships xmlns="{package_ns}"><Relationship Id="rId1" Type="{rel_ns}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr('xl/workbook.xml', workbook)
        book.writestr('xl/_rels/workbook.xml.rels', rels)
        for i, (_, rows) in enumerate(sheets, 1):
            book.writestr(f'xl/worksheets/sheet{i}.xml', _sheet(rows))
