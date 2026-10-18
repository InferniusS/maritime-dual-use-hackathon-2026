"""Offline review workbook. Centerline legs are not purchased pipe parts."""
from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


EVIDENCE_LABELS = {
    'computed_geometry': 'Computed from geometry',
    'supplied_measurement': 'Supplied measurement',
    'reviewer_declaration': 'Reviewer declaration',
    'unimplemented': 'Not implemented',
}


def evidence_labels(finding):
    """Keep finding origin separate from outcome, including older results.

    A pass is never used to infer automatic calculation or complete coverage.
    Generated-piece reports predate the V3 origin fields; only their explicit
    evidence_basis is recognized, rather than guessing from measured values.
    """
    kind = finding.get('evidence_kind')
    if kind is not None:
        return (EVIDENCE_LABELS.get(kind, 'Evidence not classified'),
                str(finding.get('evidence_completeness', 'not_classified')).replace('_', ' '),
                finding.get('evidence_note', 'Evidence details were not recorded.'))
    basis = finding.get('evidence_basis')
    if basis == 'generated_component_geometry':
        return ('Computed from generated geometry', 'generated pieces only',
                'Calculated from the proposed pieces; source completeness, catalog suitability and fabrication approval are separate.')
    if basis == 'not_provided_for_generated_components':
        return ('Evidence not provided', 'not provided for generated pieces',
                'This item has no supporting evidence for the proposed pieces; it is not an automated pass.')
    return ('Evidence not classified', 'not classified',
            'This saved result did not record an evidence origin; its outcome does not establish automatic verification.')


def _evidence_text(finding):
    label, completeness, note = evidence_labels(finding)
    return f'{label}. Coverage: {completeness}. {note}'


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
        if assessment.get('evidence_counts'):
            origins = ', '.join(f'{EVIDENCE_LABELS.get(kind, kind)}: {count}'
                                for kind, count in assessment['evidence_counts'].items())
            summaries.append(f'{label} finding origins (all outcomes, not passed checks): {origins}')
        for finding in assessment['findings']:
            values = [label, finding.get('id', finding.get('rule_id', '')).replace('_', ' ').capitalize(),
                      finding['status'].replace('_', ' ').capitalize(),
                      'Mandatory' if finding['severity'] == 'error' else 'Preference',
                      _evidence_text(finding), finding['message']]
            rows.append('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in values) + '</tr>')
    return ('<section class="panel"><h2>Challenge rule review</h2>'
            '<p>V3 checks. Missing evidence is marked needs_review. A pass covers the named check and supplied evidence only. '
            'Supplied measurements and reviewer declarations are not independently extracted or verified in CAD. '
            'Computed geometry covers the recorded inputs only. This is not a finished pipe assembly or full standards approval.</p>'
            '<p>' + html.escape(' · '.join(summaries)) + '</p>'
            '<p><a href="route-review.xlsx">Download Excel review</a></p>'
            '<details><summary>Show all rule findings</summary><div style="overflow-x:auto"><table><thead><tr><th>Route</th><th>Rule</th><th>Result</th><th>Rule type</th><th>Evidence</th><th>Finding</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></details></section>')


def _measurement_text(value):
    if value is None:
        return 'Not measured'
    if isinstance(value, float):
        return f'{value:,.3f}'.rstrip('0').rstrip('.')
    if isinstance(value, dict):
        return '; '.join(f"{str(key).replace('_', ' ')}: {_measurement_text(item)}" for key, item in value.items())
    if isinstance(value, (tuple, list)):
        shown = ' | '.join(_measurement_text(item) for item in value[:8])
        return shown + (f' | {len(value)-8} more records in the full result' if len(value) > 8 else '')
    return str(value)


def assembly_panel(result):
    """Describe generated geometry without presenting it as catalog parts."""
    assembly = result.get('assembly')
    if not assembly:
        return ''
    ready = assembly.get('status') == 'ready' and result.get('candidate') is not None
    title = 'Rounded pipe proposal' if ready else 'Rounded pipe proposal blocked'
    text = ('The green shapes use the selected outer diameter and rounded centerline. '
            'They are capped outer envelopes: wall thickness, vendor fittings and fabrication approval are not included.'
            if ready else 'The rounded geometry was not accepted. The centerline alone does not establish that a pipe assembly fits.')
    details = [text]
    if assembly.get('bend_radius_mm') is not None:
        details.append(f"Selected centerline bend radius: {assembly['bend_radius_mm']:g} mm. Radius basis: {assembly.get('radius_source', 'Unspecified')}.")
    if assembly.get('reference_meshes'):
        details.append('Orange shows the rounded baseline at the supplied pipe width, using the same geometric radius assumption.'
                       if assembly.get('reference_representation') == 'rounded_baseline' else
                       'Orange shows the original polyline at the supplied pipe width. Its corners are an unfitted reference, not verified original bends.')
    metrics = assembly.get('metrics') or {}
    if ready:
        details.append(f"Proposed rounded centerline: {metrics.get('centerline_length_mm', metrics.get('rounded_length_mm', 0)):,.1f} mm. "
                       f"Rounded bends: {metrics.get('bend_count', 0)}. The schedule is geometric review information, not an orderable parts list.")
    findings = ''.join('<li>' + html.escape(str(item.get('message', item))) + '</li>' for item in assembly.get('findings', []))
    rows = []
    if ready:
        for part in assembly.get('components', []):
            values = [part.get('id', ''), part.get('kind', ''),
                      f"{part.get('length_mm', 0):,.1f}",
                      f"{part['radius_mm']:g}" if part.get('radius_mm') is not None else '',
                      f"{part['angle_deg']:g}" if part.get('angle_deg') is not None else '',
                      ', '.join(f'{v:g}' for v in part.get('start', [])),
                      ', '.join(f'{v:g}' for v in part.get('end', []))]
            rows.append('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in values) + '</tr>')
    table = ('<details><summary>Proposed geometric component schedule</summary><p>For straight pieces, length is the geometric cut length. '
             'For bends, it is centerline arc length. Connections, wall thickness and fabrication allowances require review. All coordinates and lengths are mm.</p>'
             '<div style="overflow-x:auto"><table><thead><tr><th>ID</th><th>Geometry</th><th>Length</th><th>Radius</th><th>Angle °</th><th>Start XYZ</th><th>End XYZ</th>'
             '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></details>') if rows else ''
    piece_review = assembly.get('manufacturing_assessment') or {}
    piece_panel = ''
    if piece_review:
        counts = piece_review['counts']
        check_rows = []
        for item in piece_review['findings']:
            values = [item['rule_id'].replace('_', ' ').capitalize(), item['status'].replace('_', ' ').capitalize(),
                      _evidence_text(item), item['message'], _measurement_text(item.get('measured'))]
            check_rows.append('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in values) + '</tr>')
        piece_panel = ('<h3>Proposed-piece checks and missing evidence</h3><p>'
            + f"{counts['pass']} passed · {counts['fail']} failed · {counts['needs_review']} need review. "
            + 'These measurements describe the generated pieces. They do not approve catalog fittings, weld design or fabrication.</p><p>'
            + html.escape(piece_review['piece_definition'])
            + '</p><details><summary>Measured piece checks and remaining decisions</summary><div style="overflow-x:auto"><table><thead><tr>'
            + '<th>Check</th><th>Result</th><th>Evidence</th><th>Finding</th><th>Measurement</th></tr></thead><tbody>'
            + ''.join(check_rows) + '</tbody></table></div></details>')
    return ('<section class="panel"><h2>' + title + '</h2>'
            + ''.join('<p>' + html.escape(item) + '</p>' for item in details)
            + ('<ul>' + findings + '</ul>' if findings else '') + piece_panel + table + '</section>')


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
        ['Original sampled polyline length mm', result['original']['length']],
        ['Suggested design-polyline length mm', result['candidate']['length'] if result.get('candidate') else 'No accepted suggestion'],
        ['Source handles', layout.get('cad_context', {}).get('source_handles', [])],
        ['Source hash', layout.get('cad_context', {}).get('source_sha256', '')],
        ['Model context', layout.get('model_context', {}).get('coordinate_frame', {})],
        ['Full engineering compliance', 'Not verified'],
    ]
    findings = [['Route', 'Rule', 'Status', 'Severity', 'Finding', 'Measured', 'Required', 'Source', 'Component references',
                 'Evidence kind', 'Evidence completeness', 'Evidence note']]
    review = result.get('challenge_review', {})
    for key in ('original', 'candidate'):
        assessment = review.get(key) or {}
        label = 'rejected_candidate' if key == 'candidate' and not result.get('candidate') else key
        if assessment.get('evidence_counts'):
            summary.append([f'{label} finding origins (all outcomes)', assessment['evidence_counts']])
            summary.append([f'{label} outcomes by evidence origin', assessment.get('evidence_status_counts', {})])
            summary.append([f'{label} evidence count scope', assessment.get('evidence_counts_note', '')])
        for item in assessment.get('findings', []):
            label = 'rejected_candidate' if key == 'candidate' and not result.get('candidate') else key
            findings.append([label, item.get('id', item.get('rule_id', '')), item['status'], item['severity'], item['message'],
                             item.get('measured'), item.get('required'), item.get('source'), item.get('source_refs', []),
                             *evidence_labels(item)])
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
    assembly = result.get('assembly')
    if assembly:
        accepted = assembly.get('status') == 'ready' and result.get('candidate') is not None
        summary.extend([
            ['Rounded proposal status', assembly.get('status', '')],
            ['Bend radius mm', assembly.get('bend_radius_mm', '')],
            ['Radius basis', assembly.get('radius_source', 'Unspecified')],
            ['Original visual reference', assembly.get('reference_representation', 'unfitted_polyline')],
            ['Original rounded centerline length mm', (assembly.get('baseline_metrics') or {}).get('centerline_length_mm', 'Not assessed as an assembly')],
            ['Suggested rounded centerline length mm', assembly.get('metrics', {}).get('centerline_length_mm', 'Unavailable') if accepted else 'No accepted rounded proposal'],
            ['Assembly scope', 'Geometric outer envelopes only. Catalog selection, wall thickness and fabrication approval not verified.'],
        ])
        components = [['Component ID', 'Geometric type', 'Start X mm', 'Start Y mm', 'Start Z mm',
                       'End X mm', 'End Y mm', 'End Z mm', 'Outer diameter mm',
                       'Straight cut length mm', 'Bend centerline length mm', 'Bend radius mm', 'Bend angle degrees',
                       'Bend center X mm', 'Bend center Y mm', 'Bend center Z mm', 'Status']]
        if accepted:
            for part in assembly.get('components', []):
                straight = part.get('kind') == 'straight'
                components.append([part.get('id', ''), part.get('kind', ''), *part.get('start', ['', '', '']),
                                   *part.get('end', ['', '', '']), part.get('outer_diameter_mm', assembly.get('outer_diameter_mm', '')),
                                   part.get('length_mm', '') if straight else '', part.get('length_mm', '') if not straight else '',
                                   part.get('radius_mm', ''), part.get('angle_deg', ''), *part.get('center', ['', '', '']),
                                   'Geometric proposal. Catalog and fabrication not verified.'])
        else:
            components.append(['No accepted rounded assembly', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '',
                               'Blocked. Do not use intermediate components as a parts list.'])
        sheets.append(('Proposed assembly', components))
        piece_review = assembly.get('manufacturing_assessment') or {}
        if piece_review:
            summary.append(['Proposed-piece checks', piece_review['counts']])
            checks = [['Check', 'Status', 'Severity', 'Finding', 'Measured', 'Required', 'Proposed piece IDs', 'Evidence basis',
                       'Evidence kind', 'Evidence completeness', 'Evidence note']]
            for item in piece_review['findings']:
                checks.append([item['rule_id'], item['status'], item['severity'], item['message'], item.get('measured'),
                               item.get('required'), item.get('component_ids', []), item.get('evidence_basis', ''),
                               *evidence_labels(item)])
            sheets.append(('Proposed piece checks', checks))
            joints = [['Interface ID', 'Type', 'X mm', 'Y mm', 'Z mm', 'Status']]
            if accepted:
                for joint in piece_review['joint_locations']:
                    joints.append([joint['id'], joint['kind'], *joint['point_mm'], 'Proposed interface only. Weld and mating design require review.'])
            else:
                joints.append(['No accepted proposal', '', '', '', '', 'Blocked; do not implement intermediate interfaces.'])
            sheets.append(('Proposed interfaces', joints))
    write_workbook(sheets, destination)


def write_workbook(sheets, destination):
    """Write a small review workbook using literal cells, never source formulas."""
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
