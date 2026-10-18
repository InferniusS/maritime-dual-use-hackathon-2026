"""Revisioned CAD sessions with conservative incremental dependencies and durable audit.

CAD source edits are explicit inputs. Optimization/approval never writes a source drawing.
SQLite transactions serialize revisions, proposal decisions, and session-only application.
"""
import copy,hashlib,json,os,sqlite3,threading,uuid,time
from datetime import datetime,timezone
from pathlib import Path
from .models import Model
from .validation import analyze_pipe
from .exports import change_report
from .cad_exchange import CoordinateTransform
from .extraction import checked_coverage,coverage_validation
from .fitting_geometry import conservative_pipe_margin
from .work_limits import checkpoint,check_result,check_issue_count

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
class RevisionConflict(ValueError):pass
class NotFound(KeyError):pass
class ApprovalError(ValueError):pass

def _bounds(obj,clearance=0.):
    if hasattr(obj,'points'):
        margin=conservative_pipe_margin(obj,clearance)
        return tuple(min(p[j] for p in obj.points)-margin for j in range(3)),tuple(max(p[j] for p in obj.points)+margin for j in range(3))
    return obj.min,obj.max

def _intersect(a,b):return all(a[0][j]<=b[1][j]+1e-8 and a[1][j]>=b[0][j]-1e-8 for j in range(3))

def affected_pipes(old,new,clearance=1.):
    """Conservative source-route/direct-reference dependency bounds at max API clearance.

Both old and new object envelopes are considered, including additions/removals.
A pipe's whole route AABB also encloses its direct-connector reference.
"""
    before={p.id:p for p in [*old.pipes,*old.obstacles]};after={p.id:p for p in [*new.pipes,*new.obstacles]}
    changed={id for id in before.keys()|after.keys() if before.get(id)!=after.get(id)}
    boxes=[_bounds(obj,clearance) for id in changed for obj in (before.get(id),after.get(id)) if obj is not None]
    return sorted(p.id for p in new.pipes if p.id in changed or any(_intersect(_bounds(p,clearance),box) for box in boxes))

def normalize_map(updates,ids,base=None):
    result={id:list(handles) for id,handles in (base or {}).items() if id in ids}
    for id,handles in updates.items():
        if id not in ids:raise ValueError(f'CAD mapping references unknown engineering object {id}')
        if isinstance(handles,str):handles=[handles]
        if not isinstance(handles,list) or any(not isinstance(h,str) or not h.strip() or len(h)>128 for h in handles):raise ValueError('CAD handles must be nonempty strings')
        if len(handles)>100:raise ValueError('Too many CAD handles for one object')
        handles=list(dict.fromkeys(h.strip() for h in handles))
        if handles:result[id]=handles
        else:result.pop(id,None)
    seen={}
    for id,handles in result.items():
        for h in handles:
            if h.casefold() in seen and seen[h.casefold()]!=id:raise ValueError('One CAD handle cannot refer to different engineering objects')
            seen[h.casefold()]=id
    return result

class SessionStore:
    def __init__(self,path=None):
        self.path=Path(path) if path is not None else Path(os.environ.get('PIPEFORGE_DATA_DIR',str(Path.cwd()/'.pipeforge-state')))/'edge.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(str(self.path),check_same_thread=False,timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,revision INTEGER NOT NULL,event TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS audit_session_seq ON audit(session_id,seq)')
        self.db.execute('CREATE TABLE IF NOT EXISTS engineering_migrations(session_id TEXT PRIMARY KEY,source_snapshot TEXT NOT NULL)')
        self.db.commit()
        self._upgrade_legacy_assurance()
    def _upgrade_legacy_assurance(self):
        # Numeric-only 3.1 dumps cannot reveal which dimensions/K came from defaults.
        # Archive the exact snapshot, keep its geometry and history, and qualify it.
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                for id,raw in self.db.execute('SELECT id,data FROM sessions').fetchall():
                    state=json.loads(raw)
                    if state.get('engineering_version')=='3.2':continue
                    self.db.execute('INSERT OR IGNORE INTO engineering_migrations(session_id,source_snapshot) VALUES(?,?)',(id,raw))
                    for pipe in state['model']['pipes']:
                        pipe.setdefault('metadata_missing',['nominal_diameter','outer_diameter','inner_diameter'])
                        for fitting in pipe.get('fittings',[]):fitting.setdefault('metadata_missing',['angle','radius','k'])
                    state['engineering_version']='3.2';state['revision']+=1;state['_cache']={};state['analysis']=None
                    state['model_digest']=digest(state['model'])
                    state['extraction_coverage']=checked_coverage(state.get('extraction_coverage'),state.get('source_adapter','json'))
                    for proposal in state.get('proposals',[]):
                        if proposal['status'] in ('pending','approved'):proposal['status']='stale'
                    self._write(state)
                    self._event(state,'engineering_assurance_upgraded',source_geometry_modified=False,previous_snapshot_preserved=True,detail='Re-establish dimension/fitting provenance and reconnect native sessions before review')
                self.db.commit()
            except Exception:self.db.rollback();raise
    def _load(self,id):
        row=self.db.execute('SELECT data FROM sessions WHERE id=?',(id,)).fetchone()
        if row is None:raise NotFound(id)
        state=json.loads(row[0])
        state.setdefault('extraction_coverage',checked_coverage(adapter=state.get('source_adapter','json')))
        return state
    def _write(self,state):self.db.execute('UPDATE sessions SET data=? WHERE id=?',(json.dumps(state,allow_nan=False),state['id']))
    def _event(self,state,kind,**data):
        event=dict(event=kind,revision=state['revision'],time=datetime.now(timezone.utc).isoformat(),**data)
        self.db.execute('INSERT INTO audit(session_id,revision,event) VALUES(?,?,?)',(state['id'],state['revision'],json.dumps(event,allow_nan=False)))
    def _mutate(self,id,expected,callback):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                checkpoint('after source write-lock acquisition')
                state=self._load(id)
                if expected is not None and expected!=state['revision']:raise RevisionConflict(f'Source revision {expected} is stale; current revision is {state["revision"]}')
                result=callback(state);checkpoint('before source publication');self._write(state);checkpoint('before source commit');self.db.commit()
                return copy.deepcopy(result if result is not None else state)
            except Exception:self.db.rollback();raise
    def capture(self,id):
        with self.lock:return copy.deepcopy(self._load(id))
    def audit(self,id,offset=0,limit=100):
        with self.lock:
            self._load(id)
            return [dict(seq=row[0],**json.loads(row[1])) for row in self.db.execute('SELECT seq,event FROM audit WHERE session_id=? ORDER BY seq LIMIT ? OFFSET ?',(id,limit,offset))]
    def get(self,id):
        with self.lock:
            self.db.execute('BEGIN')
            try:
                state=self._load(id);state.pop('_cache',None)
                count=self.db.execute('SELECT COUNT(*) FROM audit WHERE session_id=?',(id,)).fetchone()[0]
                state['audit']=self.audit(id,max(0,count-100));state['audit_total']=count
                self.db.commit()
            except Exception:self.db.rollback();raise
        return state
    def status(self,id):
        # One SQLite statement keeps revision and event identity in the same snapshot.
        with self.lock:
            row=self.db.execute('SELECT data,(SELECT MAX(seq) FROM audit WHERE session_id=sessions.id) FROM sessions WHERE id=?',(id,)).fetchone()
        if row is None:raise NotFound(id)
        state=json.loads(row[0])
        return dict(id=state['id'],drawing_id=state['drawing_id'],revision=state['revision'],model_digest=state['model_digest'],last_event_seq=row[1] or 0,analysis_revision=(state.get('analysis') or {}).get('source_revision'))
    def create(self,drawing_id,model,object_map=None,source_adapter='json',coordinate_transform=None,extraction_coverage=None):
        model=Model.model_validate(model.model_dump() if isinstance(model,Model) else model)
        transform=CoordinateTransform.model_validate(coordinate_transform or {}).checked()
        if not drawing_id.strip():raise ValueError('A drawing ID is required')
        ids={o.id for o in [*model.pipes,*model.obstacles]}
        state=dict(id=uuid.uuid4().hex,drawing_id=drawing_id,revision=1,source_adapter=source_adapter,coordinate_transform=transform,extraction_coverage=checked_coverage(extraction_coverage,source_adapter),model=model.model_dump(mode='json'),object_map=normalize_map(object_map or {},ids),analysis=None,proposals=[],_cache={},affected_pipe_ids=[p.id for p in model.pipes],model_digest=digest(model.model_dump(mode='json')))
        state['engineering_version']='3.2'
        with self.lock,self.db:
            self.db.execute('INSERT INTO sessions(id,data) VALUES(?,?)',(state['id'],json.dumps(state,allow_nan=False)))
            self._event(state,'drawing_connected',source_adapter=source_adapter,model_digest=state['model_digest'])
        return self.get(state['id'])
    def patch(self,id,base_revision,upsert_pipes=None,remove_pipe_ids=None,upsert_obstacles=None,remove_obstacle_ids=None,object_map=None,extraction_coverage=None):
        def update(state):
            old=Model.model_validate(state['model']);raw=copy.deepcopy(state['model'])
            for key,upserts,removals in [('pipes',upsert_pipes or [],remove_pipe_ids or []),('obstacles',upsert_obstacles or [],remove_obstacle_ids or [])]:
                upserts=[o.model_dump(mode='json') if hasattr(o,'model_dump') else o for o in upserts]
                up_ids=[o['id'] for o in upserts]
                if len(set(up_ids))!=len(up_ids) or len(set(removals))!=len(removals) or set(up_ids)&set(removals):raise ValueError('Conflicting or duplicate delta object IDs')
                known={o['id'] for o in raw[key]}
                if set(removals)-known:raise ValueError('Cannot remove an unknown object')
                raw[key]=[o for o in raw[key] if o['id'] not in set(removals)|set(up_ids)]+upserts
                # Stable source ordering makes no-op patches and cache identity predictable.
                previous_order={o.id:i for i,o in enumerate(getattr(old,key))}
                raw[key].sort(key=lambda o:(previous_order.get(o['id'],len(previous_order)),o['id']))
            new=Model.model_validate(raw);ids={o.id for o in [*new.pipes,*new.obstacles]}
            mapping=normalize_map(object_map or {},ids,state['object_map'])
            coverage=checked_coverage(extraction_coverage,state['source_adapter']) if extraction_coverage is not None else state['extraction_coverage']
            if coverage['source_generation']<state['extraction_coverage']['source_generation']:raise ValueError('Source coverage generation cannot move backwards')
            if new==old and mapping==state['object_map'] and coverage==state['extraction_coverage']:return
            affected=affected_pipes(old,new)
            state['revision']+=1;state['model']=new.model_dump(mode='json');state['object_map']=mapping;state['analysis']=None;state['extraction_coverage']=coverage
            state['model_digest']=digest(state['model']);state['affected_pipe_ids']=affected
            state['_cache']={id:item for id,item in state['_cache'].items() if id in ids and id not in affected}
            for proposal in state['proposals']:
                if proposal['source_revision']!=state['revision'] and proposal['status'] not in ('applied','rejected'):proposal['status']='stale'
            self._event(state,'geometry_updated',affected_pipe_ids=affected,model_digest=state['model_digest'])
        self._mutate(id,base_revision,update);return self.get(id)
    def incremental_analysis(self,capture,clearance=.1):
        model=Model.model_validate(capture['model']);records={};reused=[];computed=[];cache=capture.get('_cache',{})
        issue_count=0
        for pipe in model.pipes:
            checkpoint('incremental analysis')
            cached=cache.get(pipe.id)
            if cached and cached['clearance']==clearance:records[pipe.id]=cached['record'];reused.append(pipe.id)
            else:records[pipe.id]=analyze_pipe(model,pipe,clearance);computed.append(pipe.id)
            issue_count+=len(records[pipe.id]['issues']);check_issue_count(issue_count)
        all_issues=[i for p in model.pipes for i in records[p.id]['issues']]
        result=dict(issues=[i for i in all_issues if i['type']!='inefficiency']+[i for i in all_issues if i['type']=='inefficiency'],metrics={p.id:records[p.id]['metrics'] for p in model.pipes},affected_pipe_ids=computed,cache=dict(reused=len(reused),recomputed=len(computed),reused_pipe_ids=reused,recomputed_pipe_ids=computed))
        new_cache={id:dict(clearance=clearance,record=record) for id,record in records.items()}
        return result,new_cache
    def publish_analysis(self,id,revision,result,cache):
        def update(state):
            state['analysis']={**result,'source_revision':revision};state['_cache']=cache;state['affected_pipe_ids']=[]
            self._event(state,'analysis_completed',reused=result.get('cache',{}).get('reused',0),recomputed=result.get('cache',{}).get('recomputed',0))
        self._mutate(id,revision,update)
    def publish_proposals(self,id,revision,result,clearance=.1,validate_output=None):
        def update(state):
            source=Model.model_validate(state['model']);created=[]
            choices=result.get('alternatives') or [result]
            for choice in choices[:20]:
                if not choice.get('candidates') or not all(c['status']=='accepted' for c in choice['candidates']):continue
                target=Model.model_validate(choice['model']);report=change_report(source,target,clearance)
                affected=[p.id for p in target.pipes if p!=next(o for o in source.pipes if o.id==p.id)]
                scope=list(dict.fromkeys([*affected,*[c['pipe_id'] for c in choice['candidates']]]))
                choice=copy.deepcopy(choice)
                coverage=state['extraction_coverage'];coverage_row=coverage_validation(coverage)
                for candidate in choice['candidates']:
                    candidate['validation']=[*candidate['validation'],copy.deepcopy(coverage_row)]
                    candidate.setdefault('assurance',{})['cad_scope_verified']=coverage['status']=='complete'
                    if coverage['status']!='complete':candidate['assurance']['geometrically_valid']=False
                proposal=dict(id=uuid.uuid4().hex,source_revision=revision,drawing_id=state['drawing_id'],source_digest=state['model_digest'],status='pending',model=target.model_dump(mode='json'),source_model=copy.deepcopy(state['model']),affected_component_ids=scope,modified_component_ids=affected,object_map=copy.deepcopy(state['object_map']),coordinate_transform=copy.deepcopy(state.get('coordinate_transform',{'source_units':'m','meters_per_unit':1.})),original_geometry={p.id:p.points for p in source.pipes if p.id in scope},proposed_geometry={p.id:p.points for p in target.pipes if p.id in scope},candidates=choice['candidates'],issues=choice.get('issues',[]),explanations=choice.get('explanations',result.get('explanations',[])),unverified=sorted({r['requirement'] for c in choice['candidates'] for r in c['validation'] if r['status']=='UNVERIFIED'}),clearance=clearance,approval_scope='Designer review of a computed proposal; not construction approval')
                proposal['extraction_coverage']=copy.deepcopy(coverage)
                state['proposals'].append(proposal);created.append(proposal)
            check_result(created)
            if validate_output is not None:validate_output(created)
            self._event(state,'proposals_generated',proposal_ids=[p['id'] for p in created])
            return created
        return self._mutate(id,revision,update)
    def decision(self,id,proposal_id,revision,decision,engineer,comment=''):
        if not engineer.strip():raise ApprovalError('Engineer identity is required')
        if decision not in ('approve','reject'):raise ApprovalError('Decision must be approve or reject')
        def update(state):
            proposal=next((p for p in state['proposals'] if p['id']==proposal_id),None)
            if proposal is None:raise NotFound(proposal_id)
            if proposal['source_revision']!=revision or proposal['source_digest']!=state['model_digest']:raise RevisionConflict('Proposal is stale')
            if proposal['status']!='pending':raise ApprovalError('A proposal can receive only one decision')
            if decision=='approve':
                if state['extraction_coverage']['status']=='incomplete':raise ApprovalError('Resolve unclassified CAD geometry before approving a drawing-scoped proposal')
                change_report(Model.model_validate(state['model']),Model.model_validate(proposal['model']),proposal['clearance'])
            proposal['status']='approved' if decision=='approve' else 'rejected';proposal['engineer']=engineer.strip();proposal['comment']=comment
            self._event(state,'proposal_'+proposal['status'],proposal_id=proposal_id,engineer=engineer.strip(),comment=comment)
            return proposal
        return self._mutate(id,revision,update)
    def approved(self,id,proposal_id):
        state=self.capture(id);proposal=next((p for p in state['proposals'] if p['id']==proposal_id),None)
        if proposal is None:raise NotFound(proposal_id)
        if proposal['source_revision']!=state['revision'] or proposal['source_digest']!=state['model_digest']:raise RevisionConflict('Proposal is stale')
        if proposal['status']!='approved':raise ApprovalError('Explicit current-revision designer approval is required before export or application')
        if state['extraction_coverage']['status']=='incomplete':raise ApprovalError('CAD extraction is incomplete')
        return state,proposal
    def apply(self,id,proposal_id,revision,engineer,confirmation):
        if confirmation is not True:raise ApprovalError('Explicit application confirmation is required')
        if not engineer.strip():raise ApprovalError('Engineer identity is required')
        def update(state):
            if state['source_adapter']=='autocad':raise ApprovalError('AutoCAD owns this source; apply changes through the native connector and synchronize its confirmed drawing revision')
            proposal=next((p for p in state['proposals'] if p['id']==proposal_id),None)
            if proposal is None:raise NotFound(proposal_id)
            if proposal['source_revision']!=revision or proposal['source_digest']!=state['model_digest']:raise RevisionConflict('Proposal is stale')
            if proposal['status']!='approved':raise ApprovalError('Designer approval is required')
            change_report(Model.model_validate(state['model']),Model.model_validate(proposal['model']),proposal['clearance'])
            state['model']=proposal['model'];state['model_digest']=digest(state['model']);state['revision']+=1;state['_cache']={};state['analysis']=None;proposal['status']='applied'
            state['affected_pipe_ids']=[p['id'] for p in state['model']['pipes']]
            for other in state['proposals']:
                if other['id']!=proposal_id and other['status'] not in ('rejected','applied'):other['status']='stale'
            self._event(state,'proposal_applied_to_session',proposal_id=proposal_id,engineer=engineer.strip(),cad_files_modified=False)
        self._mutate(id,revision,update);return self.get(id)
