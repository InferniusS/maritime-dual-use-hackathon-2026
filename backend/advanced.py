"""Bounded alternative exploration; no global/Pareto optimality claim."""
import itertools,time
from .models import Model
from .optimizer import optimize,objective,MODES
from .validation import metrics,validate,analyze,assurance
from .evidence import clearance_evidence
from .work_limits import work_scope,checkpoint,preflight_model,check_result

def network_metrics(model,original,clearance=.1):
    ms=[metrics(p) for p in model.pipes]
    hydraulics=[m['hydraulics'] for m in ms]
    known=[h['pressure_loss_pa'] for h in hydraulics if h['status']=='PASS']
    modification_distance=sum(objective(p,next(q for q in original.pipes if q.id==p.id),'modification',original)['terms']['modification'] for p in model.pipes)
    evidence=[e for p in model.pipes for e in clearance_evidence(model,p,clearance)]
    return dict(modification_distance_normalized=modification_distance,length=sum(m['length'] for m in ms),bends=sum(m['bends'] for m in ms),fitting_count=sum(m['fitting_count'] for m in ms),elevation_change=sum(m['elevation_change'] for m in ms),pressure_loss_pa=sum(known) if len(known)==len(ms) else None,known_pressure_loss_pa=sum(known) if known else None,hydraulic_coverage=f'{len(known)}/{len(ms)}',minimum_clearance_m=min((e['measured'] for e in evidence),default=None),clearance_required_m=clearance,modified_components=sum(p!=next(q for q in original.pipes if q.id==p.id) for p in model.pipes),issues=len(analyze(model,clearance)['issues']))

def explain(original,proposed,candidates,clearance):
    before=network_metrics(original,original,clearance);after=network_metrics(proposed,original,clearance)
    messages=[]
    for key,unit in [('length','m'),('bends','bends'),('elevation_change','m')]:
        delta=after[key]-before[key]
        messages.append(dict(kind='tradeoff' if delta>1e-8 else 'improvement' if delta< -1e-8 else 'preserved',metric=key,before=before[key],after=after[key],text=f"{key.replace('_',' ').capitalize()}: {before[key]:.3f} → {after[key]:.3f} {unit} (change {delta:+.3f})."))
    for c in candidates:
        failures=[r['requirement'] for r in c['validation'] if r['status']=='FAIL']
        unknown=[r['requirement'] for r in c['validation'] if r['status']=='UNVERIFIED']
        messages.append(dict(kind='validation',pipe_id=c['pipe_id'],text=f"{c['pipe_id']}: {c['status']}. {c['reason']}",failed=failures,unverified=unknown))
    messages.append(dict(kind='review',text='Feasible means evaluated model constraints only. Organizer standards, catalog approval, supports and stress remain subject to engineering review.'))
    return messages

def run_optimization(model,pipe_ids=None,mode='balanced',resolution=.5,clearance=.1,cancel=None,progress=None,alternatives=False,multiresolution=False,try_orders=False):
    with work_scope(cancel=cancel):
        preflight_model(model,'alternative routing')
        result=_run_optimization(model,pipe_ids,mode,resolution,clearance,cancel,progress,alternatives,multiresolution,try_orders)
        check_result(result)
        return result

def _run_optimization(model,pipe_ids=None,mode='balanced',resolution=.5,clearance=.1,cancel=None,progress=None,alternatives=False,multiresolution=False,try_orders=False):
    started=time.perf_counter();selected=pipe_ids if pipe_ids is not None else [p.id for p in model.pipes if p.editable]
    if len(selected)!=len(set(selected)):raise ValueError('Duplicate pipe IDs')
    # All independent proposals compare against the same original.
    modes=list(dict.fromkeys([mode,*MODES])) if alternatives else [mode]
    orders=[selected]
    if try_orders and len(selected)>1:orders.append(list(reversed(selected)))
    resolutions=list(dict.fromkeys([min(2.,resolution*2),resolution])) if multiresolution else [resolution]
    choices=[];attempts=[];seen=set();base=None;expanded_total=0
    # Bounded 20 independent runs maximum; every run remains cancellable.
    for order in orders:
        for target_mode in modes:
            for grid in resolutions:
                checkpoint('alternative exploration')
                if cancel and cancel.is_set():break
                result=optimize(model,order,target_mode,grid,clearance,cancel,(lambda n:progress(expanded_total+n)) if progress else None)
                expanded_total+=sum(c.get('expanded',0) for c in result['candidates'])
                if progress:progress(expanded_total)
                if multiresolution:result=refine_route(model,result,target_mode,clearance)
                if base is None:base=result
                accepted=all(c['status']=='accepted' for c in result['candidates']) and bool(result['candidates'])
                proposal=Model.model_validate(result['model'])
                # Recheck every selected final route together: no accepted final-system clash.
                valid=accepted and all(not any(r['status']=='FAIL' for r in validate(proposal,proposal,id,clearance)) for id in selected)
                attempts.append(dict(mode=target_mode,resolution=grid,order=order,status='feasible' if valid else 'unresolved',expanded=sum(c.get('expanded',0) for c in result['candidates']),reasons=[c['reason'] for c in result['candidates']]))
                if not valid:continue
                score=sum(objective(next(p for p in proposal.pipes if p.id==id),next(p for p in model.pipes if p.id==id),mode,model)['value'] for id in selected)
                key=tuple((p.id,tuple(tuple(round(v,7) for v in point) for point in p.points)) for p in proposal.pipes)
                if key in seen:continue
                seen.add(key)
                item=dict(id=f'route-{len(choices)+1}',name=target_mode.title()+' / '+str(grid)+' m',mode=target_mode,resolution=grid,order=order,network_cost=score,metrics=network_metrics(proposal,model,clearance),**result)
                item['explanations']=explain(model,proposal,result['candidates'],clearance)
                choices.append(item)
    if base is None:base=dict(model=model.model_dump(),candidates=[],issues=analyze(model,clearance)['issues'])
    # Empirical nondominance over explored candidates only, not the true continuous frontier.
    dimensions=['length','bends','modified_components','modification_distance_normalized']
    if choices and all(c['metrics']['pressure_loss_pa'] is not None for c in choices):dimensions.append('pressure_loss_pa')
    elif choices and all(c['metrics']['known_pressure_loss_pa'] is not None for c in choices):dimensions.append('known_pressure_loss_pa')
    for c in choices:
        c['nondominated']=not any(all(d['metrics'][k]<=c['metrics'][k]+1e-9 for k in dimensions) and any(d['metrics'][k]<c['metrics'][k]-1e-9 for k in dimensions) for d in choices if d is not c)
    if choices:
        best=min(choices,key=lambda c:c['network_cost']);base={k:best[k] for k in ('model','candidates','issues')};chosen=best['id']
    else:chosen=None
    proposed=Model.model_validate(base['model'])
    rows=[r for c in base['candidates'] for r in c['validation']]
    return dict(**base,alternatives=choices,selected_alternative=chosen,exploration=attempts,frontier=dict(label='Approximate nondominated set among explored candidates',dimensions=dimensions),network_before=network_metrics(model,model,clearance),network_after=network_metrics(proposed,model,clearance),explanations=explain(model,proposed,base['candidates'],clearance),assurance=assurance(rows),network_cost_before=sum(objective(p,p,mode,model)['value'] for p in model.pipes if p.id in selected),network_cost_after=sum(objective(p,next(q for q in model.pipes if q.id==p.id),mode,model)['value'] for p in proposed.pipes if p.id in selected),validation_coverage={status:sum(r['status']==status for r in rows) for status in ('PASS','FAIL','UNVERIFIED','NOT_APPLICABLE')},runtime_s=time.perf_counter()-started)

def refine_route(original,result,mode,clearance):
    """Bounded local vertex removal; every geometric shortcut gets fresh validation."""
    proposal=Model.model_validate(result['model'])
    for candidate in result['candidates']:
        if candidate['status']!='accepted':continue
        id=candidate['pipe_id'];pipe=next(p for p in proposal.pipes if p.id==id);reference=next(p for p in original.pipes if p.id==id)
        attempts=0;improved=True
        while improved and attempts<24:
            checkpoint('local route refinement')
            improved=False
            for start in range(len(pipe.points)-2):
                for end in range(len(pipe.points)-1,start+1,-1):
                    if end<=start+1:continue
                    attempts+=1;trial=proposal.model_copy(deep=True);new=next(p for p in trial.pipes if p.id==id)
                    new.points=pipe.points[:start+1]+pipe.points[end:]
                    checks=validate(proposal,trial,id,clearance)
                    if not any(r['status']=='FAIL' for r in checks) and objective(new,reference,mode,original)['value']<objective(pipe,reference,mode,original)['value']-1e-8:
                        proposal=trial;pipe=new;improved=True;candidate['reason']+='; independently validated local shortcut';break
                    if attempts>=24:break
                if improved or attempts>=24:break
        candidate['after']=metrics(pipe)
        candidate['objective_after']=objective(pipe,reference,mode,original)
        # Final self-check evaluates all final-system geometry; original preservation
        # was independently checked on each step before applying a shortcut.
        baseline=proposal.model_copy(deep=True);baseline.pipes=[reference if p.id==id else p for p in baseline.pipes]
        candidate['validation']=validate(baseline,proposal,id,clearance)
        candidate['assurance']=assurance(candidate['validation'])
    result['model']=proposal.model_dump();result['issues']=analyze(proposal,clearance)['issues']
    return result
