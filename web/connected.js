/* Local engineering review. All untrusted text is inserted with textContent. */
'use strict';
const el = id => document.getElementById(id);
let network = null, response = null, validation = null, sourceGeometry = null, jobId = null, generation = 0, cancelRequested = false;
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
  clear('downloads'); el('outcome').textContent = 'Network changed · calculation required';
  el('review-state').textContent = 'A new calculation is required before this network can be reviewed.';
}
async function loadNetwork(raw) {
  invalidate(); const loadingGeneration = generation;
  el('optimize').disabled = true; el('validate').disabled = true;
  el('progress').textContent = 'Inspecting network topology and engineering constraints…';
  try {
    const record = await api('inspect', {network:raw});
    if (generation !== loadingGeneration) return;
    network = record.network; validation = record.validation; sourceGeometry = record.geometry;
    el('network-json').value = JSON.stringify(network, null, 2);
    showNetwork(); showValidation(); showPlot(); showCoordinates();
    el('progress').textContent = 'Network inspected. Ready for coordinated search.';
  } finally {
    if (generation === loadingGeneration) {el('optimize').disabled = false; el('validate').disabled = false;}
  }
}
function showNetwork() {
  el('network-name').textContent = network.name || network.id;
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
      invalidate(); network = record.network; validation = record.validation; sourceGeometry=record.geometry; el('network-json').value = JSON.stringify(network,null,2); showNetwork(); showValidation(); showPlot(); showCoordinates();
    });
    separate.onclick = guard(async () => {
      const record = await api('confirm', {network,decisions:[{connection_id:connection.id,action:'separate'}]});
      invalidate(); network=record.network; validation=record.validation; sourceGeometry=record.geometry; el('network-json').value=JSON.stringify(network,null,2); showNetwork(); showValidation(); showPlot(); showCoordinates();
    });
    box.append(idsLabel, ids, coordinateLabel, coordinate, connect, separate); el('connections').append(box);
  }
}
function number(value, decimals=1) { return Number.isFinite(value) ? value.toLocaleString(undefined,{maximumFractionDigits:decimals}) : '—'; }
function routeLength(net) { return net.spans.reduce((sum,s)=>sum+s.points.slice(1).reduce((a,p,i)=>a+Math.hypot(...p.map((v,j)=>v-s.points[i][j])),0),0); }
function showMetrics() {
  clear('metrics'); if (!network) return;
  const original = response?.result?.original_network || network, proposed = response?.result?.proposed_network;
  const rows = [['Original length',`${number(routeLength(original))} mm`], ['Proposed length', proposed ? `${number(routeLength(proposed))} mm` : 'No replacement'], ['Changed spans',response?.result?.changed_span_ids?.length ?? 0],['Hard failures',validation?.hard_failures ?? '—'], ['Unverified',validation?.unverified ?? '—']];
  for (const [label,value] of rows) { const box=text('div','','metric'); box.append(text('span',label),text('strong',String(value))); el('metrics').append(box); }
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
  const traces=[], labels=el('show-labels').checked, proposed=response?.result?.proposed_network;
  const addSpans=(net,kind,color)=>{for(const span of net.spans){const name=`${kind} ${span.id} · ${span.system_id}`; const line=polyline(span.points,{color,name,metadata:{kind,span}});line.legendgroup=kind;line.showlegend=span===net.spans[0];line.name=`${kind} centerlines`;traces.push(line);if(labels){const midpoint=span.points[Math.floor(span.points.length/2)];traces.push({type:'scatter3d',mode:'text',x:[midpoint[0]],y:[midpoint[1]],z:[midpoint[2]],text:[plotText(`${span.id} · OD ${span.outer_diameter_mm} mm · R ${span.fitting_catalog?.radius_mm ?? 'UNVERIFIED'} mm`)],textposition:'top center',textfont:{size:10,color},showlegend:false,hoverinfo:'skip'});}}};
  if(el('show-original').checked)addSpans(response?.result?.original_network || network,'Original','#d88729');
  if(el('show-proposed').checked && proposed)addSpans(proposed,'Proposed','#12886d');
  if(el('show-physical').checked){
    const sets=[];
    if(el('show-original').checked)sets.push([response?.geometry?.original || sourceGeometry,'Original','#db9238']);
    if(el('show-proposed').checked && proposed)sets.push([response?.geometry?.proposed,'Proposed','#149879']);
    const notes=[];
    for(const [geometry,kind,color] of sets){
      if(!geometry){notes.push(`${kind} physical occupancy is unavailable; inspect again.`);continue;}
      notes.push(...geometry.notes);
      for(const span of geometry.spans){
        const trace=capsuleMesh(span.segments,color,`${kind} physical ${span.id} · catalog ${span.catalog_status}`);
        if(trace){trace.showlegend=span===geometry.spans[0];trace.legendgroup=`${kind} physical`;trace.name=`${kind} physical envelopes`;traces.push(trace);}
        if(labels)for(const fitting of span.fittings || [])traces.push({type:'scatter3d',mode:'text',x:[fitting.intersection[0]],y:[fitting.intersection[1]],z:[fitting.intersection[2]],text:[plotText(`${fitting.id} · ${number(fitting.angle_deg)}° · R${number(fitting.radius_mm)} mm (${fitting.catalog_status})`)],textposition:'top center',textfont:{size:9,color},showlegend:false,hoverinfo:'skip'});
      }
    }
    el('geometry-notes').textContent=['Physical mm envelopes follow conservative validation geometry; tessellated surfaces are a visual approximation.',...notes].join(' ');
  }else el('geometry-notes').textContent='Centerline view. Enable physical envelopes to display declared diameters and swept fittings.';
  const nodes=proposed?.nodes || network.nodes;
  if(el('show-physical').checked)for(const node of nodes){
    const radius=node.dimensions?.body_radius_mm;
    if(Number.isFinite(radius)&&radius>0){const trace=capsuleMesh([{start:node.position,end:node.position,radius}],'#366a8d',`${node.id} declared body envelope R${radius} mm`);if(trace){trace.opacity=.35;trace.showlegend=false;traces.push(trace);}}
  }
  for(const fixed of [true,false]){const ns=nodes.filter(n=>n.fixed === fixed);if(!ns.length)continue;traces.push({type:'scatter3d',mode:labels?'markers+text':'markers',x:ns.map(n=>n.position[0]),y:ns.map(n=>n.position[1]),z:ns.map(n=>n.position[2]),text:ns.map(n=>plotText(`${n.id} (${n.type})`)),textposition:'bottom center',textfont:{size:10},marker:{size:fixed?5:6,color:fixed?'#126ca0':'#a44bad',symbol:fixed?'diamond':'circle'},name:fixed?'Fixed connection ports':'Permitted movable components',customdata:ns,hovertemplate:'%{text}<br>%{x}, %{y}, %{z} mm<extra></extra>'});}
  if(el('show-context').checked)for(const obstacle of network.obstacles || []){const trace=obstacleTrace(obstacle);if(trace)traces.push(trace);}
  Plotly.react(el('network-plot'),traces,{margin:{l:0,r:0,t:0,b:0},paper_bgcolor:'#fff',scene:{aspectmode:'data',xaxis:{title:'Global WCS X (mm)'},yaxis:{title:'Global WCS Y (mm)'},zaxis:{title:'Global WCS Z (mm)'}},legend:{orientation:'h',x:0,y:1},uirevision:network.id},{responsive:true,displaylogo:false});
  el('network-plot').removeAllListeners?.('plotly_click');
  el('network-plot').on?.('plotly_click', event=>{const selected=event.points[0]?.customdata; if(!selected)return;clear('selection');el('selection').append(text('strong','Selected component / global coordinates'),text('pre',JSON.stringify(selected,null,2)));});
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
  const requested=network, localGeneration=generation; cancelRequested=false;
  el('optimize').disabled=true;el('cancel').hidden=false;el('progress').textContent='Running coordinated network evaluations…';
  try {
    const job=await api('jobs',{network:requested,max_evaluations:Number(el('evaluations').value)});jobId=job.id;
    if(cancelRequested)await api(`jobs/${jobId}/cancel`,{});
    while(true){
      await new Promise(resolve=>setTimeout(resolve,300));const state=await api(`jobs/${job.id}`);
      if(state.status==='failed')throw new Error(state.error || 'Calculation failed.');
      if(state.status==='cancelled'){el('progress').textContent='Calculation cancelled; source network preserved.';return;}
      if(state.status==='complete'){
        if(generation !== localGeneration){el('progress').textContent='Network changed during calculation; stale result discarded.';return;}
        response=state.response;validation=response.result.validation;el('outcome').textContent=response.result.status;el('progress').textContent=`Completed in ${number(state.elapsed_seconds,3)} s · ${response.result.search?.evaluations ?? response.result.search?.evaluated ?? 'bounded'} evaluations`;
        el('review-state').textContent=`Proposal ${response.result.proposal_id || 'unchanged result'} · drawing revision ${network.drawing.revision}. Review exact coordinates and every unresolved check.`;
        el('approve').checked=false;el('export').disabled=true;showValidation();showPlot();showCoordinates();break;
      }
    }
  } finally {jobId=null;el('optimize').disabled=false;el('cancel').hidden=true;}
}
el('load-example').onclick=guard(async()=>loadNetwork(await api(`examples/${encodeURIComponent(el('example').value)}`)));
el('network-file').onchange=guard(async()=>{const file=el('network-file').files[0];if(file){if(file.size>2*1024*1024)throw new Error('Network JSON exceeds 2 MB.');const raw=JSON.parse(await file.text());await loadNetwork(raw.network || raw.original_network || raw);}});
el('apply-json').onclick=guard(async()=>loadNetwork(JSON.parse(el('network-json').value)));
el('save-json').onclick=guard(async()=>{if(!network)throw new Error('Load a network.');const blob=new Blob([JSON.stringify(network,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='pipeforge-network.json';a.click();URL.revokeObjectURL(url);});
el('inspect-cad').onclick=guard(async()=>{const parse=id=>{const value=el(id).value.split(',').map(x=>Number(x.trim()));if(value.length!==3||!value.every(Number.isFinite)||!el(id).value.trim())throw new Error('Declare three finite global permitted bounds coordinates.');return value;};const record=await api('inspect',{cad_id:el('cad-id').value.trim(),bounds:{min:parse('cad-bounds-min'),max:parse('cad-bounds-max')}});await loadNetwork(record.network);});
el('optimize').onclick=guard(optimize);
el('cancel').onclick=guard(async()=>{cancelRequested=true;el('progress').textContent='Cancellation requested…';if(jobId)await api(`jobs/${jobId}/cancel`,{});});
el('validate').onclick=guard(async()=>{if(!network)throw new Error('Load a network first.');validation=(await api('validate',{network:response?.result?.proposed_network || network,reference:response ? network : undefined})).validation;showValidation();el('progress').textContent='Independent network validation complete.';});
el('check-filter').onchange=showValidation;
for(const id of ['show-original','show-proposed','show-context','show-labels','show-physical'])el(id).onchange=showPlot;
el('approve').onchange=()=>el('export').disabled=!(el('approve').checked && response?.review_token);
el('export').onclick=guard(async()=>{if(!response || !el('approve').checked)throw new Error('Review this calculation and explicitly approve the instruction bundle.');const record=await api('review',{network,review_token:response.review_token,proposal_id:response.result.proposal_id,approved:true});clear('downloads');for(const [name,url] of Object.entries(record.exports)){const a=text('a',name);a.href=url;a.download='';el('downloads').append(a);}el('review-state').textContent='Reviewed instruction bundle generated. CAD source entities have not been modified.';});
(async()=>{try{const record=await api('examples');for(const item of record.examples){const option=text('option',item.name);option.value=item.id;el('example').append(option);}if(record.examples.length)await loadNetwork(await api(`examples/${encodeURIComponent(record.examples[0].id)}`));}catch(e){error(e.message);}})();
