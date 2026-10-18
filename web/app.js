const $ = id => document.getElementById(id)
const scale = {mm: 1, m: 1000, in: 25.4}
const clone = value => structuredClone(value)
const number = value => Number(value).toLocaleString(undefined, {maximumFractionDigits: 1})
let config, layout, cad, cadSource = null
let cadId = null, routeDrawing = null, routeCandidate = null, routeDisplay = null
let cadNetwork = null, baselineLayout = null
let inputVersion = 0

function error(message = '') {
  $('error').hidden = !message
  $('error').textContent = message
}
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  })
  const record = await response.json()
  if (!response.ok) throw new Error(record.error || 'The request did not finish')
  return record
}
function option(value, label) {
  const item = document.createElement('option')
  item.value = value
  item.textContent = label
  return item
}
function paragraph(text, parent, className = '') {
  const node = document.createElement('p')
  node.textContent = text
  node.className = className
  parent.append(node)
  return node
}
function list(items, parent) {
  const node = document.createElement('ul')
  for (const item of items) {
    const li = document.createElement('li')
    li.textContent = item
    node.append(li)
  }
  parent.append(node)
}
function link(label, href, parent) {
  const node = document.createElement('a')
  node.href = href
  node.textContent = label
  node.target = '_blank'
  node.rel = 'noopener'
  if (label.startsWith('Save ')) node.download = href.split('/').at(-1)
  parent.append(node)
}
function line(points, name, color, width = 5, extra = {}) {
  return {type: 'scatter3d', mode: 'lines', name, x: points.map(p => p?.[0] ?? null), y: points.map(p => p?.[1] ?? null), z: points.map(p => p?.[2] ?? null), line: {color, width}, ...extra}
}
function boxPoints(box) {
  const [a,b,c] = box.min, [d,e,f] = box.max
  return [[a,b,c],[d,b,c],[d,e,c],[a,e,c],[a,b,f],[d,b,f],[d,e,f],[a,e,f]]
}
const faces = [[0,1,2],[0,2,3],[4,6,5],[4,7,6],[0,4,5],[0,5,1],[1,5,6],[1,6,2],[2,6,7],[2,7,3],[3,7,4],[3,4,0]]
const edges = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]]
function mesh(vertices, triangles, name, color, opacity = .3) {
  return {type: 'mesh3d', x: vertices.map(p => p[0]), y: vertices.map(p => p[1]), z: vertices.map(p => p[2]), i: triangles.map(p => p[0]), j: triangles.map(p => p[1]), k: triangles.map(p => p[2]), name, color, opacity, hoverinfo: 'name', flatshading: false, showscale: false}
}
function clipMesh(vertices, triangles, low, high) {
  const resultVertices = [], resultTriangles = []
  for (const triangle of triangles) {
    let polygon = triangle.map(index => vertices[index])
    for (let axis=0; axis<3; axis++) {
      for (const [boundary,direction] of [[low[axis],1],[high[axis],-1]]) {
        if (!polygon.length) break
        const clipped = []
        let previous = polygon.at(-1), previousInside = direction*(previous[axis]-boundary) >= 0
        for (const current of polygon) {
          const currentInside = direction*(current[axis]-boundary) >= 0
          if (currentInside !== previousInside) {
            const fraction = (boundary-previous[axis])/(current[axis]-previous[axis])
            const intersection = previous.map((value,k) => value + fraction*(current[k]-value))
            intersection[axis] = boundary
            clipped.push(intersection)
          }
          if (currentInside) clipped.push(current)
          previous = current
          previousInside = currentInside
        }
        polygon = clipped
      }
    }
    if (polygon.length >= 3) {
      const offset = resultVertices.length
      resultVertices.push(...polygon)
      for (let i=1; i<polygon.length-1; i++) resultTriangles.push([offset,offset+i,offset+i+1])
    }
  }
  return {vertices: resultVertices, triangles: resultTriangles}
}
function capsuleMesh(capsules) {
  const vertices = [], triangles = [], sides = 12, steps = 4
  const cross = (a,b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
  for (const capsule of capsules) {
    const {start,end,radius} = capsule
    const delta = end.map((v,k) => v-start[k]), length = Math.hypot(...delta)
    const axis = length ? delta.map(v => v/length) : [0,0,1]
    let u = cross(axis, Math.abs(axis[0]) < .9 ? [1,0,0] : [0,1,0])
    const norm = Math.hypot(...u)
    u = u.map(v => v/norm)
    const v = cross(axis,u)
    let capsuleVertices = [], capsuleTriangles = []
    for (const [center,first] of [[start,-Math.PI/2],[end,0]]) {
      for (let step=0; step<=steps; step++) {
        const angle = first + step*Math.PI/(2*steps)
        const axial = radius*Math.sin(angle), radial = radius*Math.cos(angle)
        for (let j=0; j<sides; j++) {
          const theta = 2*Math.PI*j/sides
          capsuleVertices.push(center.map((coordinate,k) => coordinate + axial*axis[k] + radial*(u[k]*Math.cos(theta)+v[k]*Math.sin(theta))))
        }
      }
    }
    for (let row=0; row<2*(steps+1)-1; row++) {
      for (let j=0; j<sides; j++) {
        const a = row*sides+j, b = row*sides+(j+1)%sides
        capsuleTriangles.push([a,b,a+sides],[b,b+sides,a+sides])
      }
    }
    if (capsule.clip_min) {
      const clipped = clipMesh(capsuleVertices,capsuleTriangles,capsule.clip_min,capsule.clip_max)
      capsuleVertices = clipped.vertices
      capsuleTriangles = clipped.triangles
    }
    const offset = vertices.length
    vertices.push(...capsuleVertices)
    for (const triangle of capsuleTriangles) triangles.push(triangle.map(index => offset+index))
  }
  return {vertices,triangles}
}
function geometryCoverage(input) {
  const obstacles = input.obstacles || [], shaped = obstacles.filter(item => item.capsules?.length).length
  let text = `${obstacles.length} nearby obstacle objects. ${shaped} use pipe-shaped envelopes. ${obstacles.length-shaped} use box envelopes.`
  const counts = input.cad_context?.counts || {}
  if (counts.solid_instances_without_extracted_geometry) text += ` ${counts.solid_instances_without_extracted_geometry} source solid instances have no extracted geometry.`
  if (counts.explicit_curve_objects_unchecked) text += ` ${counts.explicit_curve_objects_unchecked} explicit curve objects are not collision obstacles.`
  return text
}
function diagnosticText(item) {
  const message = item.message || item.code || 'Review finding'
  const details = []
  if (item.stage) details.push(item.stage.replaceAll('_',' '))
  if (item.source_handle) {
    const escapedHandle = String(item.source_handle).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const alreadyNamed = new RegExp('\\bsource\\s+' + escapedHandle + '(?![\\w-])','i').test(message)
    const placement = (item.instance_path || []).join('/')
    if (!alreadyNamed) details.push(`source ${item.source_handle}${placement ? ' in ' + placement : ''}`)
    else if (placement && !message.includes(placement)) details.push(`instance ${placement}`)
  }
  if (item.obstacle && !message.includes(item.obstacle)) details.push(item.obstacle)
  if (item.obstacle_method) details.push(item.obstacle_method.replaceAll('_',' '))
  if (item.point) details.push(`point ${item.point.map(number).join(', ')} mm`)
  return message + (details.length ? ` (${details.join(' · ')})` : '')
}
function plotLayout(key) {
  const axis = title => ({title: {text: title}, backgroundcolor: '#0b1726', gridcolor: '#263c53', zerolinecolor: '#3c546e', showbackground: true})
  return {paper_bgcolor: '#101e30', plot_bgcolor: '#101e30', font: {color: '#c8d6e8', family: 'system-ui'}, margin: {l: 0, r: 0, t: 12, b: 0}, uirevision: key, legend: {orientation: 'h', x: .01, y: 1, groupclick: 'togglegroup'}, scene: {xaxis: axis('X · mm'), yaxis: axis('Y · mm'), zaxis: axis('Z · mm'), aspectmode: 'data', camera: {eye: {x: 1.4, y: 1.6, z: 1.1}}}}
}
const plotConfig = {responsive: true, displaylogo: false, modeBarButtonsToRemove: ['sendDataToCloud', 'sendChartToCloud']}
function normalizedPreview(input) {
  const copy = clone(input), factor = scale[copy.units || 'mm']
  if (!factor || !copy.pipe || !copy.bounds || !Array.isArray(copy.obstacles)) throw new Error('The layout needs units, a pipe, bounds and an obstacles list')
  const point = p => {
    if (!Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite)) throw new Error('Each coordinate must contain three numbers')
    return p.map(v => v * factor)
  }
  copy.pipe.start = point(copy.pipe.start)
  copy.pipe.end = point(copy.pipe.end)
  copy.pipe.original_route = copy.pipe.original_route.map(point)
  for (const b of [copy.bounds, ...copy.obstacles]) {b.min = point(b.min); b.max = point(b.max)}
  for (const obstacle of copy.obstacles) {
    if (obstacle.capsules !== undefined) {
      if (!Array.isArray(obstacle.capsules) || !obstacle.capsules.length) throw new Error('Pipe envelopes need at least one capsule')
      for (const capsule of obstacle.capsules) {
        capsule.start = point(capsule.start)
        capsule.end = point(capsule.end)
        if (!Number.isFinite(capsule.radius) || capsule.radius <= 0) throw new Error('Envelope radii must be positive numbers')
        capsule.radius *= factor
        if (capsule.clip_min !== undefined || capsule.clip_max !== undefined) {
          capsule.clip_min = point(capsule.clip_min)
          capsule.clip_max = point(capsule.clip_max)
          if (capsule.clip_min.some((value,k) => value > capsule.clip_max[k])) throw new Error('Envelope clip bounds must be ordered')
        }
      }
    }
  }
  for (const fixed of copy.protected_endpoints || []) {
    fixed.points = fixed.points.map(point)
    if (Number.isFinite(fixed.max_distance)) fixed.max_distance *= factor
  }
  copy.pipe.diameter *= factor
  for (const key of ['clearance','grid_step','bend_penalty','bend_radius']) {
    if (Number.isFinite(copy.rules?.[key])) copy.rules[key] *= factor
  }
  copy.units = 'mm'
  return copy
}
function drawRoute(input, result = null) {
  routeDisplay = {input, result}
  const linked = Boolean(input.cad_context && routeDrawing)
  const traces = linked ? geometryTraces(routeDrawing, {context: true, candidate: routeCandidate}) : []
  if (!linked || $('show-obstacles').checked) {
    const shownGroups = new Set(), sources = new Map((input.cad_context?.obstacle_sources || []).map(item => [item.id,item]))
    const escapeHover = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))
    for (const obstacle of input.obstacles) {
      const kind = obstacle.capsules?.length ? 'pipe-envelopes' : 'box-envelopes'
      const source = sources.get(obstacle.id) || obstacle
      let hover = escapeHover(obstacle.id)
      if (source.source_handle) hover += '<br>Source ' + escapeHover(source.source_handle)
      if (source.instance_path?.length) hover += '<br>Instance ' + escapeHover(source.instance_path.join('/'))
      hover += '<br>Conservative envelope<extra></extra>'
      const presentation = {legendgroup: kind, showlegend: !shownGroups.has(kind), hovertemplate: hover, meta: {obstacle_id: obstacle.id}}
      shownGroups.add(kind)
      if (obstacle.capsules?.length) {
        const geometry = capsuleMesh(obstacle.capsules)
        traces.push({...mesh(geometry.vertices, geometry.triangles, 'Pipe envelopes', '#99b8d0', linked ? .3 : .45), ...presentation})
      } else if (linked) {
        const v = boxPoints(obstacle)
        traces.push({...line(edges.flatMap(([a,b]) => [v[a],v[b],null]), 'Box envelopes', '#e49b9b', 2), ...presentation})
      } else traces.push({...mesh(boxPoints(obstacle), faces, 'Box envelopes', '#9faabd', .28), ...presentation})
    }
  }
  const vertices = boxPoints(input.bounds)
  traces.push(line(edges.flatMap(([a,b]) => [vertices[a], vertices[b], null]), linked ? 'Assumed local test area' : 'Allowed box', '#60768e', 2))
  traces.push(line(result?.original.points || input.pipe.original_route, 'Original', '#ffb45e', 7))
  if (result?.candidate) traces.push(line(result.candidate.points, 'Suggested', '#55e1b9', 6))
  for (const fixed of input.protected_endpoints || []) traces.push(line(fixed.points, 'Fixed ' + fixed.endpoint + ' connection', '#ffd873', 9))
  const stops = (result?.diagnostics || []).filter(item => Array.isArray(item.point) && item.point.length === 3 && item.point.every(Number.isFinite))
  if (stops.length) traces.push({...line(stops.map(item => item.point), 'Search diagnostic points', '#ff7588'), mode: 'markers', marker: {color: '#ff7588', size: 7, symbol: 'diamond'}, text: stops.map(diagnosticText), hovertemplate: '%{text}<extra></extra>'})
  const ports = [input.pipe.start, input.pipe.end]
  traces.push({...line(ports, 'Fixed connections', '#ffd873'), mode: 'markers+text', marker: {color: '#ffd873', size: 5}, text: ['Start', 'End'], textposition: 'top center'})
  const view = plotLayout(input.name + (linked ? ':' + $('focus-route').checked : ''))
  if (linked) {
    const bounds = $('focus-route').checked ? input.bounds : {
      min: input.bounds.min.map((v,i) => Math.min(v, routeDrawing.bounds?.min[i] ?? v)),
      max: input.bounds.max.map((v,i) => Math.max(v, routeDrawing.bounds?.max[i] ?? v))
    }
    frameBounds(view, bounds)
  }
  $('route-legend').textContent = linked
    ? 'The imported drawing stays visible. Orange is the selected run. Green is the suggestion. Checks use nearby conservative obstacle envelopes inside the local test area.'
    : 'Orange is the original route. Green is the suggestion. Drag to rotate. Lines show pipe centers. Shaded obstacles are conservative envelopes. Checks include thickness and clearance.'
  if (input.obstacles.some(item => item.capsules?.length)) $('route-legend').textContent += ' Pipe-shaped envelopes are approximations of occupied space. Their broad search boxes are not shown as occupied volume.'
  if (input.obstacles.some(item => item.capsules?.some(capsule => capsule.clip_min))) $('route-legend').textContent += ' Trimmed envelope ends follow analytic bounds. Display meshes can have open cut faces.'
  Plotly.react($('route-plot'), traces, view, plotConfig)
}
function unitsLabels() {document.querySelectorAll('.input-unit').forEach(node => node.textContent = $('input-units').value)}
function syncForm() {
  $('layout-name').textContent = layout.name || 'Untitled layout'
  $('layout-note').textContent = layout.description || ''
  $('provenance').textContent = layout.benchmark?.synthetic_mutation ? 'CONTROLLED DETOUR / REAL CAD CONTEXT' : layout.provenance === 'synthetic' ? 'SYNTHETIC TEST LAYOUT' : 'CAD DERIVED INPUT / REVIEW REQUIRED'
  const linked = Boolean(layout.cad_context)
  $('cad-linked-note').hidden = !linked
  $('cad-view-controls').hidden = !linked || !routeDrawing
  $('cad-linked-note').textContent = linked ? `${geometryCoverage(layout)} Diameter is inferred from the source. To correct source units or choose another run use the CAD view.` : ''
  $('input-units').disabled = linked
  $('diameter').disabled = linked
  const contacts = layout.cad_context?.endpoint_contacts || []
  $('connection-controls').hidden = !linked || !contacts.length
  $('connection-summary').textContent = `${contacts.length} object contacts at the selected route ends. Review these before treating them as intended connections.`
  $('connection-list').replaceChildren()
  list(contacts.map(item => `${item.endpoint} · source ${item.source_handle} · ${item.obstacle_id}`), $('connection-list'))
  $('assume-connections').checked = Boolean(layout.protected_endpoints?.length)
  $('max-stub').value = layout.protected_endpoints?.[0]?.max_distance ?? 250
  $('connection-status').textContent = layout.protected_endpoints?.length ? 'Original connection ends are fixed. Only the middle may change.' : 'No connection exemptions are active.'
  $('benchmark-controls').hidden = !linked
  $('make-detour').disabled = Boolean(layout.benchmark?.synthetic_mutation)
  $('restore-baseline').hidden = !baselineLayout || !layout.benchmark?.synthetic_mutation
  $('benchmark-status').textContent = layout.benchmark?.synthetic_mutation ? 'Artificial detour added to a checked baseline. Run the search to measure whether it improves.' : 'The baseline must pass the current checks before a detour can be added.'
  $('benchmark-note').hidden = !layout.benchmark?.synthetic_mutation
  $('benchmark-note').textContent = layout.benchmark?.synthetic_mutation ? `Unchanged drawing baseline ≈ ${number(layout.benchmark.baseline.validation.length)} mm. This test deliberately adds ${number(layout.benchmark.mutation.added_length_mm)} mm. It is not a defect found in the supplied drawing.` : ''
  if (linked) {
    if (!$('scenario').querySelector('option[value="cad-selected"]')) $('scenario').append(option('cad-selected','Selected CAD run'))
    $('scenario').value = 'cad-selected'
  } else $('scenario').querySelector('option[value="cad-selected"]')?.remove()
  $('input-units').value = layout.units || 'mm'
  const factor = scale[layout.units || 'mm']
  $('diameter').value = layout.pipe.diameter
  $('clearance').value = layout.rules?.clearance ?? 50 / factor
  $('grid-step').value = layout.rules?.grid_step ?? 250 / factor
  $('bend-penalty').value = layout.rules?.bend_penalty ?? 300 / factor
  $('bend-radius').value = layout.rules?.bend_radius ?? ''
  $('slope').value = (layout.rules?.min_slope ?? .01) * 100
  $('profile').value = (layout.rule_profile === 'fmg-v3' ? 'challenge-' : 'prototype-') + (layout.system_type === 'gravity' ? 'gravity' : 'pressure')
  $('slope-field').hidden = layout.system_type !== 'gravity'
  unitsLabels()
  $('layout-json').value = JSON.stringify(layout, null, 2)
  inputVersion++
  clearResult()
  drawRoute(normalizedPreview(layout))
}
function clearResult() {
  $('original-metric').textContent = 'Not checked'
  $('candidate-metric').textContent = 'Not calculated'
  $('outcome').textContent = 'Ready'
  $('issues').replaceChildren()
  paragraph('Run the checks to see findings for this input.', $('issues'))
  $('exports').hidden = true
  $('run-status').textContent = 'Input ready. Results are shown in millimetres.'
}
function readForm() {
  const input = clone(layout)
  input.units = $('input-units').value
  input.system_type = $('profile').value.endsWith('-gravity') ? 'gravity' : 'pressurized'
  input.rule_profile = $('profile').value.startsWith('challenge-') ? 'fmg-v3' : 'prototype-unverified'
  input.rules ||= {}
  const read = id => {
    if ($(id).value.trim() === '') throw new Error('Enter a value for ' + $(id).previousElementSibling.textContent)
    const value = Number($(id).value)
    if (!Number.isFinite(value)) throw new Error('Enter a valid number')
    return value
  }
  input.pipe.diameter = read('diameter')
  input.rules.clearance = read('clearance')
  input.rules.grid_step = read('grid-step')
  input.rules.bend_penalty = read('bend-penalty')
  if ($('bend-radius').value !== '') input.rules.bend_radius = read('bend-radius')
  else delete input.rules.bend_radius
  if (input.system_type === 'gravity') input.rules.min_slope = read('slope') / 100
  else delete input.rules.min_slope
  return input
}
function showFindings(result) {
  const container = $('issues')
  container.replaceChildren()
  const issues = result.original.issues || []
  if (result.challenge_review) {
    paragraph('V3 challenge review. Missing evidence is not a pass.', container, 'result-heading')
    for (const key of ['original','candidate']) {
      const review = result.challenge_review[key]
      if (!review) continue
      const label = key === 'candidate' && !result.candidate ? 'rejected candidate' : key
      paragraph(`${label}: ${review.counts.pass} pass · ${review.counts.fail} fail · ${review.counts.needs_review} need review`, container)
      const details = document.createElement('details'), summary = document.createElement('summary')
      summary.textContent = 'Show rule findings'
      details.append(summary)
      list(review.findings.map(f => `${f.status} · ${f.id}: ${f.message}`), details)
      container.append(details)
    }
  }
  const context = result.normalized_layout?.cad_context
  paragraph(issues.length ? `${issues.length} geometric finding${issues.length === 1 ? '' : 's'} on the original route` : 'The original route passes the implemented geometric checks. Review rule findings separately.', container, 'result-heading')
  if (result.diagnostics?.length) {
    paragraph('What stopped the search', container, 'result-heading')
    list(result.diagnostics.map(diagnosticText), container)
  }
  if (context) {
    paragraph(geometryCoverage(result.normalized_layout), container)
    paragraph(`${result.original.bends} sampled direction changes on the original centerline. This is not a count of physical fittings.`, container)
    const sources = new Map((context.obstacle_sources || []).map(item => [item.id,item]))
    const collisions = new Map()
    for (const item of issues) if (item.obstacle) collisions.set(item.obstacle,(collisions.get(item.obstacle) || 0) + 1)
    if (context.endpoint_contacts?.length) paragraph(result.normalized_layout.protected_endpoints?.length ? 'The listed connection ends stay fixed. Contact is allowed only on those exact end sections. All obstacles still apply to the movable middle.' : 'A fixed connection point overlaps an obstacle envelope. This can be an intended pipe connection or an envelope that includes empty space. Use the connection controls to review it.', container)
    if (collisions.size) {
      const details = document.createElement('details'), summary = document.createElement('summary')
      summary.textContent = `${collisions.size} source objects overlap the checked pipe envelope`
      details.append(summary)
      list([...collisions].map(([id,count]) => `Source ${sources.get(id)?.source_handle || id} · ${count} sampled segment findings`), details)
      container.append(details)
    }
    const others = issues.filter(item => !item.obstacle)
    if (others.length) list(others.slice(0,40).map(item => item.message || item.code), container)
    paragraph('This is a local test of one inferred run. Conservative envelopes can overstate occupied space near bends and fittings. Unextracted surfaces and missing ship structure remain unchecked.', container)
  } else {
    if (issues.length) list(issues.slice(0,80).map(i => `${i.segment === undefined ? '' : 'Segment ' + (i.segment + 1) + ' · '}${diagnosticText(i)}`), container)
    if (issues.length > 80) paragraph(`Showing the first 80 findings. The result file contains all ${issues.length}.`, container)
  }
  paragraph(result.search?.reason || '', container)
  const benchmark = result.normalized_layout?.benchmark
  if (benchmark?.synthetic_mutation && result.candidate) {
    const saved = result.original.length - result.candidate.length
    const delta = result.candidate.length - benchmark.baseline.validation.length
    paragraph(`Controlled test result: ${number(saved)} mm shorter than the artificial detour. The suggestion is ${number(Math.abs(delta))} mm ${delta >= 0 ? 'longer' : 'shorter'} than the unchanged CAD baseline. This tests recovery from the added change.`, container)
  }
  if (result.candidate) paragraph(result.candidate.valid ? 'The suggested route passes the implemented geometric checks. Rule findings and review items still apply.' : 'The suggested route has unresolved findings.', container)
  if (result.candidate?.minimum_segment_slope != null) paragraph(`Smallest downhill slope is ${number(result.candidate.minimum_segment_slope * 100)}%.`, container)
  const details = document.createElement('details')
  const summary = document.createElement('summary')
  summary.textContent = 'Assumptions and remaining checks'
  details.append(summary)
  list([...(result.assumptions || []), ...(result.limitations || [])], details)
  container.append(details)
}
async function loadScenario(id) {
  if (id === 'cad-selected') return
  error()
  $('run').disabled = true
  try {layout = await request('/api/layout?id=' + encodeURIComponent(id)); routeDrawing = null; routeCandidate = null; baselineLayout = null; syncForm()}
  finally {$('run').disabled = false}
}
async function run() {
  error()
  try {
    const input = readForm()
    layout = clone(input)
    $('layout-json').value = JSON.stringify(input, null, 2)
    const version = ++inputVersion
    $('run').disabled = true
    $('run-status').textContent = 'Checking the current route and searching for a replacement…'
    const started = performance.now()
    const record = await request('/api/run', {layout: input})
    if (version !== inputVersion) return
    const {result} = record
    const metric = route => result.provenance === 'cad-derived' ? `≈ ${number(route.length)} mm` : `${number(route.length)} mm · ${route.bends} turns`
    $('original-metric').textContent = metric(result.original)
    $('candidate-metric').textContent = result.candidate ? metric(result.candidate) : 'None found'
    $('outcome').textContent = ({improved: 'Route improved', unchanged: 'Original retained', no_route: 'Needs review'})[result.status] || result.status
    $('run-status').textContent = `Finished in ${((performance.now() - started) / 1000).toFixed(2)} seconds on this computer.`
    drawRoute(record.layout, result)
    showFindings(result)
    $('exports').replaceChildren()
    link('Open report', record.exports.report, $('exports'))
    link('Save route DXF', record.exports.dxf, $('exports'))
    link('Save result JSON', record.exports.json, $('exports'))
    if (record.exports.excel) link('Excel review', record.exports.excel, $('exports'))
    $('exports').hidden = false
  } catch (e) {error(e.message); $('run-status').textContent = 'The calculation did not finish.'}
  finally {$('run').disabled = false}
}
function geometryTraces(drawing, {context = false, candidate = null} = {}) {
  const groups = new Map()
  const colors = ['#79bcee','#69d8b5','#be9ff3','#e4b774','#c0d2e3']
  const layerColors = new Map()
  const sources = candidate?.source_instances || (candidate?.source_handles || []).map(handle => ({source_handle: handle, instance_path: candidate.instance_path || []}))
  const selectedInstances = new Set(sources.map(item => JSON.stringify([item.source_handle, item.instance_path || []])))
  for (const object of drawing.objects) {
    const layer = object.layer || 'Drawing'
    if (!layerColors.has(layer)) layerColors.set(layer, colors[layerColors.size % colors.length])
    const geometry = object.geometry
    const selected = context && selectedInstances.has(JSON.stringify([object.source_handle, object.instance_path || []]))
    const key = geometry.kind + ':' + layer + ':' + selected
    if (!groups.has(key)) groups.set(key, {kind: geometry.kind, name: selected ? 'Selected source geometry' : layer, color: selected ? '#e5a95e' : layerColors.get(layer), points: [], vertices: [], faces: [], selected})
    const group = groups.get(key)
    if (geometry.kind === 'polyline') group.points.push(...geometry.points, null)
    else if (geometry.kind === 'mesh') {
      const offset = group.vertices.length
      group.vertices.push(...geometry.vertices)
      group.faces.push(...geometry.faces.map(face => face.map(i => i + offset)))
    }
  }
  const traces = []
  for (const group of groups.values()) {
    if (group.kind === 'polyline') traces.push(line(group.points, group.name + ' edges', group.color, 2, {hoverinfo: 'skip', showlegend: false}))
    else traces.push(mesh(group.vertices, group.faces, group.name, group.color, context ? (group.selected ? .4 : .26) : .78))
  }
  return traces
}
function selectedCadCandidate() {
  const [kind,index] = $('cad-axis').value.split(':')
  return kind === 'chain' ? cadNetwork?.chains?.[Number(index)] : kind === 'run' ? cad?.route_candidates?.[Number(index)] : null
}
function updatePrepareButton() {
  $('prepare-cad').disabled = !cadId || !selectedCadCandidate()
}
function cadTraces() {
  const traces = geometryTraces(cad)
  const selection = $('cad-axis').value
  if (selection) {
    const [kind,index] = selection.split(':')
    const candidate = (kind === 'chain' ? cadNetwork.chains : kind === 'run' ? cad.route_candidates : cad.axis_candidates)[Number(index)]
    traces.push(line(candidate.points, 'Selected candidate', '#ffb45e', 9))
    traces.push({...line([candidate.points[0],candidate.points.at(-1)], 'Candidate ends', '#ffcf75'), mode: 'markers', marker: {size: 5, color: '#ffcf75'}})
    const length = candidate.points.slice(1).reduce((sum,p,i) => sum + Math.hypot(...p.map((v,a) => v - candidate.points[i][a])), 0)
    $('cad-axis-info').textContent = `${number(length)} mm sampled centerline length. ${candidate.geometric_diameter ? 'Inferred diameter ' + number(candidate.geometric_diameter) + ' mm. ' : ''}${candidate.evidence || ''} Connections and flow direction are unverified.`
  } else $('cad-axis-info').textContent = 'Choose a connected axis candidate to highlight it in the drawing. These may also be fitting or fastener axes.'
  updatePrepareButton()
  return traces
}
function frameBounds(view, bounds) {
  const spans = bounds.max.map((value,index) => value - bounds.min[index])
  const maximum = Math.max(...spans, 1)
  view.scene.aspectmode = 'manual'
  view.scene.aspectratio = Object.fromEntries(['x','y','z'].map((axis,index) => [axis, Math.max(.01, spans[index] / maximum * 1.8)]))
  for (const [index,axis] of ['x','y','z'].entries()) view.scene[axis + 'axis'].range = [bounds.min[index], bounds.max[index]]
  view.scene.camera = {eye: {x: 1.6, y: 1.2, z: .8}}
}
function drawCad() {
  const view = plotLayout(cad.source_sha256 + cad.source_units)
  if (cad.bounds) frameBounds(view, cad.bounds)
  Plotly.react($('cad-plot'), cadTraces(), view, plotConfig)
}
function showCad(record) {
  cad = record.cad
  cadNetwork = null
  cadId = record.cad_id || null
  $('prepare-status').textContent = 'Select a run to prepare a test.'
  $('cad-title').textContent = cad.name
  const c = cad.counts || {}
  $('cad-summary').textContent = `${number(c.solid_instances || 0)} solid instances · ${number(cad.preview?.total_object_count || cad.objects.length)} extracted objects · ${number(cad.route_candidates?.length || 0)} possible runs. Displayed in mm. ${cad.preview?.mode === 'reduced_surfaces' ? 'Reduced surface detail for this large drawing.' : 'Supported surfaces only.'}`
  $('cad-filter').value = ''
  $('cad-axis').value = ''
  populateCandidates()
  $('cad-warnings').replaceChildren()
  paragraph(`Source coordinates are interpreted as ${cad.source_units}. Display coordinates are converted to mm.`, $('cad-warnings'))
  list(cad.warnings || [], $('cad-warnings'))
  $('cad-export').replaceChildren()
  link('Save geometry and findings', record.export, $('cad-export'))
  drawCad()
}
async function prepareCad() {
  const candidate = selectedCadCandidate()
  if (!candidate || !cadId) return
  const sourceDrawing = cad
  const sourceId = cadId
  const version = ++inputVersion
  error()
  $('prepare-cad').disabled = true
  $('prepare-status').textContent = 'Building a local test from this run and nearby extracted objects…'
  try {
    const response = await request('/api/prepare-cad-layout', {
      cad_id: sourceId, candidate_id: candidate.id, system_type: $('cad-system').value,
      clearance: Number($('cad-clearance').value), padding: Number($('cad-padding').value), reverse_flow: $('cad-reverse').checked
    })
    if (version !== inputVersion) return
    layout = response.layout
    baselineLayout = null
    routeDrawing = sourceDrawing
    routeCandidate = candidate
    $('focus-route').checked = false
    $('show-obstacles').checked = false
    tab('routing')
    syncForm()
    $('run-status').textContent = `CAD test ready. ${geometryCoverage(layout)} Click Check and improve route.`
    $('prepare-status').textContent = 'Test prepared. The route checker now includes the drawing.'
  } catch (e) {error(e.message); $('prepare-status').textContent = 'The local test could not be prepared.'}
  finally {updatePrepareButton()}
}
function populateCandidates() {
  if (!cad) return
  const previous = $('cad-axis').value
  const filter = $('cad-filter').value.toLowerCase()
  $('cad-axis').replaceChildren(option('', 'Whole drawing'))
  const candidates = [
    ...(cadNetwork?.chains || []).map((candidate,index) => ['chain:' + index, `Joined chain · ${candidate.member_ids.length} components · ${candidate.source_instances.map(s => s.source_handle).join(' + ')}`]),
    ...(cad.route_candidates || []).map((candidate,index) => ['run:' + index, (candidate.contains_inferred_bend ? 'Bent run · ' : 'Run · ') + (candidate.name || candidate.id)]),
    ...(cad.axis_candidates || []).map((candidate,index) => ['axis:' + index, 'Axis · ' + candidate.id])
  ]
  for (const [value,label] of candidates) if (label.toLowerCase().includes(filter)) $('cad-axis').append(option(value,label))
  if ([...$('cad-axis').options].some(item => item.value === previous)) $('cad-axis').value = previous
}
async function loadCad(source) {
  error()
  inputVersion++
  $('load-cad-sample').disabled = true
  $('choose-cad').disabled = true
  $('cad-units').disabled = true
  $('cad-sample').disabled = true
  $('cad-status').textContent = 'Reading the drawing locally. Large models may take a few seconds…'
  try {
    let record
    if (source === 'sample') record = await request('/api/cad-sample', {source_units: $('cad-units').value, sample_id: $('cad-sample').value})
    else {
      if (source.size > 48 * 1024 * 1024) throw new Error('Choose a DXF smaller than 48 MB')
      const content = await new Promise((resolve,reject) => {const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = reject; reader.readAsDataURL(source)})
      record = await request('/api/import-cad', {filename: source.name, content_base64: content, source_units: $('cad-units').value})
    }
    cadSource = source
    showCad(record)
    $('cad-status').textContent = 'Drawing loaded. Units are assumed. Connections remain inferred.'
    $('network-status').textContent = 'Looking for matching component ends…'
    try {
      cadNetwork = await request('/api/cad-network', {cad_id: cadId})
      populateCandidates()
      $('network-status').textContent = `${cadNetwork.chains.length} joined chains found. Matching uses a 1 mm endpoint tolerance and the same inferred diameter. Uncertain junctions stay separate.`
    } catch(e) {$('network-status').textContent = 'Single runs are available. Joined chain inspection did not finish: ' + e.message}
  } catch (e) {error(e.message); $('cad-status').textContent = 'The drawing could not be loaded.'}
  finally {$('load-cad-sample').disabled = !config.cad_sample_available; $('choose-cad').disabled = false; $('cad-units').disabled = false; $('cad-sample').disabled = false}
}
async function applyConnections() {
  error()
  const version = ++inputVersion
  $('apply-connections').disabled = true
  try {
    const input = readForm(), context = input.cad_context
    const maxStub = Number($('max-stub').value)
    const assume = $('assume-connections').checked
    const prepare = {
      cad_id: context.cad_id, candidate_id: context.candidate_id, system_type: input.system_type,
      clearance: input.rules.clearance, padding: context.padding_mm, reverse_flow: context.reverse_flow,
      rule_profile: input.rule_profile, model_context: input.model_context, engineering: input.engineering
    }
    if (Math.abs(input.rules.clearance + input.pipe.diameter / 2 - context.pipe_envelope_mm) > 0.000001) {
      const refreshed = await request('/api/prepare-cad-layout', prepare)
      if (version !== inputVersion) return
      layout = refreshed.layout
      layout.rules = input.rules
      baselineLayout = null
      syncForm()
      $('connection-status').textContent = 'Clearance changed. The contact list is now refreshed. Review the updated contacts and apply the assumption again.'
      return
    }
    const connections = {start: [], end: []}
    if (assume) for (const item of context.endpoint_contacts || []) connections[item.endpoint].push(item.obstacle_id)
    $('connection-status').textContent = 'Finding bounded original end sections that leave the listed objects…'
    const response = await request('/api/prepare-cad-layout', {
      ...prepare, endpoint_connections: connections, max_stub_length: maxStub
    })
    if (version !== inputVersion) return
    layout = response.layout
    layout.rules = input.rules
    baselineLayout = null
    syncForm()
  } catch(e) {error(e.message); $('connection-status').textContent = 'Could not apply these connection assumptions. Review the contact list and maximum end length.'}
  finally {$('apply-connections').disabled = false}
}
async function makeDetour() {
  error()
  const version = ++inputVersion
  $('make-detour').disabled = true
  $('benchmark-status').textContent = 'Checking the baseline and looking for an extra detour that fits…'
  try {
    const input = readForm()
    const response = await request('/api/make-detour', {layout: input, offset_mm: Number($('detour-offset').value)})
    if (version !== inputVersion) return
    baselineLayout = clone(input)
    layout = response.layout
    syncForm()
  } catch(e) {error(e.message); $('benchmark-status').textContent = 'No test was created. ' + e.message; $('make-detour').disabled = false}
}
function tab(view) {
  for (const id of ['routing','cad']) {$(id + '-view').hidden = id !== view; $(id + '-tab').classList.toggle('active', id === view)}
  const plot = $(view === 'routing' ? 'route-plot' : 'cad-plot')
  if (plot.data) Plotly.Plots.resize(plot)
  window.scrollTo({top: 0, behavior: 'instant'})
}
async function init() {
  config = await request('/api/config')
  for (const item of config.scenarios) $('scenario').append(option(item.id, item.title))
  for (const item of config.rule_profiles.profiles) $('profile').append(option(item.id, item.name))
  for (const fitting of config.rule_profiles.fittings) {
    const node = paragraph(`${fitting.nominal_size} · ${fitting.outer_diameter_mm} mm outer diameter · ${fitting.center_to_end_mm} mm center to end.`, $('fittings'))
    link('Manufacturer dimensions', fitting.source_url, node)
  }
  paragraph('These examples do not select or certify fittings for the route.', $('fittings'), 'hint')
  $('load-cad-sample').disabled = !config.cad_sample_available
  for (const sample of (config.cad_samples || [])) $('cad-sample').append(option(sample.id, sample.name))
  $('scenario').onchange = () => loadScenario($('scenario').value).catch(e => error(e.message))
  $('profile').onchange = () => {
    try {
      const next = readForm()
      const profile = config.rule_profiles.profiles.find(p => p.id === $('profile').value)
      next.system_type = profile.system_type
      next.rule_profile = profile.rule_profile || 'prototype-unverified'
      next.rules = clone(profile.rules)
      for (const key of ['clearance','grid_step','bend_penalty','bend_radius']) if (key in next.rules) next.rules[key] /= scale[next.units]
      layout = next
      syncForm()
    } catch (e) {error(e.message)}
  }
  for (const id of ['input-units','diameter','clearance','grid-step','bend-penalty','bend-radius','slope']) $(id).onchange = () => {
    try {inputVersion++; unitsLabels(); clearResult(); drawRoute(normalizedPreview(readForm()))} catch(e) {error(e.message)}
  }
  $('run').onclick = run
  $('test-cad').onclick = () => tab('cad')
  $('return-cad').onclick = () => tab('cad')
  $('prepare-cad').onclick = prepareCad
  $('apply-connections').onclick = applyConnections
  $('make-detour').onclick = makeDetour
  $('restore-baseline').onclick = () => {if (baselineLayout) {layout = clone(baselineLayout); baselineLayout = null; syncForm(); error()}}
  for (const id of ['focus-route','show-obstacles']) $(id).onchange = () => {if (routeDisplay) drawRoute(routeDisplay.input, routeDisplay.result)}
  $('import-layout').onclick = () => $('layout-file').click()
  $('layout-file').onchange = async () => {
    try {const input = JSON.parse(await $('layout-file').files[0].text()); normalizedPreview(input); layout = input; routeDrawing = null; routeCandidate = null; baselineLayout = null; syncForm(); error()} catch(e) {error('Could not read layout: ' + e.message)}
    $('layout-file').value = ''
  }
  $('apply-json').onclick = () => {try {const edited = JSON.parse($('layout-json').value); normalizedPreview(edited); layout = edited; syncForm(); error()} catch(e) {error(e.message)}}
  $('save-layout').onclick = () => {
    try {
      const data = readForm(), url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)], {type: 'application/json'}))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'pipe-layout.json'; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch(e) {error(e.message)}
  }
  $('routing-tab').onclick = () => tab('routing')
  $('cad-tab').onclick = () => tab('cad')
  $('load-cad-sample').onclick = () => loadCad('sample')
  $('choose-cad').onclick = () => $('cad-file').click()
  $('cad-file').onchange = () => {const source = $('cad-file').files[0]; if(source) loadCad(source); $('cad-file').value = ''}
  $('cad-units').onchange = () => {if (cadSource) loadCad(cadSource)}
  $('cad-axis').onchange = drawCad
  $('cad-filter').oninput = () => {populateCandidates(); drawCad()}
  await loadScenario(config.scenarios[0].id)
}
init().catch(e => error('The workspace could not load: ' + e.message))
