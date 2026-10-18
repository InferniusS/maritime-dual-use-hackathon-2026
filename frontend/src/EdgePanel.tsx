import React,{useEffect,useMemo,useRef,useState} from 'react';
import {Activity,ArrowDownToLine,ArrowUpFromLine,Check,CheckCircle2,ChevronRight,Clock3,FileJson,GitBranch,Link2,Play,Radio,RefreshCw,ShieldCheck,Sparkles,TriangleAlert,X} from 'lucide-react';
import './EdgePanel.css';

type Props={model:any;selected:string;onPreview:(model:any)=>void;onSource?:(model:any)=>void;onSelect:(id:string)=>void;workspaceIsProposal?:boolean};
type CoverageEntity={handle?:string;entity_type?:string;layer?:string;reason?:string};
type ExtractionCoverage={schema_version?:string;adapter?:string;scope?:string;status?:'complete'|'incomplete'|'unverified';mapped_entities?:number;unmapped_entities?:number;unresolved_ids?:string[];unresolved_entities?:CoverageEntity[];ignored_entities?:CoverageEntity[];static_metadata_ids?:string[];environment?:string;notes?:string[];source_generation?:number};
type Session={id:string;drawing_id:string;revision:number;model:any;object_map:Record<string,string[]>;source_adapter?:'autocad'|'json'|'dxf'|'procedural';extraction_coverage?:ExtractionCoverage;analysis?:any;proposals:any[];audit:any[];audit_total?:number};
type Job={id:string;source_revision:number;status:string;operation:string;result?:any;error?:string;execution?:any;expanded?:number};
const fingerprint=(value:any)=>JSON.stringify(value);
const formatValue=(value:any):string=>value===undefined||value===null?'Not supplied':typeof value==='number'?Number(value.toFixed(4)).toString():typeof value==='object'?JSON.stringify(value):String(value);
const formatEvidence=(value:any):string=>typeof value==='number'?String(Number(value.toPrecision(10))):formatValue(value);
const errorMessage=(e:unknown)=>e instanceof Error?e.message:String(e);
async function request(path:string,body?:any,method?:string){
 const response=await fetch('/api'+path,{method:method||(body===undefined?'GET':'POST'),headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 if(!response.ok){const text=await response.text();try{const json=JSON.parse(text);throw Error(typeof json.detail==='string'?json.detail:JSON.stringify(json.detail||json));}catch(e){if(e instanceof SyntaxError)throw Error(text||`Request failed (${response.status})`);throw e;}}
 return response.json();
}
function incremental(source:any,next:any){
 const delta:any={upsert_pipes:[],remove_pipe_ids:[],upsert_obstacles:[],remove_obstacle_ids:[]};
 for(const [key,upsert,remove] of [['pipes','upsert_pipes','remove_pipe_ids'],['obstacles','upsert_obstacles','remove_obstacle_ids']]){
  const old=new Map<string,any>((source?.[key]||[]).map((v:any)=>[v.id,v]));
  const current=new Map<string,any>((next?.[key]||[]).map((v:any)=>[v.id,v]));
  for(const [id,item] of current)if(fingerprint(old.get(id))!==fingerprint(item))delta[upsert].push(item);
  for(const id of old.keys())if(!current.has(id))delta[remove].push(id);
 }
 return delta;
}
function changedCount(delta:any){return Object.values(delta).reduce((n:number,v:any)=>n+(Array.isArray(v)?v.length:0),0) as number;}
function sourceMetadata(model:any){if(!model)return '';const {pipes:_pipes,obstacles:_obstacles,...rest}=model;return fingerprint(rest);}
function downloadBlob(blob:Blob,name:string){const href=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=href;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(href),1000);}
const checkStatuses=['PASS','FAIL','UNVERIFIED','NOT_APPLICABLE'];
function checkStatus(row:any){const value=String(row.status||row.status_code||'UNVERIFIED').toUpperCase().replace(/ /g,'_');return checkStatuses.includes(value)?value:'UNVERIFIED';}
function ValidationEvidence({candidate}:{candidate:any}){
 const rows=Array.isArray(candidate.validation)?candidate.validation:[];
 return <details open className="edge-validation-evidence"><summary>Independent validation · {candidate.pipe_id}<span>{checkStatuses.map(status=><b className={'edge-check-status '+status.toLowerCase()} key={status}>{rows.filter((row:any)=>checkStatus(row)===status).length} {status}</b>)}</span></summary>
  {rows.length?<div className="edge-validation-scroll"><table aria-label={'Independent validation for '+candidate.pipe_id}><thead><tr><th>Engineering check</th><th>Status</th><th>Measured</th><th>Required</th><th>Evidence</th></tr></thead><tbody>{rows.map((row:any,index:number)=>{const evidence=[...(row.clearance_evidence||[]),...(row.evidence||[])];return <tr key={row.requirement+':'+index}><td>{String(row.requirement||row.constraint||'Unnamed check').replace(/_/g,' ')}{row.object_ids?.length>0&&<small>{row.object_ids.join(' ↔ ')}</small>}</td><td><span className={'edge-check-status '+checkStatus(row).toLowerCase()}>{checkStatus(row)}</span></td><td>{formatEvidence(row.measured)}{row.units&&row.measured!==null&&row.measured!==undefined&&<small>{row.units}</small>}</td><td>{formatEvidence(row.required)}{row.units&&row.required!==null&&row.required!==undefined&&<small>{row.units}</small>}</td><td>{row.detail||'No explanatory evidence supplied.'}{row.location&&<small>Location · {formatValue(row.location)}</small>}{row.suggested_correction&&<small>{row.suggested_correction}</small>}{evidence.length>0&&<details><summary>Inspect {evidence.length} measured record{evidence.length===1?'':'s'}</summary>{evidence.map((item:any,i:number)=><div className="edge-measured-record" key={i}><strong>{item.object_ids?.join(' ↔ ')||item.type||'Constraint evidence'}</strong><span>{formatEvidence(item.measured)} / required {formatEvidence(item.required)}{item.units?' '+item.units:''}</span>{item.status&&<span className={'edge-check-status '+checkStatus(item).toLowerCase()}>{checkStatus(item)}</span>}<small>{item.method||item.explanation}</small></div>)}</details>}</td></tr>;})}</tbody></table></div>:<p className="edge-warning">The engine supplied no independent validation rows for this candidate.</p>}
 </details>;
}
function ExtractionSummary({coverage,adapter}:{coverage?:ExtractionCoverage;adapter?:string}){
 const unresolved=coverage?.unresolved_entities||[];
 const hasUnresolved=unresolved.length>0||(coverage?.unresolved_ids?.length||0)>0||(coverage?.unmapped_entities||0)>0;
 const complete=coverage?.status==='complete'&&!hasUnresolved;
 const partial=coverage?.status==='incomplete'||hasUnresolved;
 const modelOnly=(coverage?.adapter||adapter)==='procedural';
 return <section className={'edge-extraction '+(complete?'declared':'unverified')} aria-label="Edge extraction coverage"><div><h3>{modelOnly?'Engineering model scope':'CAD extraction coverage'}</h3><span>{complete?'ADAPTER REPORT: COMPLETE':partial?'INCOMPLETE · UNVERIFIED':'UNVERIFIED'}</span></div><p>{coverage?.scope?coverage.scope.replace(/_/g,' '):'Extraction completeness was not supplied.'} {complete?'The adapter reports that its declared scope was extracted. This does not establish engineering compliance.':'Implemented checks apply only to the supplied engineering model; no findings does not establish that the whole CAD drawing is clear.'}</p>{coverage?.environment&&<p>{coverage.environment}</p>}
  <div className="edge-coverage-counts">{coverage?.mapped_entities!==undefined&&<span>{coverage.mapped_entities} mapped entities</span>}{coverage?.unmapped_entities!==undefined&&<span>{coverage.unmapped_entities} unresolved / unmapped entities</span>}{coverage?.ignored_entities&&<span>{coverage.ignored_entities.length} explicitly ignored entities</span>}</div>
  {(unresolved.length>0||(coverage?.unresolved_ids?.length||0)>0)&&<details open><summary>Unresolved source geometry · {unresolved.length||coverage?.unresolved_ids?.length} record{(unresolved.length||coverage?.unresolved_ids?.length)===1?'':'s'}</summary>{unresolved.length>0?<div className="edge-unresolved-scroll"><table><thead><tr><th>CAD handle</th><th>Entity type / layer</th><th>Reason</th></tr></thead><tbody>{unresolved.slice(0,100).map((item,i)=><tr key={(item.handle||'unknown')+':'+i}><td>{item.handle||'Not supplied'}</td><td>{item.entity_type||'Unknown type'}{item.layer&&<small>{item.layer}</small>}</td><td>{item.reason||'Engineering interpretation is unresolved.'}</td></tr>)}</tbody></table>{unresolved.length>100&&<p>Showing the first 100 of {unresolved.length} unresolved entities.</p>}</div>:<p>{coverage?.unresolved_ids?.join(', ')}</p>}</details>}
  {coverage?.static_metadata_ids&&coverage.static_metadata_ids.length>0&&<p className="edge-static-coverage">Static engineering metadata · {coverage.static_metadata_ids.join(', ')}. These objects are not verified as live CAD bindings.</p>}{coverage?.notes?.map((note,i)=><p key={i}>{note}</p>)}
 </section>;
}

export function EdgePanel({model,selected,onPreview,onSource,onSelect,workspaceIsProposal=false}:Props){
 const showSource=onSource||onPreview;
 const [session,setSession]=useState<Session|null>(null),[drawingId,setDrawingId]=useState(''),[pending,setPending]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[autoSync,setAutoSync]=useState(false);
 const [health,setHealth]=useState<any>(null),[remote,setRemote]=useState(false),[endpoint,setEndpoint]=useState('http://127.0.0.1:8010'),[remoteHealth,setRemoteHealth]=useState<any>(null);
 const [workerAccessToken,setWorkerAccessToken]=useState('');
 const [job,setJob]=useState<Job|null>(null),[analysis,setAnalysis]=useState<any>(null),[analysisRevision,setAnalysisRevision]=useState<number|null>(null),[previewId,setPreviewId]=useState(''),[activeProposal,setActiveProposal]=useState('');
 const [engineer,setEngineer]=useState(''),[comment,setComment]=useState(''),[selectedOnly,setSelectedOnly]=useState(false),[resolution,setResolution]=useState(.5),[clearance,setClearance]=useState(.1),[mode,setMode]=useState('balanced');
 const [focusRecommendation,setFocusRecommendation]=useState('');
 const [liveAnalyze,setLiveAnalyze]=useState(false),[queuedAnalysis,setQueuedAnalysis]=useState<{sessionId:string;revision:number}|null>(null),[externalRevisionPending,setExternalRevisionPending]=useState(false),[cancelRequestedId,setCancelRequestedId]=useState('');
 const [dxfUnits,setDxfUnits]=useState(''),[existingSessionId,setExistingSessionId]=useState('');
 const [nativeConnection,setNativeConnection]=useState<'connecting'|'connected'|'retrying'>('connecting');
 const sessionRef=useRef<Session|null>(null),previewFingerprint=useRef(''),lastAutoAttempt=useRef(''),autoAttached=useRef(false),unmounted=useRef(false),upload=useRef<HTMLInputElement>(null),mappingUpload=useRef<HTMLInputElement>(null);
 const statusBaseline=useRef<{id:string;lastEventSeq:number}>({id:'',lastEventSeq:0});
 function storeSession(next:Session){sessionRef.current=next;const events=next.audit||[];statusBaseline.current={id:next.id,lastEventSeq:Number(events[events.length-1]?.seq||0)};setSession(next);}
 useEffect(()=>{unmounted.current=false;request('/edge/health').then(setHealth).catch(()=>setHealth({status:'unavailable'}));return()=>{unmounted.current=true;};},[]);
 useEffect(()=>{if(!drawingId&&model?.id)setDrawingId(model.id);},[model?.id,drawingId]);
 useEffect(()=>{const id=new URLSearchParams(window.location.search).get('edge_session');if(!id||autoAttached.current)return;autoAttached.current=true;setExistingSessionId(id);void guarded('Attaching CAD session',()=>attachSession(id));},[]);
 const isPreview=!!previewId&&previewFingerprint.current===fingerprint(model);
 const previewProtected=!!previewId;
 const workspacePreviewProtected=workspaceIsProposal&&(!session||fingerprint(model)!==fingerprint(session.model));
 const sourceUpdateBlocked=previewProtected||workspacePreviewProtected||externalRevisionPending;
 const cadOwnsSource=session?.source_adapter==='autocad';
 const metadataChanged=!!session&&sourceMetadata(session.model)!==sourceMetadata(model);
 const delta=useMemo(()=>incremental(session?.model,model),[session?.model,model]);
 const edits=changedCount(delta);
 const jobRunning=!!job&&['queued','running'].includes(job.status);
 const workspaceDirty=!!session&&(edits>0||metadataChanged)&&!previewProtected;
 const analysisCurrent=!!analysis&&analysisRevision===session?.revision;
 const proposals=(session?.proposals||[]).filter(p=>p.source_revision===session?.revision);
 const proposal=proposals.find(p=>p.id===activeProposal)||proposals[proposals.length-1];
 const staleProposal=!!proposal&&proposal.source_revision!==session?.revision;

 async function guarded(label:string,work:()=>Promise<void>){setPending(label);setError('');try{await work();}catch(e){if(!unmounted.current)setError(errorMessage(e));}finally{if(!unmounted.current)setPending('');}}
 async function attachSession(id=existingSessionId){
  const cleanId=id.trim();if(!cleanId)throw Error('Enter the session ID created by the CAD connector.');
  const next=await request('/edge/sessions/'+encodeURIComponent(cleanId));if(unmounted.current)return;
  storeSession(next);setExistingSessionId(next.id);setDrawingId(next.drawing_id);setJob(null);setQueuedAnalysis(null);setCancelRequestedId('');setExternalRevisionPending(false);setPreviewId('');previewFingerprint.current='';setActiveProposal('');setFocusRecommendation('');setAutoSync(false);setNativeConnection('connecting');
  if(next.analysis?.source_revision===next.revision){setAnalysis(next.analysis);setAnalysisRevision(next.revision);}else{setAnalysis(null);setAnalysisRevision(null);}
  showSource(next.model);onSelect(next.model.pipes[0]?.id||'');setNotice(`Attached to CAD session ${next.drawing_id} at revision ${next.revision}. No session or revision was created.`);
 }
 async function createSession(source:any=model,map?:Record<string,string|string[]>,name?:string,showImported=false,adapter='procedural',coordinateTransform?:any,extractionCoverage?:ExtractionCoverage){
  if(!source)throw Error('Load a drawing before starting an Edge session.');
  if(!showImported&&workspacePreviewProtected)throw Error('An unreviewed engineering preview cannot become an authoritative drawing session. Load or restore a source drawing first.');
  const next=await request('/edge/sessions',{drawing_id:name||drawingId.trim()||source.id,model:source,object_map:map||{},source_adapter:adapter,coordinate_transform:coordinateTransform,extraction_coverage:extractionCoverage});
  storeSession(next);setJob(null);setAnalysis(null);setAnalysisRevision(null);setActiveProposal('');setPreviewId('');previewFingerprint.current='';setFocusRecommendation('');
  setExternalRevisionPending(false);setQueuedAnalysis(null);setCancelRequestedId('');
  if(showImported){showSource(next.model);onSelect(next.model.pipes[0]?.id||'');}
  setExistingSessionId(next.id);setDrawingId(next.drawing_id);setNotice('Revision session created. Drawing objects remain under designer control.');
 }
 async function synchronize(){
  const current=sessionRef.current;if(!current||!model)return;
  if(current.source_adapter==='autocad')throw Error('The CAD connector owns this source revision. Edit the drawing in CAD or create a separate what-if session.');
  if(sourceUpdateBlocked)throw Error('Restore the current source drawing before synchronizing workspace edits. Unreviewed proposal previews are not CAD source updates.');
  if(sourceMetadata(current.model)!==sourceMetadata(model))throw Error('Drawing identity, bounds or metadata changed. Start a new session for this drawing.');
  const patch=incremental(current.model,model);if(!changedCount(patch)){setNotice('Source revision is already current.');return;}
  const next=await request(`/edge/sessions/${current.id}`,{base_revision:current.revision,...patch},'PATCH');
  if(sessionRef.current?.id!==current.id)return;
  storeSession(next);setAnalysis(null);setAnalysisRevision(null);setFocusRecommendation('');setNotice(`Revision ${next.revision} synchronized from ${changedCount(patch)} object edit${changedCount(patch)===1?'':'s'}.`);
  setActiveProposal('');if(liveAnalyze)setQueuedAnalysis({sessionId:next.id,revision:next.revision});
 }
 useEffect(()=>{
  if(!autoSync||!workspaceDirty||metadataChanged||pending||sourceUpdateBlocked||cadOwnsSource||!model)return;
  const signature=session?.id+':'+session?.revision+':'+fingerprint(model);if(lastAutoAttempt.current===signature)return;
  const timer=setTimeout(()=>{lastAutoAttempt.current=signature;void guarded('Synchronizing revision',synchronize);},400);return()=>clearTimeout(timer);
 },[autoSync,workspaceDirty,metadataChanged,pending,sourceUpdateBlocked,cadOwnsSource,model,session?.revision]);

 async function submitJob(operation:'analyze'|'optimize',source:Session){
  if(remote&&!endpoint.trim())throw Error('Enter a remote worker URL or select the local engine.');
  const created=await request(`/edge/sessions/${source.id}/jobs`,{revision:source.revision,operation,pipe_ids:selectedOnly?[selected]:undefined,mode,resolution,clearance,remote:remote?{enabled:true,endpoint:endpoint.trim(),timeout_s:30,...(workerAccessToken.trim()?{access_token:workerAccessToken.trim()}:{})}:undefined});
  setJob({...created,status:'queued',operation});setCancelRequestedId('');setNotice('');
 }
 async function startJob(operation:'analyze'|'optimize'){
  const source=sessionRef.current;if(!source)return;
  if(sourceUpdateBlocked)throw Error('Restore the current source drawing before starting a revision check.');
  if(workspaceDirty)throw Error('Synchronize pending drawing edits before running a revision check.');
  await submitJob(operation,source);
 }
 useEffect(()=>{
  if(!queuedAnalysis||jobRunning||pending||!liveAnalyze||sourceUpdateBlocked)return;
  const current=sessionRef.current;
  if(!current||queuedAnalysis.sessionId!==current.id||queuedAnalysis.revision!==current.revision){setQueuedAnalysis(null);return;}
  if(changedCount(incremental(current.model,model))||sourceMetadata(current.model)!==sourceMetadata(model))return;
  setQueuedAnalysis(null);void guarded('Starting live revision analysis',()=>submitJob('analyze',current));
 },[queuedAnalysis,jobRunning,pending,liveAnalyze,sourceUpdateBlocked,session?.revision,model]);
 async function refreshSession(options?:{silent?:boolean;isCurrent?:()=>boolean}){
  const current=sessionRef.current;if(!current)return;
  const latest=await request('/edge/sessions/'+current.id);if(unmounted.current||sessionRef.current?.id!==current.id||(options?.isCurrent&&!options.isCurrent()))return;
  storeSession(latest);
  if(latest.revision!==current.revision){setAnalysis(null);setAnalysisRevision(null);setActiveProposal('');setFocusRecommendation('');setQueuedAnalysis(null);setExternalRevisionPending(true);setNotice(`CAD source advanced to revision ${latest.revision}. Findings and proposals from older revisions were cleared; the viewport was preserved.`);}
  else{if(latest.analysis?.source_revision===latest.revision){setAnalysis(latest.analysis);setAnalysisRevision(latest.revision);}if(!options?.silent)setNotice(`CAD session refreshed at revision ${latest.revision}.`);}
 }
 useEffect(()=>{
  if(!cadOwnsSource||!session?.id||pending||jobRunning)return;
  const id=session.id;let cancelled=false;let timer:ReturnType<typeof setTimeout>;
  const isCurrent=()=>!cancelled&&!unmounted.current&&sessionRef.current?.id===id;
  async function pollStatus(){
   try{
    const status=await request('/edge/sessions/'+encodeURIComponent(id)+'/status');if(!isCurrent())return;
    const current=sessionRef.current;
    if(status.revision!==current?.revision||statusBaseline.current.id!==id||Number(status.last_event_seq)!==statusBaseline.current.lastEventSeq)await refreshSession({silent:true,isCurrent});
    if(isCurrent())setNativeConnection('connected');
   }catch{if(isCurrent())setNativeConnection('retrying');}
   if(isCurrent())timer=setTimeout(pollStatus,1000);
  }
  timer=setTimeout(pollStatus,1000);return()=>{cancelled=true;clearTimeout(timer);};
 },[cadOwnsSource,session?.id,pending,jobRunning]);
 async function cancelJob(){
  if(!job)return;await request('/edge/jobs/'+job.id,undefined,'DELETE');setCancelRequestedId(job.id);setNotice('Cancellation requested. Already dispatched remote CPU work may finish; its result will not be applied.');
 }
 useEffect(()=>{
  if(!jobRunning||!job?.id)return;
  const id=job.id,sessionId=sessionRef.current?.id,operation=job.operation;let cancelled=false;let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   try{
    const state=await request('/edge/jobs/'+id);if(cancelled||unmounted.current)return;
    if(['queued','running'].includes(state.status)){setJob({...state,id,operation});timer=setTimeout(poll,350);return;}
    if(state.status==='failed'){setJob({...state,id,operation});setError(state.error||'The engineering job failed.');return;}
    if(state.status==='stale'){await refreshSession();if(cancelled)return;setJob({...state,id,operation});setNotice('The CAD source changed during computation. Stale findings and proposals were cleared. Load the latest source view before rerunning.');return;}
    if(state.status!=='complete'){setJob({...state,id,operation});return;}
    const latest=await request('/edge/sessions/'+sessionId);if(cancelled||sessionRef.current?.id!==sessionId)return;
    if(latest.revision!==sessionRef.current?.revision){setExternalRevisionPending(true);setAnalysis(null);setAnalysisRevision(null);setActiveProposal('');setFocusRecommendation('');}
    storeSession(latest);
    if(state.source_revision!==latest.revision){setJob({...state,id,operation,status:'stale'});setNotice('A newer drawing revision exists. Older recommendations were discarded.');return;}
    if(operation==='analyze'){setAnalysis(state.result);setAnalysisRevision(state.source_revision);setNotice('Analysis complete for the current drawing revision. Select a finding to locate its source object.');}
    else{const produced=state.result?.proposals||[],list=latest.proposals||produced;if(!latest.proposals?.length&&list.length)storeSession({...latest,proposals:list});if(produced.length)setActiveProposal(produced[produced.length-1].id);const reasons=(state.result?.candidates||[]).filter((candidate:any)=>candidate.status!=='accepted').map((candidate:any)=>candidate.reason).filter(Boolean);setNotice(produced.length?'Proposal ready for preview and engineer review.':'No new validated route proposal. '+(reasons.join('; ')||state.result?.error||state.result?.reason||'Inspect the returned routing evidence.'));}
    setJob({...state,id,operation});
   }catch(e){if(!cancelled){setError(errorMessage(e));setJob(previous=>previous?{...previous,status:'failed',error:errorMessage(e)}:previous);}}
  }
  timer=setTimeout(poll,100);return()=>{cancelled=true;clearTimeout(timer);};
 },[job?.id,jobRunning]);

 async function importDrawing(file:File){
  const content=await file.text();let source:any,map:Record<string,string|string[]>|undefined,name:string|undefined,transform:any,coverage:ExtractionCoverage|undefined;
  if(file.name.toLowerCase().endsWith('.dxf')){
   if(!model)throw Error('DXF exchange requires a loaded engineering model for diameters, systems and constraints. Import its JSON metadata first.');
   const parsed=await request('/edge/import/dxf',{dxf:content,metadata:model,source_units:dxfUnits||undefined});source=parsed.model;map=parsed.object_map;transform=parsed.coordinate_transform;coverage=parsed.coverage||parsed.extraction_coverage;name=file.name.replace(/\.dxf$/i,'');
  }else{const parsed=JSON.parse(content);source=await request('/import/json',parsed.model||parsed);map=parsed.object_map;transform=parsed.coordinate_transform;coverage=parsed.extraction_coverage;name=parsed.drawing_id||source.id;}
  await createSession(source,map,name,true,file.name.toLowerCase().endsWith('.dxf')?'dxf':'json',transform,coverage);
 }
 async function importMapping(file:File){
  const current=sessionRef.current;if(!current)return;
  if(current.source_adapter==='autocad')throw Error('AutoCAD owns CAD handle bindings. Use PFMAP in the native connector and reconnect the drawing to update them.');
  const parsed=JSON.parse(await file.text()),map=parsed.object_map||parsed;
  if(!map||Array.isArray(map)||typeof map!=='object'||Object.entries(map).some(([key,value])=>!key||!(typeof value==='string'?value.trim():Array.isArray(value)&&value.length&&value.every(v=>typeof v==='string'&&v.trim()))))throw Error('Expected a JSON object mapping engineering IDs to a CAD handle string or an array of handle strings.');
  const next=await request(`/edge/sessions/${current.id}`,{base_revision:current.revision,object_map:map},'PATCH');storeSession(next);setAnalysis(null);setAnalysisRevision(null);setNotice('CAD object mapping updated. Run a fresh analysis for this revision.');
 }
 function showProposal(){if(!proposal?.model||staleProposal)return;previewFingerprint.current=fingerprint(proposal.model);setPreviewId(proposal.id);onPreview(proposal.model);setNotice('Proposal shown in the 3D viewport. The source session and CAD drawing are unchanged.');}
 function restoreSource(){if(!session)return;setPreviewId('');previewFingerprint.current='';setExternalRevisionPending(false);showSource(session.model);if(!session.model.pipes.some((p:any)=>p.id===selected))onSelect(session.model.pipes[0]?.id||'');setNotice('Current source drawing restored in the 3D viewport.');}
 async function decision(value:'approve'|'reject'){
  const current=sessionRef.current;if(!current||!proposal)return;
  if(!engineer.trim())throw Error('Enter the reviewing engineer’s name to record a decision.');
  if(value==='approve'&&(proposal.extraction_coverage||current.extraction_coverage)?.status==='incomplete')throw Error('CAD extraction is incomplete. Resolve unclassified source geometry and rerun analysis before approval.');
  const updated=await request(`/edge/sessions/${current.id}/proposals/${proposal.id}/decision`,{revision:current.revision,decision:value,engineer:engineer.trim(),comment:comment.trim()});
  const latest=await request('/edge/sessions/'+current.id);storeSession({...latest,proposals:(latest.proposals||[]).map((p:any)=>p.id===updated.id?updated:p)});
  setNotice(value==='approve'?'Approval recorded. Export the reviewed changes for manual CAD application.':'Rejection recorded. The source drawing remains unchanged.');
 }
 async function exportProposal(format:string){
  if(!session||!proposal)return;if((proposal.extraction_coverage||session.extraction_coverage)?.status==='incomplete')throw Error('CAD extraction is incomplete; reviewed export is unavailable.');const response=await fetch(`/api/edge/sessions/${session.id}/proposals/${proposal.id}/export/${format}`);
  if(!response.ok){let detail=await response.text();try{detail=JSON.parse(detail).detail||detail;}catch{}throw Error(detail);}
  downloadBlob(await response.blob(),`pipeforge-edge-${session.drawing_id.replace(/[^a-zA-Z0-9_-]/g,'_')}-r${proposal.source_revision}.${format==='report'?'txt':format}`);
 }
 const recommendations=analysisCurrent?(analysis.recommendations||[]):[];
 const optimizationCurrent=job?.operation==='optimize'&&job.status==='complete'&&job.source_revision===session?.revision&&!!job.result;
 const unsuccessfulCandidates=optimizationCurrent?(job?.result?.candidates||[]).filter((candidate:any)=>!['accepted','optimized'].includes(candidate.status)):[];
 const unsuccessfulSearch=optimizationCurrent&&(unsuccessfulCandidates.length>0||(!(job?.result?.proposals||[]).length&&!(job?.result?.candidates||[]).length));
 const proposalCoverage=proposal?.extraction_coverage||session?.extraction_coverage;
 const coverageBlocksReview=proposalCoverage?.status==='incomplete';
 const reviewCoverageUnverified=proposalCoverage?.status!=='complete'||(proposalCoverage?.unmapped_entities||0)>0||(proposalCoverage?.unresolved_entities?.length||0)>0||(proposalCoverage?.unresolved_ids?.length||0)>0;
 const sourceCount=(session?.model?.pipes?.length||0)+(session?.model?.obstacles?.length||0);
 return <section className="edge-panel" aria-label="PipeForge Edge CAD companion">
  <div className="edge-heading"><div className="edge-logomark"><Radio size={21}/></div><div><div className="edge-eyebrow">CAD ENGINEERING COPILOT</div><h2>PIPEFORGE <span>EDGE</span></h2><p>Live revisions. Explainable engineering. Designer control.</p></div><span className={'edge-status '+(health?.status==='unavailable'?'warning':'')}><i/>{!health?'Connecting engine':health.status==='unavailable'?'Engine unavailable':'Local companion'}</span></div>
  <div className="edge-boundary"><ShieldCheck size={15}/><span>Recommendations and reviewed exchange files. Source CAD changes require a designer’s action.</span></div>
  <div className="edge-workflow" aria-label="Edge review workflow">{[['01','Connect drawing'],['02','Check revision'],['03','Review proposal'],['04','Export changes']].map(([n,label],i)=><React.Fragment key={n}><span className={(i===0?!!session:i===1?!!analysisCurrent:i===2?!!proposal:proposal?.status==='approved')?'done':''}><b>{n}</b>{label}</span>{i<3&&<ChevronRight size={12}/>}</React.Fragment>)}</div>
  <div className="edge-connect-grid">
   <div className="edge-connection"><h3><Link2 size={14}/>Drawing connection</h3><div className="edge-attach"><label>Existing CAD session ID<input aria-label="Edge existing session ID" value={existingSessionId} maxLength={256} onChange={e=>setExistingSessionId(e.target.value)} placeholder="Paste the native connector session ID"/></label><button disabled={!!pending||jobRunning||!existingSessionId.trim()} onClick={()=>void guarded('Attaching CAD session',()=>attachSession())}><Link2 size={12}/>Attach CAD session</button></div><label>Drawing identifier<input aria-label="Edge drawing identifier" value={drawingId} onChange={e=>setDrawingId(e.target.value)} placeholder="Drawing or document ID"/></label><div className="edge-button-row"><button disabled={!model||!!pending||jobRunning||workspacePreviewProtected} onClick={()=>void guarded('Connecting drawing',()=>cadOwnsSource&&session?createSession(model,session.object_map,session.drawing_id+'-what-if'):createSession())}><GitBranch size={13}/>{cadOwnsSource?'Create what-if session':session?'New session from workspace':'Connect workspace drawing'}</button><button disabled={!!pending||jobRunning} onClick={()=>upload.current?.click()}><ArrowUpFromLine size={13}/>Import JSON / DXF</button></div><input ref={upload} type="file" accept=".json,.dxf" hidden aria-label="Edge import drawing file" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void guarded('Importing drawing',()=>importDrawing(file));}}/>
   <p className="edge-help">JSON carries engineering metadata. DXF updates mapped 3D pipe polylines against the loaded model and converts declared units to meters. Native DWG requires the external CAD adapter.</p><label className="edge-dxf-units">DXF source units<select aria-label="Edge DXF source units" value={dxfUnits} onChange={e=>setDxfUnits(e.target.value)}><option value="">Read declared file units</option>{[['m','Meters'],['mm','Millimeters'],['cm','Centimeters'],['in','Inches'],['ft','Feet']].map(([unit,label])=><option key={unit} value={unit}>{label} · explicit source units</option>)}</select></label>{workspacePreviewProtected&&<p className="edge-warning">An unreviewed engineering preview is displayed. It is not a CAD source update; live sync, manual sync and revision jobs are blocked.</p>}
   {session&&<><div className="edge-revision"><span><i/>REVISION <strong>{session.revision}</strong></span><code title={session.id}>{session.drawing_id}</code><span>{sourceCount} objects</span></div><div className="edge-button-row"><button disabled={!!pending||!edits||metadataChanged||sourceUpdateBlocked||cadOwnsSource} onClick={()=>void guarded('Synchronizing revision',synchronize)}><RefreshCw size={12}/>Sync workspace edits{edits>0&&!previewProtected?` (${edits})`:''}</button><label className="edge-checkbox"><input type="checkbox" aria-label="Edge automatically sync edits" disabled={cadOwnsSource} checked={autoSync} onChange={e=>setAutoSync(e.target.checked)}/>Live sync · 400 ms</label><label className="edge-checkbox"><input type="checkbox" aria-label="Edge live analyze after sync" disabled={cadOwnsSource} checked={liveAnalyze} onChange={e=>{setLiveAnalyze(e.target.checked);if(!e.target.checked)setQueuedAnalysis(null);}}/>Live analyze after sync</label></div><div className="edge-button-row"><button disabled={!!pending} onClick={()=>void guarded('Refreshing CAD session',refreshSession)}><RefreshCw size={12}/>Refresh CAD session</button>{(sourceUpdateBlocked||(cadOwnsSource&&workspaceDirty))&&<button disabled={!!pending} onClick={restoreSource}><RefreshCw size={12}/>{externalRevisionPending?'Load latest source view':'Restore source view'}</button>}</div>{externalRevisionPending&&<p className="edge-warning">A newer CAD source revision is available. The viewport is unchanged. Load the latest source view before continuing.</p>}{metadataChanged&&!sourceUpdateBlocked&&!cadOwnsSource&&<p className="edge-warning">Drawing metadata changed. Create a new session to preserve source identity.</p>}{workspaceDirty&&!metadataChanged&&!sourceUpdateBlocked&&!cadOwnsSource&&<p className="edge-warning">{edits} object edit{edits===1?'':'s'} awaiting revision synchronization.</p>}{cadOwnsSource&&<p className="edge-cad-source"><ShieldCheck size={13}/><span>AutoCAD owns this source drawing. Geometry synchronization from the workspace is disabled. Edit the source in CAD or create a separate what-if session.<small className={nativeConnection==='retrying'?'edge-native-retrying':''}>{pending||jobRunning?'Native synchronization resumes after the active operation.':nativeConnection==='retrying'?'Native connection interrupted · retrying every second.':nativeConnection==='connected'?'Native synchronization active · monitoring source changes.':'Connecting to native session updates…'}</small></span></p>}<details className="edge-map"><summary>CAD object identity · {Object.keys(session.object_map||{}).length} mapped handles</summary><p>Unknown CAD handles remain unmapped and unverified. Import the adapter’s explicit handle map to support one or more source entities per engineering object.</p><button disabled={!!pending||cadOwnsSource} onClick={()=>mappingUpload.current?.click()}><FileJson size={12}/>Import CAD handle map</button><input ref={mappingUpload} type="file" accept=".json" hidden aria-label="Edge import CAD handle map" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void guarded('Updating CAD mapping',()=>importMapping(file));}}/><div className="edge-handle-list">{Object.entries(session.object_map||{}).map(([id,handle])=><button key={id} onClick={()=>onSelect(id)}><span>{id}</span><code>{Array.isArray(handle)?handle.join(', '):handle}</code></button>)}</div></details></>}
   </div>
   <div className="edge-engine"><h3><Activity size={14}/>Engineering engine</h3><div className="edge-engine-toggle"><button className={!remote?'active':''} disabled={jobRunning} onClick={()=>setRemote(false)}>Local engine</button><button className={remote?'active':''} disabled={jobRunning} onClick={()=>setRemote(true)}>Remote worker</button></div>{remote&&<><label>Worker endpoint<input aria-label="Edge remote worker endpoint" type="url" value={endpoint} disabled={jobRunning} onChange={e=>{setEndpoint(e.target.value);setRemoteHealth(null);setWorkerAccessToken('');}} placeholder="https://worker.example.com"/></label><label className="edge-worker-token">Worker access token · optional<input aria-label="Edge remote worker access token" type="password" autoComplete="off" value={workerAccessToken} disabled={jobRunning} onChange={e=>{setWorkerAccessToken(e.target.value);setRemoteHealth(null);}} placeholder="Only if the worker requires a token"/></label><p className="edge-help">The token is sent only with worker requests and stays in this page’s memory.</p><div className="edge-button-row"><button disabled={!!pending||!endpoint.trim()} onClick={()=>void guarded('Checking worker',async()=>{const result=await request('/edge/remote/health',{endpoint:endpoint.trim(),timeout_s:5,...(workerAccessToken.trim()?{access_token:workerAccessToken.trim()}:{})});setRemoteHealth(result);if(result.status==='error'||result.error)throw Error(result.error||'Remote worker unavailable');})}><Radio size={12}/>Check worker</button>{remoteHealth&&<span className="edge-help">{remoteHealth.available===false?'Unavailable':remoteHealth.status||'Checked'}{remoteHealth.latency_ms!==undefined?` · ${formatValue(remoteHealth.latency_ms)} ms`:''}</span>}</div></>}
   <div className="edge-routing-options"><label>Objective<select aria-label="Edge optimization objective" value={mode} disabled={jobRunning} onChange={e=>setMode(e.target.value)}>{[['balanced','Balanced design'],['shortest','Shortest route'],['bends','Fewer bends'],['hydraulic','Hydraulic efficiency'],['modification','Minimal modification']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><label>Grid · m<input aria-label="Edge grid resolution" type="number" min="0.2" max="2" step="0.1" value={resolution} disabled={jobRunning} onChange={e=>setResolution(Number(e.target.value))}/></label><label>Clearance · m<input aria-label="Edge required clearance" type="number" min="0" max="1" step="0.05" value={clearance} disabled={jobRunning} onChange={e=>setClearance(Number(e.target.value))}/></label></div>
   <label className="edge-checkbox"><input type="checkbox" aria-label="Edge selected pipe only" checked={selectedOnly} onChange={e=>setSelectedOnly(e.target.checked)}/>Route selected pipe only{selectedOnly&&selected&&<code>{selected}</code>}</label><div className="edge-button-row edge-engine-actions"><button disabled={!session||!!pending||jobRunning||workspaceDirty||sourceUpdateBlocked||clearance<0||clearance>1} onClick={()=>void guarded('Starting revision analysis',()=>startJob('analyze'))}><Play size={13}/>Analyze revision</button><button className="edge-primary" disabled={!session||!!pending||jobRunning||workspaceDirty||sourceUpdateBlocked||resolution<.2||resolution>2||clearance<0||clearance>1||(selectedOnly&&!session?.model?.pipes?.some((p:any)=>p.id===selected))} onClick={()=>void guarded('Starting route search',()=>startJob('optimize'))}><Sparkles size={13}/>Propose route</button></div>
   {job&&<div className={'edge-job '+(job.status==='stale'||job.status==='failed'?'warning':'')} role="status"><span>{jobRunning?<span className="edge-pulse"/>:job.status==='complete'?<CheckCircle2 size={13}/>:<Clock3 size={13}/>}<strong>{job.operation==='analyze'?'Analysis':'Route search'} · {job.status}</strong><code>r{job.source_revision}</code></span>{job.operation==='optimize'&&job.expanded!==undefined&&(!jobRunning||Number(job.expanded)>0)&&<small>{Number(job.expanded).toLocaleString()} explored states</small>}{job.execution&&<><small>{job.execution.location==='remote'?'Remote worker':'Local engine'} · {job.execution.architecture||'architecture unknown'} · {job.execution.processor||'CPU'}</small><div className="edge-timings">{[['Total',job.execution.total_ms],['Compute',job.execution.location==='remote'?job.execution.remote_compute_ms:job.execution.local_compute_ms],['Round trip',job.execution.roundtrip_ms],['Local verification',job.execution.local_validation_ms]].filter(([,value])=>typeof value==='number').map(([label,value])=><span key={String(label)}>{label}<b>{formatValue(value)} ms</b></span>)}</div>{job.execution.request_bytes>0&&<small>{formatValue(job.execution.request_bytes/1024)} KB sent · {job.execution.payload_mode||'full'} payload</small>}{remote&&job.execution.local_fallback_reason&&<small>Local fallback · {job.execution.local_fallback_reason}</small>}</>}{jobRunning&&<><div className="edge-button-row"><button disabled={!!pending||cancelRequestedId===job.id} onClick={()=>void guarded('Requesting Edge job cancellation',cancelJob)}><X size={12}/>{cancelRequestedId===job.id?'Cancellation requested':'Cancel Edge job'}</button></div><small>Dispatched remote CPU work may finish after cancellation.</small></>}</div>}
   {!session&&<div className="edge-empty"><GitBranch size={20}/><p>Connect the current drawing to begin a versioned engineering session.</p></div>}
   </div>
  </div>
  {session&&<ExtractionSummary coverage={session.extraction_coverage} adapter={session.source_adapter}/>}
  {(pending||notice)&&<div className="edge-notice" role="status">{pending?<RefreshCw size={13} className="edge-spinning"/>:<CheckCircle2 size={13}/>}<span>{pending||notice}</span></div>}
  {error&&<div className="edge-error" role="alert"><TriangleAlert size={14}/><span>{error}</span><button aria-label="Dismiss Edge error" onClick={()=>setError('')}><X size={13}/></button></div>}
  {unsuccessfulSearch&&<section className="edge-unsuccessful-search" aria-label="Edge unsuccessful routing evidence"><div className="edge-section-title"><h3>Routing attempts require review</h3><span className="edge-help">Source r{job?.source_revision} · failed attempts do not create approved geometry</span></div>{unsuccessfulCandidates.map((candidate:any,index:number)=><article key={candidate.pipe_id||index}><div className="edge-attempt-title"><button onClick={()=>onSelect(candidate.pipe_id)}>{candidate.pipe_id}</button><span>{candidate.status}</span></div><p className="edge-attempt-reason">{candidate.reason||'The engine did not supply an accepted replacement route or a more specific explanation.'}</p><ValidationEvidence candidate={candidate}/></article>)}{!unsuccessfulCandidates.length&&<p className="edge-attempt-reason">{job?.result?.error||job?.result?.reason||'The engine returned no accepted route candidates. The source model is retained.'}</p>}{job?.result?.exploration?.some((attempt:any)=>attempt.reasons?.length>0)&&<details><summary>Search termination and constraint reasons</summary>{job.result.exploration.flatMap((attempt:any,index:number)=>(attempt.reasons||[]).map((reason:string,i:number)=><p key={index+':'+i}>{reason}</p>))}</details>}</section>}
  {analysisCurrent&&<div className="edge-findings"><div className="edge-section-title"><h3>Revision findings <span>{recommendations.length}</span></h3><div className="edge-cache"><span>r{analysisRevision}</span><span>{analysis.affected_pipe_ids?.length??session?.model?.pipes?.length} pipes checked</span>{analysis.cache&&<><span>{analysis.cache.reused??0} reused</span><span>{analysis.cache.recomputed??0} recomputed</span></>}</div></div>{recommendations.length===0?<p className="edge-clear"><CheckCircle2 size={15}/>No implemented-rule findings in the supplied model for this revision. Review CAD extraction coverage and unverified engineering requirements before fabrication.</p>:<div className="edge-recommendation-grid">{recommendations.map((item:any,index:number)=><button key={item.id||index} className={'edge-recommendation '+(focusRecommendation===item.id?'selected':'')} onClick={()=>{setFocusRecommendation(item.id);if(item.pipe_id)onSelect(item.pipe_id);}}><div><span className={'edge-severity '+(item.severity||'review').toLowerCase()}>{item.severity||'REVIEW'}</span><code>{item.pipe_id||item.affected_component_ids?.join(', ')||'Network'}</code><ChevronRight size={13}/></div><strong>{String(item.issue_type||'Engineering finding').replace(/_/g,' ')}</strong><p>{item.explanation}</p><small className="edge-verification">Evidence · {String(item.verification_status||'unverified').toUpperCase()}</small><div className="edge-evidence"><span>Measured <b>{formatEvidence(item.measured)}</b></span><span>Required <b>{formatEvidence(item.required)}</b></span></div>{item.location&&<small>Location · {formatValue(item.location)}</small>}<small>CAD handles · {item.cad_handles?.join(', ')||session?.object_map[item.pipe_id]?.join(', ')||'Unmapped · UNVERIFIED'}</small>{item.suggested_correction&&<p className="edge-correction">{formatValue(item.suggested_correction)}</p>}</button>)}</div>}</div>}
  {proposal&&<div className="edge-proposal"><div className="edge-section-title"><h3>Route proposal <span className={staleProposal?'edge-stale':''}>{staleProposal?'STALE':proposal.status||'pending'}</span></h3><span className="edge-help">Source r{proposal.source_revision} · {proposal.affected_component_ids?.length||proposal.candidates?.length||0} affected components</span></div>{proposals.length>1&&<label>Proposal history<select aria-label="Edge proposal history" value={proposal.id} onChange={e=>setActiveProposal(e.target.value)}>{proposals.map((p:any,i:number)=><option key={p.id} value={p.id}>Proposal {i+1} · r{p.source_revision} · {p.status}</option>)}</select></label>}
   {staleProposal&&<p className="edge-warning">The source drawing has advanced. Preview, approval and export are disabled for this revision.</p>}<div className="edge-proposal-grid"><div><div className="edge-change-chips">{proposal.affected_component_ids?.map((id:string)=><button key={id} onClick={()=>onSelect(id)}>{id}<ChevronRight size={11}/></button>)}</div>{(proposal.explanations||[]).map((item:any,i:number)=><p key={i} className="edge-explanation">{typeof item==='string'?item:item.text||formatValue(item)}</p>)}{(proposal.candidates||[]).map((candidate:any,i:number)=><div className="edge-candidate-summary" key={candidate.pipe_id||i}><strong>{candidate.pipe_id}</strong><span>{candidate.status}</span>{candidate.before?.length!==undefined&&<small>{formatValue(candidate.before.length)} → {formatValue(candidate.after?.length)} m</small>}{candidate.reason&&<p>{candidate.reason}</p>}</div>)}{(proposal.candidates||[]).map((candidate:any,index:number)=><ValidationEvidence key={candidate.pipe_id||index} candidate={candidate}/>)}{proposal.unverified?.length>0&&<p className="edge-warning">Unverified · {proposal.unverified.map(formatValue).join(' · ')}</p>}<details className="edge-geometry"><summary>Original and proposed geometry evidence</summary><div><span>ORIGINAL · m</span><pre>{JSON.stringify(proposal.original_geometry,null,2)}</pre><span>PROPOSED · m</span><pre>{JSON.stringify(proposal.proposed_geometry,null,2)}</pre></div></details><div className="edge-button-row"><button disabled={staleProposal||!!pending||!proposal.model} onClick={showProposal}><Activity size={13}/>Preview proposal in 3D</button></div>{previewProtected&&<p className="edge-preview-note">{isPreview?'Proposal preview is displayed.':'The viewport was edited after preview.'} Restore the source view before synchronizing drawing edits.</p>}</div>
   <div className="edge-review"><h4><ShieldCheck size={14}/>Engineer decision</h4><p>Approval records review and enables exchange export. Apply changes manually in the source CAD drawing.</p>{reviewCoverageUnverified&&<p className="edge-warning edge-review-scope">CAD extraction coverage remains unverified or incomplete. This decision covers the supplied model and reported checks; it does not clear unseen geometry or resolve unverified requirements.</p>}{coverageBlocksReview&&<p className="edge-warning">Approval and reviewed export are unavailable while CAD extraction is incomplete. Resolve the unclassified source geometry and rerun analysis.</p>}<label>Reviewing engineer<input aria-label="Edge reviewing engineer" value={engineer} onChange={e=>setEngineer(e.target.value)} placeholder="Name or engineering identifier"/></label><label>Review comment<textarea aria-label="Edge review comment" value={comment} onChange={e=>setComment(e.target.value)} rows={2} placeholder="Decision rationale / remaining checks"/></label><div className="edge-button-row"><button className="edge-primary" disabled={!!pending||staleProposal||!engineer.trim()||proposal.status!=='pending'||workspaceDirty||coverageBlocksReview} onClick={()=>void guarded('Recording approval',()=>decision('approve'))}><Check size={13}/>Approve proposal</button><button disabled={!!pending||staleProposal||!engineer.trim()||proposal.status!=='pending'||workspaceDirty} onClick={()=>void guarded('Recording rejection',()=>decision('reject'))}><X size={13}/>Reject</button></div>{proposal.status!=='pending'&&<p className="edge-decision">Decision: {proposal.status}{proposal.engineer?' · '+proposal.engineer:''}</p>}<div className="edge-export-label">REVIEWED CHANGE PACKAGE</div><div className="edge-export-actions">{['json','dxf','csv','report'].map(format=><button key={format} disabled={!!pending||staleProposal||proposal.status!=='approved'||workspaceDirty||coverageBlocksReview} onClick={()=>void guarded('Preparing '+format.toUpperCase()+' export',()=>exportProposal(format))}><ArrowDownToLine size={11}/>{format.toUpperCase()}</button>)}</div></div></div>
  </div>}
  {session&&<details className="edge-audit"><summary><Clock3 size={13}/>Revision & decision audit <span>{session.audit?.length||0} of {session.audit_total??session.audit?.length??0} events</span></summary>{session.audit?.slice(-8).reverse().map((event:any,i:number)=><div key={event.id||i}><span>{event.action||event.event||event.operation||'Session event'}</span><code>{event.revision!==undefined?'r'+event.revision:''}</code><small>{event.engineer||event.actor||''}</small><small>{event.timestamp||event.time||''}</small></div>)}<button className="edge-audit-download" onClick={()=>downloadBlob(new Blob([JSON.stringify({drawing_id:session.drawing_id,revision:session.revision,object_map:session.object_map,extraction_coverage:session.extraction_coverage,audit_total:session.audit_total??session.audit?.length??0,audit_returned:session.audit?.length||0,scope:'Latest audit events, up to 100',audit:session.audit},null,2)],{type:'application/json'}),'pipeforge-edge-latest-audit.json')}><ArrowDownToLine size={12}/>Download latest {session.audit?.length||0} audit events</button></details>}
 </section>;
}
