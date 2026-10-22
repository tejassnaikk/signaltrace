"""Held-out analysis for the frozen agent vs the M5 engine (M7 step 1).

Reads evaluation/results/agent_heldout_f854dee9.jsonl, agent_dev_f854dee9.jsonl
and heldout_engine.json (no model call, no DB). Prints Score A / Score B /
total top-1 / C1 / rejected-claims / determinism / efficiency tables, the
dev comparators, and a paired exact sign test (McNemar) on per-date top-1.

Run from the repo root:
    python -I evaluation/integrity/analyze.py
"""
import json,collections,ast,sys
from collections import Counter,defaultdict
import os
R=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),'evaluation','results')+'/'
ho=[json.loads(l) for l in open(R+'agent_heldout_f854dee9.jsonl')]
dv=[json.loads(l) for l in open(R+'agent_dev_f854dee9.jsonl')]
eng=json.load(open(R+'heldout_engine.json'))
INC=["I1","I2","I3","I3b","I4","I5","I6","I7","C1"]
assert len(ho)==219 and all(r['status']=='ok' for r in ho)
g=defaultdict(list)
for r in ho: g[(r['incident_id'],r['date'])].append(r)
assert all(len(v)==3 for v in g.values()) and len(g)==73
def dist(counts):  # counts per date of passes out of 3
    return (sum(c==3 for c in counts),sum(c>=2 for c in counts),sum(c==0 for c in counts))
# Score A
tg={'I1':['sessions/channel/Organic Search'],'I2':['conversion_rate/channel/Referral'],'I3':['aov/channel/Direct'],'I3b':['aov/channel/Direct'],'I5':['conversion_rate/device/desktop'],'I7':['sessions/channel/Organic Search','conversion_rate/channel/Referral']}
def tkey(k):
    m,d,s=ast.literal_eval(k); return f"{m}/{d}/{s}"
SA={}
for iid in tg:
    per=defaultdict(lambda: defaultdict(int)) # target -> date -> passes
    for (i,d),rs in g.items():
        if i!=iid: continue
        for r in rs:
            for k,v in r['score_a'].items(): per[tkey(k)][d]+=int(v)
    for t,dd in per.items(): SA[(iid,t)]=(sum(dd.values()),3*len(dd),dist(list(dd.values())),len(dd))
engSA={}
for iid,l in eng['aggregate']['score_a'].items():
    for x in l: engSA[(iid,f"{x['metric']}/{x['dimension']}/{x['segment']}")]=(x['pass'],x['n'])
print('SCORE A')
for k,v in SA.items(): print(k,f"{v[0]}/{v[1]}",'dates 3/3,>=2,0:',v[2],'of',v[3],'engine',engSA[k])
# Score B
SB={}
for iid in INC:
    n=0;pat=t1=t3=both=0;dp=defaultdict(lambda:defaultdict(int))
    for (i,d),rs in g.items():
        if i!=iid: continue
        for r in rs:
            sb=r['score_b'];n+=1;pat+=sb['pattern_correct']
            if sb['top1_correct'] is not None: t1+=sb['top1_correct'];dp['t1'][d]+=int(sb['top1_correct'])
            if sb['top3_correct'] is not None: t3+=sb['top3_correct'];dp['t3'][d]+=int(sb['top3_correct'])
            both+=bool(sb.get('both_in_top3'))
            dp['pat'][d]+=int(sb['pattern_correct'])
            if iid=='I7': dp['both'][d]+=int(bool(sb.get('both_in_top3')))
    SB[iid]=dict(n=n,pat=pat,t1=t1,t3=t3,both=both,d={k:dist(list(v.values())) for k,v in dp.items()})
es=eng['aggregate']['score_b']
print('SCORE B')
for iid in INC: print(iid,SB[iid]['n'],SB[iid]['pat'],SB[iid]['t1'],SB[iid]['t3'],SB[iid]['d'],'eng',es[iid],)
print('I7 both',SB['I7']['both'],SB['I7']['d'].get('both'),'eng',eng['aggregate']['i7_both_top3'])
has=['I1','I2','I3','I3b','I5','I7']
at1=sum(SB[i]['t1'] for i in has);an=sum(SB[i]['n'] for i in has)
et1=sum(es[i]['top1'] for i in has);en=sum(es[i]['n'] for i in has)
at3=sum(SB[i]['t3'] for i in has);et3=sum(es[i]['top3'] for i in has)
print('TOTAL top1 agent',at1,an,'engine',et1,en,'top3',at3,et3)
# dev
dsb=defaultdict(lambda: Counter())
for r in dv:
    sb=r['score_b'];i=r['incident_id'];dsb[i]['n']+=1;dsb[i]['pat']+=sb['pattern_correct']
    if sb['top1_correct'] is not None: dsb[i]['t1']+=sb['top1_correct'];dsb[i]['t3']+=sb['top3_correct']
dt1=sum(dsb[i]['t1'] for i in has);dn=sum(dsb[i]['n'] for i in has)
print('dev total top1',dt1,dn,{i:dict(dsb[i]) for i in INC})
dsa=defaultdict(lambda:[0,0])
for r in dv:
    if r['score_a']:
        for k,v in r['score_a'].items(): dsa[(r['incident_id'],tkey(k))][0]+=int(v);dsa[(r['incident_id'],tkey(k))][1]+=1
print('dev SA',dict(dsa))
# C1
c1=[r for r in ho if r['incident_id']=='C1']
fp=sum(r['c1_claim_counts']['false_positives'] for r in c1);ut=sum(r['c1_claim_counts']['untested_claims'] for r in c1)
pn=Counter(r['answer']['pattern'] for r in c1)
print('C1 runs',len(c1),'fp',fp,'untested',ut,'patterns',pn,'dates w fp',sum(1 for (i,d),rs in g.items() if i=='C1' and any(r['c1_claim_counts']['false_positives'] for r in rs)))
print('C1 per-date fp',[ (d,[r['c1_claim_counts'] for r in rs]) for (i,d),rs in g.items() if i=='C1'])
dc=[r for r in dv if r['incident_id']=='C1'];print('dev C1',sum(r['c1_claim_counts']['false_positives'] for r in dc),sum(r['c1_claim_counts']['untested_claims'] for r in dc),Counter(r['answer']['pattern'] for r in dc))
print('pattern dist by incident',{i:dict(Counter(r['answer']['pattern'] for r in ho if r['incident_id']==i)) for i in INC})
# rejected
rj=defaultdict(Counter)
for r in ho:
    for k,v in r['rejected_reasons'].items(): rj[r['incident_id']][k]+=v
tot=Counter();[tot.update(v) for v in rj.values()]
print('REJ',dict(tot),{i:dict(rj[i]) for i in INC})
print('runs with any rejection',sum(1 for r in ho if r['n_rejected']),'accepted claims total',sum(len(r['answer']['claims']) for r in ho),'runs w/0 accepted claims',sum(1 for r in ho if not r['answer']['claims']))
# determinism
def key(r):
    a=r['answer'];return (a['pattern'],tuple((c['entry_id'],c['direction'],c['strength']) for c in a['claims']),tuple(c['entry_id'] for c in a['primary']))
ident=[k for k,rs in g.items() if len({key(r) for r in rs})==1]
dis=[k for k in g if k not in ident]
print('identical',len(ident),'/73; disagree',[(k,[r['answer']['pattern'] for r in g[k]],[len(r['answer']['claims']) for r in g[k]]) for k in dis])
# stricter: same answer in terms of semantic score
# efficiency
td=Counter(r['tool_call_counts']['test_dimension'] for r in ho);tc=Counter(sum(r['tool_call_counts'].values()) for r in ho)
print('td',td,'total',tc,'mean elapsed',sum(r['elapsed_s'] for r in ho)/219)
tdd=Counter(r['tool_call_counts']['test_dimension'] for r in dv);print('dev td',tdd)

# Paired per-date top-1 (agent runs are identical per date, so run 0 is the date's answer)
from math import comb
ea={(x['incident'],x['date']):x['score_b'] for x in eng['records']}
b=c=both=neither=0
for (i,d),rs in g.items():
    a=rs[0]['score_b']['top1_correct']
    if a is None: continue
    e=ea[(i,d)]['top1_correct']
    if a and e: both+=1
    elif a: b+=1
    elif e: c+=1
    else: neither+=1
n=b+c;k=min(b,c)
p=min(1.0,2*sum(comb(n,j) for j in range(k+1))/2**n)
print(f'PAIRED top-1 over {b+c+both+neither} dates: both {both}, agent-only {b}, engine-only {c}, neither {neither}; '
      f'agent {both+b}/46 vs engine {both+c}/46; exact two-sided sign test (McNemar) on {n} discordant pairs p={p:.4f}')
