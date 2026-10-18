/* Local engineering review. All untrusted text is inserted with textContent. */
'use strict';
const el = id => document.getElementById(id);
let network = null, response = null, validation = null, originalValidation = null, sourceGeometry = null, jobId = null, generation = 0, cancelRequested = false;
let selectedComponent = null, measuring = false, measurementPoints = [], cameraPreset = null;
const systemColors = ['#087f83','#7b58b1','#b36b20','#2871ad','#b84f70','#578136','#886c4e','#4d6e7b'];
const strategyNames = {balanced:'Balanced engineering',length:'Network length',bends:'Bend count',hydraulic:'Hydraulic pressure loss',minimal_modifications:'Design modifications',feasibility:'Congested-space feasibility'};
function text(tag, content, className) { const node = document.createElement(tag); node.textContent = content ?? ''; if (className) node.className = className; return node; }
function plotText(value) { return String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'); }
function clear(id) { el(id).replaceChildren(); }
function error(message) { el('error').textContent = message; el('error').hidden = !message; }
async function api(path, payload) {
  const options = payload === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)};
  const res = await fetch(`/api/network/${path}`, options);
  const record = await res.json();
  if (!res.ok) throw new Error(record.error || record.detail || `Request failed (${res.status})`);
  return record;
}
function guard(work) { return async () => { error(''); try { await work(); } catch (e) { error(e.message); el('progress').textContent = 'Operation could not finish.'; } }; }
function invalidate() {
  generation += 1; response = null; el('approve').checked = false; el('export').disabled = true;
  validation = originalValidation; selectedComponent = null;
  clear('downloads'); el('outcome').textContent = 'Network changed · calculation required';
  delete el('outcome').dataset.status;
  el('review-state').textContent = 'A new calculation is required before this network can be reviewed.';
  clear('candidates'); clear('selection');
}
async function loadNetwork(raw) {
  invalidate(); const loadingGeneration = generation;
  el('optimize').disabled = true; el('validate').disabled = true;
  el('progress').textContent = 'Inspecting network topology and engineering constraints…';
  try {
    const record = await api('inspect', {network:raw});
    if (generation !== loadingGeneration) return;
    network = record.network; validation = record.validation; originalValidation = validation; sourceGeometry = record.geometry;
    measuring = false; measurementPoints = []; cameraPreset = null; showMeasurement();
    el('network-json').value = JSON.stringify(network, null, 2);
    showNetwork(); showScope(true); showValidation(); showPlot(); showCoordinates(); showCopilot(); showTopology();
    el('progress').textContent = 'Network inspected. Ready for coordinated search.';
  } finally {
    if (generation === loadingGeneration) {el('optimize').disabled = false; el('validate').disabled = false;}
  }
}
function showNetwork() {
  el('network-name').textContent = network.name || network.title || network.id;
  el('network-summary').textContent = `${network.nodes.length} nodes · ${network.spans.length} connected spans · ${new Set(network.spans.map(s=>s.system_id)).size} systems`;
  el('source-info').textContent = `Drawing ${network.drawing.id} · revision ${network.drawing.revision} · ${network.provenance} · global WCS mm`;
  clear('connections');
  for (const connection of network.unresolved_connections || []) {
    if (connection.status && connection.status !== 'unresolved') continue;
    const box = text('div', '', 'connection');
    box.append(text('strong', `Unresolved ${connection.id}`), text('p', connection.reason || 'Nearby endpoints require explicit engineering confirmation.'));
    const idsLabel = text('label', 'Endpoint IDs to connect (comma separated)');
    const ids = document.createElement('input'); ids.value = (connection.endpoint_ids || []).join(', '); ids.setAttribute('aria-label', `Endpoints ${connection.id}`);
    const coordinateLabel = text('label', 'Confirmed global position X, Y, Z (mm)');
    const coordinate = document.createElement('input'); coordinate.value = (network.nodes.find(n=>n.id === connection.endpoint_ids?.[0])?.position || []).join(', '); coordinate.setAttribute('aria-label', `Position ${connection.id}`);
    const connect = text('button', 'Confirm selected connection'), separate = text('button', 'Keep endpoints separate');
    connect.onclick = guard(async () => {
      const position = coordinate.value.split(',').map(x=>Number(x.trim()));
      if (position.length !== 3 || !position.every(Number.isFinite)) throw new Error('Enter three finite global coordinates.');
      const decisions = [{connection_id:connection.id, action:'connect', node_ids:ids.value.split(',').map(x=>x.trim()).filter(Boolean), position}];
      const record = await api('confirm', {network, decisions});
      invalidate(); network = record.network; validation = record.validation; originalValidation=validation; sourceGeometry=record.geometry; el('network-json').value = JSON.stringify(network,null,2); showNetwork(); showScope(true); showValidation(); showPlot(); showCoordinates(); showCopilot(); showTopology();
    });
    separate.onclick = guard(async () => {
      const record = await api('confirm', {network,decisions:[{connection_id:connection.id,action:'separate'}]});
      invalidate(); network=record.network; validation=record.validation; originalValidation=validation; sourceGeometry=record.geometry; el('network-json').value=JSON.stringify(network,null,2); showNetwork(); showScope(true); showValidation(); showPlot(); showCoordinates(); showCopilot(); showTopology();
    });
    box.append(idsLabel, ids, coordinateLabel, coordinate, connect, separate); el('connections').append(box);
  }
}
function number(value, decimals=1) { return Number.isFinite(value) ? value.toLocaleString(undefined,{maximumFractionDigits:decimals}) : '—'; }
function routeLength(net) { return net.spans.reduce((sum,s)=>sum+s.points.slice(1).reduce((a,p,i)=>a+Math.hypot(...p.map((v,j)=>v-s.points[i][j])),0),0); }
function spanLength(span) { return routeLength({spans:[span]}); }
function systemIds() { return [...new Set(network.spans.map(span=>span.system_id))].sort(); }
function systemColor(id) { return systemColors[systemIds().indexOf(id)%systemColors.length]; }
function selectedIds(kind) { return [...el(`${kind}-selection`).querySelectorAll('input:checked')].map(input=>input.value); }
function scopePayload() {
  if (el('selection-scope').value === 'systems') return {selected_system_ids:selectedIds('system')};
  if (el('selection-scope').value === 'spans') return {selected_span_ids:selectedIds('span')};
  return {};
}
function showScope(reset=false) {
  if (!network) return;
  if(reset) {
    for(const kind of ['system','span'])clear(`${kind}-selection`);
    for(const id of systemIds()) {
      const label=text('label','','scope-item'),input=document.createElement('input');input.type='checkbox';input.checked=true;input.value=id;
      input.setAttribute('aria-label',`Optimize system ${id}`);input.onchange=settingsChanged;
      label.append(input,text('span',`${id} · ${network.spans.filter(s=>s.system_id===id).length} spans`));el('system-selection').append(label);
    }
    for(const span of network.spans) {
      const label=text('label','','scope-item'),input=document.createElement('input');input.type='checkbox';input.checked=span.modifiable && !span.constraints?.preserve_existing;input.disabled=!input.checked;input.value=span.id;
      input.setAttribute('aria-label',`Optimize span ${span.id}`);input.onchange=settingsChanged;
      const description=text('span',`${span.id} · ${span.system_id}`);description.append(text('small',`${span.from_node} → ${span.to_node} · ${input.disabled?'locked':'OD '+number(span.outer_diameter_mm)+' mm'}`));
      label.append(input,description);el('span-selection').append(label);
    }
    const isolated=el('isolate-system').value;el('isolate-system').replaceChildren(text('option','All systems'));el('isolate-system').firstChild.value='all';
    for(const id of systemIds()){const option=text('option',id);option.value=id;el('isolate-system').append(option);}
    el('isolate-system').value=systemIds().includes(isolated)?isolated:'all';
  }
  const scope=el('selection-scope').value,payload=scopePayload();
  el('system-selection').hidden=scope!=='systems';el('span-selection').hidden=scope!=='spans';
  const active=network.spans.filter(s=>s.modifiable&&!s.constraints?.preserve_existing&&(!payload.selected_span_ids||payload.selected_span_ids.includes(s.id))&&(!payload.selected_system_ids||payload.selected_system_ids.includes(s.system_id)));
  el('scope-summary').textContent=`${active.length} spans may change · ${network.spans.length-active.length} preserved · all ${network.spans.length} spans independently validated.`;
}
function settingsChanged() {
  if(jobId){error('Cancel the active calculation before changing optimization settings.');return;}
  invalidate();showScope();showValidation();showCopilot();showPlot();showCoordinates();showTopology();
  el('strategy-note').textContent=el('strategy').value==='hydraulic'?'Hydraulic ranking requires sourced flow, fluid, bore and friction data. Missing inputs are UNVERIFIED; no pressure-loss value is invented.':'Normalized preferences rank independently validated candidates. Hard constraints are never traded for a lower score.';
}
function showMetrics() {
  clear('metrics'); if (!network) return;
  const original = response?.result?.original_network || network, proposed = response?.result?.proposed_network;
  const before=response?.result?.before,after=response?.result?.after;
  const delta=proposed?routeLength(proposed)-routeLength(original):0;
  const rows = [['Network length',`${number((proposed?routeLength(proposed):routeLength(original))/1000,2)} m`,proposed?`${delta<=0?'−':'+'}${number(Math.abs(delta),1)} mm vs original`:`Original ${number(routeLength(original))} mm`],['Bends',after?.direction_changes??before?.direction_changes??'—',before?`${before.direction_changes} in original`:'Calculated during search'],['Changed spans',response?.result?.changed_span_ids?.length??0,`${network.spans.length} spans in supplied context`],['Hard failures',validation?.hard_failures??'—',response?`${response.result.original_validation?.hard_failures??'—'} in original`:'Independent engineering checks'],['Unverified',validation?.unverified??'—','Missing engineering evidence']];
  for (const [label,value,note] of rows) { const box=text('div','','metric'); box.append(text('span',label),text('strong',String(value)),text('small',note)); el('metrics').append(box); }
}
function showValidation() {
  clear('verification'); const v=validation || {};
  el('verification').append(text('p',response?.result?.proposed_network ? 'Checking the proposed network against the original source.' : 'Checking the current original network.','hint'));
  for (const [key,label] of [['geometrically_feasible','Geometrically feasible'],['engineering_constraint_feasible','Engineering constraints feasible'],['manufacturability_verified','Manufacturability verified']]) {
    const geometricIds=new Set(['network.connectivity','collision.pipe_to_pipe','collision.pipe_to_duct','collision.pipe_to_structure','collision.fitting_envelopes','routing.permitted_bounds','environment.coverage']);
    const relevant=(v.checks || []).filter(check=>key==='geometrically_feasible'?geometricIds.has(check.id):key==='manufacturability_verified'?(check.id.startsWith('manufacturing.')||check.id.startsWith('fittings.')):check.severity==='hard');
    const status=v[key]===true?'PASS':relevant.some(check=>check.status==='FAIL')?'FAIL':'UNVERIFIED';
    const p=text('p'); p.append(text('span',status,`badge ${status}`),text('span',label)); el('verification').append(p);
  }
  clear('checks'); const filter=el('check-filter').value;
  for (const check of v.checks || []) {
    if(filter !== 'ALL' && check.status !== filter)continue;
    const box=text('article','','check'), heading=text('h3'); heading.append(text('span',check.status,`badge ${check.status}`),text('span',`${check.id} · ${check.severity || 'hard'}`));
    box.append(heading,text('p',check.message));
    if(check.component_ids?.length) box.append(text('p',`Components: ${check.component_ids.join(', ')}`,'hint'));
    if(check.evidence && Object.keys(check.evidence).length){const detail=document.createElement('details'); detail.append(text('summary','Measured evidence'),text('pre',JSON.stringify(check.evidence,null,2)));box.append(detail);}
    el('checks').append(box);
  }
  showMetrics();
}
function showCopilot() {
  clear('copilot');clear('candidates');if(!network)return;
  const result=response?.result,v=validation||{},failures=(v.checks||[]).filter(c=>c.status==='FAIL'),unknown=(v.checks||[]).filter(c=>c.status==='UNVERIFIED');
  el('copilot').append(text('h3',result?'Calculated network decision':'Current engineering findings'));
  el('copilot').append(text('p',result?.explanation||`${failures.length} failed checks and ${unknown.length} unverified checks in the supplied network. Optimization will preserve locked components and evaluate all supplied systems together.`));
  if(failures.length){const list=document.createElement('ul');for(const check of failures.slice(0,5))list.append(text('li',`${check.id}: ${check.message}${check.component_ids?.length?' ['+check.component_ids.join(', ')+']':''}`));el('copilot').append(list);}
  if(result){
    const changed=result.changed_span_ids||[],systems=[...new Set((result.proposed_network?.spans||[]).filter(s=>changed.includes(s.id)).map(s=>s.system_id))];
    el('copilot').append(text('h3','Coordinated changes and tradeoffs'),text('p',changed.length?`${changed.length} spans change together across ${systems.length} systems: ${changed.join(', ')}. ${network.spans.length-changed.length} spans remain untouched.`:'No replacement geometry was accepted. The original network remains intact.'));
    const before=result.before||{},after=result.after||{},selection=result.selection||{};
    el('copilot').append(text('p',`Strategy: ${strategyNames[result.strategy]||result.strategy||'Balanced engineering'}. Length ${number(before.length_mm)} → ${number(after.length_mm)} mm; bends ${number(before.direction_changes,0)} → ${number(after.direction_changes,0)}; elevation travel ${number(before.elevation_change_mm)} → ${number(after.elevation_change_mm)} mm.`));
    if(selection.selected_span_ids)el('copilot').append(text('p',`Permitted span set: ${selection.selected_span_ids.join(', ')||'none'}. The remaining network participates in collision checks.`,'hint'));
    if(before.unverified_objective_terms?.length||after.unverified_objective_terms?.length)el('copilot').append(text('p',`UNVERIFIED objective terms: ${[...new Set([...(before.unverified_objective_terms||[]),...(after.unverified_objective_terms||[])])].join(', ')}. Missing data is excluded explicitly; no verified pressure loss or catalog availability is implied.`,'evidence-note'));
    if(after.objective_terms){
      const detail=document.createElement('details'),table=document.createElement('table'),head=document.createElement('tr');
      detail.append(text('summary','Normalized objective and engineering evidence'));
      for(const label of ['Term','Original value','Proposal value','Normalization scale','Effective weight','Evidence'])head.append(text('th',label));table.append(head);
      for(const [name,term] of Object.entries(after.objective_terms)){const row=document.createElement('tr');for(const value of [name,number(before.objective_terms?.[name]?.raw,4),number(term.raw,4),number(term.normalization_scale,4),number(term.weight,4),term.status])row.append(text('td',value));table.append(row);}
      detail.append(text('p',`Objective ${number(before.objective,4)} → ${number(after.objective,4)}. ${after.objective_units||'Dimensionless source-normalized preferences'}. Modification and constructability proxies do not establish fabrication cost or installation feasibility.`,'hint'),table);
      if(after.hydraulic_evidence)detail.append(text('p',after.hydraulic_evidence.scope||'','hint'),text('pre',JSON.stringify(after.hydraulic_evidence,null,2)));
      el('copilot').append(detail);
    }
    el('copilot').append(text('p',result.search?.method||'Bounded coordinated search with independent whole-network acceptance.','hint'));
    if(result.optimization_quality)el('copilot').append(text('p',`Search conclusion: ${result.optimization_quality}.`,'hint'));
    for(const note of result.search?.limitations||[])el('copilot').append(text('p',note,'hint'));
    const attempts=result.search?.attempts||[];
    if(attempts.length){
      const table=document.createElement('table'),head=document.createElement('tr');for(const label of ['Candidate','Order / coordination','Hard failures','Length (mm)','Bends','Normalized objective','Decision'])head.append(text('th',label));table.append(head);
      for(const attempt of attempts){
        const row=document.createElement('tr'),metrics=attempt.metrics||{};if(attempt.selected)row.className='candidate-selected';
        const decision=attempt.reason||(attempt.selected?'Selected proposal':attempt.accepted?'Feasible improvement retained for comparison':'Not selected');
        for(const value of [attempt.evaluation??'—',(attempt.order||[]).join(' → ')+(attempt.reservation_mode?` · ${attempt.reservation_mode}`:''),attempt.hard_failures??'—',number(metrics.length_mm),number(metrics.direction_changes,0),number(metrics.objective??attempt.objective,4),decision])row.append(text('td',String(value)));
        table.append(row);
      }
      el('candidates').append(text('p',`${attempts.length} calculated candidate records. Lower objective ranks candidates under the selected normalized preferences; acceptance also requires hard constraints and physical fitting evidence.`,'hint'),table);
    }else el('candidates').append(text('p','No candidate evaluations were required or permitted. See blocking findings above.','hint'));
  }else el('candidates').append(text('p','Run coordinated search to compare actual evaluated alternatives.','hint'));
  if(unknown.length){el('copilot').append(text('h3','Engineering evidence still required'),text('p',unknown.slice(0,5).map(c=>`${c.id}: ${c.message}`).join(' '),'evidence-note'));}
}
function showTopology() {
  clear('topology');if(!network)return;
  const net=response?.result?.proposed_network||network,changed=new Set(response?.result?.changed_span_ids||[]),table=document.createElement('table'),head=document.createElement('tr');
  for(const label of ['Pipe span','System','Explicit graph connection','OD (mm)','Length (mm)','Proposal state','Inspect'])head.append(text('th',label));table.append(head);
  for(const span of net.spans){
    const row=document.createElement('tr');row.className='topology-row';row.dataset.component=span.id;row.dataset.selected=String(selectedComponent===span.id);
    for(const value of [span.id,span.system_id,`${span.from_node} → ${span.to_node}`,number(span.outer_diameter_mm),number(spanLength(span)),changed.has(span.id)?'CHANGED':!span.modifiable||span.constraints?.preserve_existing?'LOCKED':'UNTOUCHED'])row.append(text('td',value));
    const td=document.createElement('td'),button=text('button','Inspect','inspect-button');button.setAttribute('aria-label',`Inspect span ${span.id}`);button.onclick=()=>{inspectComponent({kind:response?.result?.proposed_network?'Proposed':'Original',span});showPlot();};td.append(button);row.append(td);table.append(row);
  }
  el('topology').append(text('p','Connectivity comes from explicit node identifiers. Nearby geometry is never silently joined. Inspect a span to see its exact WCS points and source handle.','hint'),table);
}
function inspectComponent(selected,point) {
  if(!selected)return;clear('selection');const span=selected.span,node=selected.position?selected:null;
  selectedComponent=span?.id||selected.id||null;
  el('selection').append(text('strong',`Selected ${span?'pipe span':node?'connection component':'environment object'} · ${selectedComponent||'geometry'}`));
  const fields=span?[['System',span.system_id],['CAD handle',span.cad_handle||'UNVERIFIED'],['Outer diameter',`${number(span.outer_diameter_mm)} mm`],['Centerline length',`${number(spanLength(span))} mm`],['Graph connection',`${span.from_node} → ${span.to_node}`],['State',(response?.result?.changed_span_ids||[]).includes(span.id)?'CHANGED':'UNTOUCHED']]:[['Kind',selected.type||selected.kind||'geometry'],['Source handle',selected.cad_handle||'UNVERIFIED']];
  const grid=text('div','','inspection-grid');for(const [label,value] of fields){const item=document.createElement('div');item.append(text('span',label),text('strong',value));grid.append(item);}el('selection').append(grid);
  if(point)el('selection').append(text('p',`Picked WCS point: ${point.map(v=>number(v,3)).join(', ')} mm`,'coordinate-pill'));
  el('selection').append(text('pre',JSON.stringify(span||selected,null,2)));showTopology();
}
function showMeasurement() {
  if(measurementPoints.length===2){const [a,b]=measurementPoints,delta=b.map((v,i)=>v-a[i]);el('measurement').textContent=`Point-to-point distance ${number(Math.hypot(...delta),3)} mm · ΔX ${number(delta[0],3)} · ΔY ${number(delta[1],3)} · ΔZ ${number(delta[2],3)} mm. Measured picked global coordinates; not a certified surface clearance.`;}
  else el('measurement').textContent=measuring?`Measurement active: select ${measurementPoints.length?'the second':'the first'} plotted point.`:'Measurement: choose two plotted points to calculate WCS distance.';
  el('measure-start').textContent=measuring?'Measuring… pick points':'Measure two points';
}
function polyline(points, options={}) {
  return {type:'scatter3d',mode:'lines',x:points.map(p=>p[0]),y:points.map(p=>p[1]),z:points.map(p=>p[2]),line:{width:6,color:options.color},name:plotText(options.name),customdata:points.map(()=>options.metadata),hovertemplate:`%{x} mm, %{y} mm, %{z} mm<extra>${plotText(options.name)}</extra>`,...options};
}
function obstacleTrace(obstacle) {
  const a=obstacle.min,b=obstacle.max; if(!a||!b)return null;
  const vertices=[[a[0],a[1],a[2]],[b[0],a[1],a[2]],[b[0],b[1],a[2]],[a[0],b[1],a[2]],[a[0],a[1],b[2]],[b[0],a[1],b[2]],[b[0],b[1],b[2]],[a[0],b[1],b[2]]];
  const triangles=[[0,1,2],[0,2,3],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]];
  return {type:'mesh3d',x:vertices.map(p=>p[0]),y:vertices.map(p=>p[1]),z:vertices.map(p=>p[2]),i:triangles.map(t=>t[0]),j:triangles.map(t=>t[1]),k:triangles.map(t=>t[2]),opacity:.13,color:obstacle.kind==='pipe'?'#ad69b7':'#587789',name:plotText(`${obstacle.id} · ${obstacle.kind}`),customdata:vertices.map(()=>obstacle),hovertemplate:'%{x}, %{y}, %{z} mm<extra>%{fullData.name}</extra>',showscale:false};
}
function capsuleMesh(segments,color,name){
  // Global-coordinate capsule surfaces, including hemispherical ends. Radial
  // tessellation affects appearance only; the server validates analytic volumes.
  const x=[],y=[],z=[],ii=[],jj=[],kk=[],detail=8;
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  for(const segment of segments){
    const a=segment.start,b=segment.end,r=segment.radius,delta=b.map((v,i)=>v-a[i]),length=Math.hypot(...delta);
    if(!(Number.isFinite(length)&&r>0))continue;
    const axis=length>0?delta.map(v=>v/length):[0,0,1],raw=cross(axis,Math.abs(axis[2])<.9?[0,0,1]:[0,1,0]),norm=Math.hypot(...raw),u=raw.map(v=>v/norm),v=cross(axis,u);
    const rings=[[-r,0],[-r*.70710678,r*.70710678],[0,r],[length,r],[length+r*.70710678,r*.70710678],[length+r,0]],offset=x.length;
    for(const [axial,radial] of rings)for(let j=0;j<detail;j++){
      const theta=2*Math.PI*j/detail,p=a.map((c,k)=>c+axis[k]*axial+radial*(u[k]*Math.cos(theta)+v[k]*Math.sin(theta)));x.push(p[0]);y.push(p[1]);z.push(p[2]);
    }
    for(let ring=0;ring<rings.length-1;ring++)for(let j=0;j<detail;j++){
      const a0=offset+ring*detail+j,a1=offset+ring*detail+(j+1)%detail,b0=a0+detail,b1=a1+detail;
      ii.push(a0,a0);jj.push(b0,b1);kk.push(b1,a1);
    }
  }
  return x.length?{type:'mesh3d',x,y,z,i:ii,j:jj,k:kk,color,opacity:.72,name:plotText(name),hovertemplate:'%{x}, %{y}, %{z} mm<extra>%{fullData.name}</extra>',showlegend:true,flatshading:false}:null;
}
function showPlot() {
  if(!network || !window.Plotly)return;
  const labels=el('show-labels').checked,result=response?.result,proposed=result?.proposed_network;
  const changed=new Set(result?.changed_span_ids||[]),isolated=el('isolate-system').value;
  const visible=span=>(isolated==='all'||span.system_id===isolated)&&(el('show-unchanged').checked||!proposed||changed.has(span.id));
  const visibleNodes=new Set((proposed||network).spans.filter(visible).flatMap(span=>[span.from_node,span.to_node]));
  const notes=new Set(),split=el('comparison-mode').value==='side-by-side';
  el('plot-grid').classList.toggle('split',split);el('proposal-pane').hidden=!split;el('original-caption').hidden=!split;
  el('proposal-caption').textContent=proposed?'PROPOSAL · independently validated candidate':'NO ACCEPTED REPLACEMENT · original retained';
  clear('system-legend');
  for(const id of systemIds())if(isolated==='all'||isolated===id){const key=text('span','','system-key'),swatch=text('span','','swatch');swatch.style.setProperty('--system-color',systemColor(id));key.append(swatch,text('span',id));el('system-legend').append(key);}
  function build(kinds) {
    const traces=[];
    for(const kind of kinds){
      const net=kind==='Proposed'?proposed:result?.original_network||network;
      if(!net || !el(kind==='Original'?'show-original':'show-proposed').checked)continue;
      const firstBySystem=new Set();
      for(const span of net.spans.filter(visible)){
        const color=systemColor(span.system_id),isChanged=changed.has(span.id),selected=selectedComponent===span.id;
        const name=`${kind} ${span.id} · ${span.system_id} · ${isChanged?'changed':'untouched'}`;
        const line=polyline(span.points,{color: selected?'#cf8a12':color,name,metadata:{kind,span}});
        line.line={color:selected?'#cf8a12':color,width:selected?11:kind==='Proposed'&&isChanged?8:4,dash:kind==='Original'&&proposed?'dash':'solid'};
        line.opacity=kind==='Original'&&proposed?(split?0.85:0.4):1;
        line.legendgroup=`${kind}:${span.system_id}`;line.showlegend=!firstBySystem.has(span.system_id);firstBySystem.add(span.system_id);line.name=plotText(`${kind} · ${span.system_id}`);traces.push(line);
        if(labels&&(kind==='Proposed'||!proposed||split)){const midpoint=span.points[Math.floor(span.points.length/2)];traces.push({type:'scatter3d',mode:'text',x:[midpoint[0]],y:[midpoint[1]],z:[midpoint[2]],text:[plotText(`${span.id}${kind==='Proposed'&&isChanged?' • CHANGED':''} · OD ${span.outer_diameter_mm} mm`)],textposition:'top center',textfont:{size:10,color},showlegend:false,hoverinfo:'skip'});}
      }
      if(el('show-physical').checked){
        const geometry=kind==='Proposed'?response?.geometry?.proposed:response?.geometry?.original||sourceGeometry;
        if(!geometry)notes.add(`${kind} physical occupancy unavailable; inspect the network again.`);
        else{
          for(const note of geometry.notes||[])notes.add(note);
          for(const shape of geometry.spans||[]){
            const span=net.spans.find(s=>s.id===shape.id);if(!span||!visible(span))continue;
            const color=systemColor(span.system_id),trace=capsuleMesh(shape.segments,color,`${kind} physical ${shape.id} · catalog ${shape.catalog_status}`);
            if(trace){trace.opacity=kind==='Original'&&proposed?(split?0.5:0.17):0.68;trace.showlegend=false;trace.customdata=trace.x.map(()=>({kind,span}));traces.push(trace);}
            if(labels&&selectedComponent===span.id&&(kind==='Proposed'||!proposed||split))for(const fitting of shape.fittings||[])traces.push({type:'scatter3d',mode:'text',x:[fitting.intersection[0]],y:[fitting.intersection[1]],z:[fitting.intersection[2]],text:[plotText(`${fitting.id} · ${number(fitting.angle_deg)}° · R${number(fitting.radius_mm)} mm (${fitting.catalog_status})`)],textposition:'top center',textfont:{size:9,color},showlegend:false,hoverinfo:'skip'});
          }
        }
      }
      if(el('show-warnings').checked){
        const v=kind==='Original'?result?.original_validation||originalValidation:validation,markers=new Map();
        for(const check of v?.checks||[])if(check.status==='FAIL'||check.status==='UNVERIFIED')for(const id of check.component_ids||[]){
          const span=net.spans.find(s=>s.id===id),node=net.nodes.find(n=>n.id===id),point=span&&visible(span)?span.points[Math.floor(span.points.length/2)]:node&&visibleNodes.has(node.id)?node.position:null;
          if(point&&(!markers.has(id)||check.status==='FAIL'))markers.set(id,{point,check,span,node});
        }
        for(const [id,marker] of markers){const p=marker.point;traces.push({type:'scatter3d',mode:'markers',x:[p[0]],y:[p[1]],z:[p[2]],marker:{size:7,color:marker.check.status==='FAIL'?'#ca453e':'#d5a52a',symbol:'diamond'},name:plotText(`${marker.check.status} · ${id}`),text:[plotText(`${marker.check.id}: ${marker.check.message}`)],customdata:[marker.span?{kind,span:marker.span}:marker.node],showlegend:false,hovertemplate:'%{text}<extra></extra>'});}
      }
    }
    const net=kinds.includes('Proposed')&&proposed?proposed:result?.original_network||network;
    const nodes=net.nodes.filter(node=>visibleNodes.has(node.id));
    if(el('show-physical').checked)for(const node of nodes){const radius=node.dimensions?.body_radius_mm;if(Number.isFinite(radius)&&radius>0){const trace=capsuleMesh([{start:node.position,end:node.position,radius}],'#366a8d',`${node.id} declared body R${radius} mm`);if(trace){trace.opacity=.35;trace.showlegend=false;trace.customdata=trace.x.map(()=>node);traces.push(trace);}}}
    for(const fixed of [true,false]){const ns=nodes.filter(n=>n.fixed===fixed);if(!ns.length)continue;traces.push({type:'scatter3d',mode:labels?'markers+text':'markers',x:ns.map(n=>n.position[0]),y:ns.map(n=>n.position[1]),z:ns.map(n=>n.position[2]),text:ns.map(n=>plotText(`${n.id} (${n.type})`)),textposition:'bottom center',textfont:{size:10},marker:{size:fixed?5:6,color:fixed?'#126ca0':'#a44bad',symbol:fixed?'diamond':'circle'},name:fixed?'Fixed ports':'Permitted movable components',customdata:ns,hovertemplate:'%{text}<br>%{x}, %{y}, %{z} mm<extra></extra>'});}
    if(el('show-context').checked)for(const obstacle of network.obstacles||[]){const trace=obstacleTrace(obstacle);if(trace){trace.opacity=Number(el('context-opacity').value)/100;traces.push(trace);}}
    if(measurementPoints.length){const line=polyline(measurementPoints,{color:'#d73e8b',name:'WCS measurement'});line.mode='lines+markers';line.marker={size:6,color:'#d73e8b'};line.showlegend=false;traces.push(line);}
    return traces;
  }
  function render(id,traces){
    const plot=el(id),axis=title=>({title,gridcolor:'#dfebef',zerolinecolor:'#acc0ca',backgroundcolor:'#f8fafb',showbackground:true});
    const layout={margin:{l:0,r:0,t:25,b:10},paper_bgcolor:'#fff',scene:{domain:{x:[.02,.98],y:[.08,.93]},aspectmode:'data',xaxis:axis('Global WCS X (mm)'),yaxis:axis('Global WCS Y (mm)'),zaxis:axis('Global WCS Z (mm)')},legend:{orientation:'h',x:0,y:1,font:{size:10}},uirevision:`${network.id}:${network.drawing.revision}`};
    if(cameraPreset)layout.scene.camera=cameraPreset;
    Promise.resolve(Plotly.react(plot,traces,layout,{responsive:true,displaylogo:false})).then(()=>{
      plot.removeAllListeners?.('plotly_click');
      plot.on?.('plotly_click',event=>{
        const point=event.points[0];if(!point)return;const xyz=[Number(point.x),Number(point.y),Number(point.z)];
        if(measuring&&xyz.every(Number.isFinite)){measurementPoints.push(xyz);if(measurementPoints.length===2)measuring=false;showMeasurement();showPlot();}
        if(point.customdata)inspectComponent(point.customdata,xyz);
      });
    }).catch(e=>error(`3D viewport: ${e.message}`));
  }
  render('network-plot',build(split?['Original']:['Original','Proposed']));
  if(split)render('network-plot-proposed',build(['Proposed']));
  el('geometry-notes').textContent=el('show-physical').checked?['Physical envelopes use conservative validation geometry; tessellated surfaces approximate analytic volumes.',...notes].join(' '):'Centerline view. Enable physical envelopes to display declared diameters and swept fitting occupancy.';
}
function showCoordinates() {
  clear('coordinates');if(!network)return;
  for(const [label,net] of [['Original',response?.result?.original_network || network],['Proposed',response?.result?.proposed_network]]){
    if(!net)continue;el('coordinates').append(text('h2',`${label} · WCS mm`));
    const wrap=text('div','','table-wrap'),table=document.createElement('table'),head=document.createElement('tr');
    for(const label of ['Span / CAD handle','System / endpoints','Point','X (mm)','Y (mm)','Z (mm)'])head.append(text('th',label));table.append(head);
    for(const span of net.spans)span.points.forEach((point,i)=>{const row=document.createElement('tr');for(const value of [`${span.id} / ${span.cad_handle || 'UNVERIFIED'}`,`${span.system_id} / ${span.from_node} → ${span.to_node}`,i,...point])row.append(text('td',String(value)));table.append(row);});wrap.append(table);el('coordinates').append(wrap);
  }
}
async function optimize() {
  if(!network)throw new Error('Load a network first.');
  const selection=scopePayload();
  if(selection.selected_span_ids?.length===0||selection.selected_system_ids?.length===0)throw new Error('Select at least one pipe span or piping system to optimize.');
  const budget=Number(el('evaluations').value),maxSeconds=Number(el('max-seconds').value);
  if(!Number.isInteger(budget)||budget<1||budget>64)throw new Error('Choose an integer evaluation budget from 1 to 64.');
  if(!Number.isFinite(maxSeconds)||maxSeconds<1||maxSeconds>120)throw new Error('Choose a search time limit from 1 to 120 seconds.');
  const requested=network, localGeneration=generation; cancelRequested=false;
  response=null;validation=originalValidation;el('approve').checked=false;el('export').disabled=true;clear('downloads');showValidation();showPlot();showCopilot();showCoordinates();
  el('outcome').textContent='COORDINATED SEARCH';delete el('outcome').dataset.status;
  el('review-state').textContent='Calculation in progress. Review the completed result before generating new instructions.';
  const lockedControls=[...document.querySelectorAll('#selection-scope,#strategy,#evaluations,#max-seconds,#load-example,#network-file,#apply-json,#inspect-cad,#validate,#system-selection input,#span-selection input')];
  for(const control of lockedControls){control.dataset.previousDisabled=String(control.disabled);control.disabled=true;}
  el('optimize').disabled=true;el('cancel').hidden=false;el('progress').textContent='Running coordinated network evaluations…';
  const started=performance.now();
  try {
    const job=await api('jobs',{network:requested,max_evaluations:budget,max_seconds:maxSeconds,strategy:el('strategy').value,...selection});jobId=job.id;
    if(cancelRequested)await api(`jobs/${jobId}/cancel`,{});
    while(true){
      await new Promise(resolve=>setTimeout(resolve,300));const state=await api(`jobs/${job.id}`);
      if(state.status==='failed')throw new Error(state.error || 'Calculation failed.');
      if(state.status==='cancelled'){el('outcome').textContent='CANCELLED';el('review-state').textContent='Calculation cancelled. The original network is preserved; no new review bundle is available.';el('progress').textContent='Calculation cancelled; source network preserved.';return;}
      if(state.status!=='complete')el('progress').textContent=`${cancelRequested?'Cancellation requested':'Coordinated search running'} · ${number((performance.now()-started)/1000,1)} s elapsed · bounded to ${budget} network evaluations.`;
      if(state.status==='complete'){
        if(generation !== localGeneration){el('progress').textContent='Network changed during calculation; stale result discarded.';return;}
        response=state.response;validation=response.result.validation;el('outcome').textContent=response.result.status;el('outcome').dataset.status=response.result.status;el('progress').textContent=`Completed in ${number(state.elapsed_seconds,3)} s · ${response.result.search?.evaluations ?? response.result.search?.evaluated ?? 'bounded'} evaluations`;
        el('review-state').textContent=`Proposal ${response.result.proposal_id || 'unchanged result'} · drawing revision ${network.drawing.revision}. Review exact coordinates and every unresolved check.`;
        el('approve').checked=false;el('export').disabled=true;showValidation();showPlot();showCoordinates();showCopilot();showTopology();break;
      }
    }
  } catch(e) {el('outcome').textContent='CALCULATION FAILED';throw e;}
  finally {jobId=null;el('optimize').disabled=false;el('cancel').hidden=true;for(const control of lockedControls)control.disabled=control.dataset.previousDisabled==='true';}
}
el('load-example').onclick=guard(async()=>loadNetwork(await api(`examples/${encodeURIComponent(el('example').value)}`)));
el('network-file').onchange=guard(async()=>{const file=el('network-file').files[0];if(file){if(file.size>2*1024*1024)throw new Error('Network JSON exceeds 2 MB.');let raw;try{raw=JSON.parse(await file.text());}catch{throw new Error('This file is not valid JSON. Export a PipeForge engineering network JSON and try again.');}await loadNetwork(raw.network || raw.original_network || raw);}});
el('apply-json').onclick=guard(async()=>loadNetwork(JSON.parse(el('network-json').value)));
el('save-json').onclick=guard(async()=>{if(!network)throw new Error('Load a network.');const blob=new Blob([JSON.stringify(network,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='pipeforge-network.json';a.click();URL.revokeObjectURL(url);});
el('inspect-cad').onclick=guard(async()=>{const parse=id=>{const value=el(id).value.split(',').map(x=>Number(x.trim()));if(value.length!==3||!value.every(Number.isFinite)||!el(id).value.trim())throw new Error('Declare three finite global permitted bounds coordinates.');return value;};const record=await api('inspect',{cad_id:el('cad-id').value.trim(),bounds:{min:parse('cad-bounds-min'),max:parse('cad-bounds-max')}});await loadNetwork(record.network);});
el('optimize').onclick=guard(optimize);
el('cancel').onclick=guard(async()=>{cancelRequested=true;el('progress').textContent='Cancellation requested…';if(jobId)await api(`jobs/${jobId}/cancel`,{});});
el('validate').onclick=guard(async()=>{if(!network)throw new Error('Load a network first.');validation=(await api('validate',{network:response?.result?.proposed_network || network,reference:response ? network : undefined})).validation;showValidation();el('progress').textContent='Independent network validation complete.';});
el('check-filter').onchange=showValidation;
for(const id of ['show-original','show-proposed','show-context','show-labels','show-physical','show-unchanged','show-warnings','comparison-mode','isolate-system'])el(id).onchange=showPlot;
for(const id of ['selection-scope','strategy','evaluations','max-seconds'])el(id).onchange=settingsChanged;
el('context-opacity').oninput=()=>{el('opacity-value').textContent=`${el('context-opacity').value}%`;showPlot();};
const cameras={iso:{eye:{x:1.5,y:1.5,z:1.15},up:{x:0,y:0,z:1}},top:{eye:{x:0,y:0,z:2.2},up:{x:0,y:1,z:0},projection:{type:'orthographic'}},front:{eye:{x:0,y:-2.2,z:0},up:{x:0,y:0,z:1},projection:{type:'orthographic'}},side:{eye:{x:2.2,y:0,z:0},up:{x:0,y:0,z:1},projection:{type:'orthographic'}}};
for(const button of document.querySelectorAll('[data-camera]'))button.onclick=()=>{cameraPreset=cameras[button.dataset.camera];for(const id of ['network-plot','network-plot-proposed'])if(el(id).data)Plotly.relayout(el(id),{'scene.camera':cameraPreset});};
el('measure-start').onclick=()=>{measuring=true;measurementPoints=[];showMeasurement();showPlot();};
el('measure-clear').onclick=()=>{measuring=false;measurementPoints=[];showMeasurement();showPlot();};
el('approve').onchange=()=>el('export').disabled=!(el('approve').checked && response?.review_token);
el('export').onclick=guard(async()=>{if(!response || !el('approve').checked)throw new Error('Review this calculation and explicitly approve the instruction bundle.');const record=await api('review',{network,review_token:response.review_token,proposal_id:response.result.proposal_id,approved:true});clear('downloads');for(const [name,url] of Object.entries(record.exports)){const a=text('a',name);a.href=url;a.download='';el('downloads').append(a);}el('review-state').textContent='Reviewed instruction bundle generated. CAD source entities have not been modified.';});
(async()=>{try{const record=await api('examples');for(const item of record.examples){const option=text('option',item.name);option.value=item.id;el('example').append(option);}if(record.examples.length)await loadNetwork(await api(`examples/${encodeURIComponent(record.examples[0].id)}`));}catch(e){error(e.message);}})();
