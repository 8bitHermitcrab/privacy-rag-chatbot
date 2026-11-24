"""기존 문서에서 PID 사전을 생성합니다. 정답 라벨·외부 API는 사용하지 않습니다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime,timezone

from . import config,db,pii_pipeline
from .person_rules import COLUMNS, LABEL_TO_COLUMN, POLICY_VERSION, REASONS, PROFILES, clean_values, decide
from .person_ownership import group_records


class PersonDictionaryError(ValueError):
    """사용자에게 표시할 수 있는 인물사전 검증 오류."""


def report_dir():
    from server_admin.security import state_dir
    return Path(os.getenv('PID_REPORT_DIR',str(state_dir()/'person_dictionary')))


def read_json(path,default):
    if not path.exists():return default
    return json.loads(path.read_text(encoding='utf-8'))


def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)


def current_people(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT pid,data1,data2,data3,data4,data5,data6,data7,created_at,updated_at FROM vault.person ORDER BY pid')
        return [dict(zip(('pid',)+COLUMNS+('created_at','updated_at'),row)) | clean_values(dict(zip(('pid',)+COLUMNS+('created_at','updated_at'),row))) for row in cur.fetchall()]


def stored_documents():
    with db.rag_conn() as conn,conn.cursor() as cur:
        cur.execute('SELECT doc_id,masked_text FROM rag.chunks ORDER BY doc_id,chunk_idx')
        docs={}
        for doc_id,text in cur.fetchall():
            if doc_id.startswith('SYN-'):continue
            docs.setdefault(doc_id,[]).append(text)
    return {doc:'\n'.join(parts) for doc,parts in docs.items()}


def prepare_documents(progress=None, source=None):
    stored=stored_documents()
    # 기존 DB의 토큰을 보존합니다. 키가 달라졌어도 정규화 값·종류가 유일하게
    # 일치할 때만 기존 토큰으로 대응시키고, 완성된 masked_text까지 대조합니다.
    with db.vault_conn() as conn,conn.cursor() as cur:
        cur.execute('SELECT token,label,normalized FROM vault.token_map')
        by_identity={}
        for token,label,normalized in cur.fetchall():
            by_identity.setdefault((label,normalized),[]).append(token)
    path=Path(source or os.getenv('PID_SOURCE_PATH') or config.DATA_DIR/'holdout_labeled.jsonl')
    # id와 text만 사용하고 정답 entities·person_id는 참조하지 않습니다.
    docs=[]
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():continue
        row=json.loads(line)
        if row['id'] in stored:
            docs.append({'id':row['id'],'text':row['text']})
    if len({d['id'] for d in docs})!=len(docs):
        raise PersonDictionaryError('원문 문서 ID가 중복됩니다.')
    result=[]
    compact=lambda text:' '.join(text.split())
    for index,doc in enumerate(docs,1):
        _,records=pii_pipeline.pseudonymize(doc['text'],pii_pipeline.detect(doc['text']))
        reused=0
        for record in records:
            found=by_identity.get((record['label'],record['normalized']),[])
            if len(found)==1:
                if record['token']!=found[0]:reused+=1
                record['token']=found[0]
        out=[];end=0
        for record in records:
            out.extend((doc['text'][end:record['start']],record['token']))
            end=record['end']
        out.append(doc['text'][end:])
        masked=''.join(out)
        if compact(masked)!=compact(stored[doc['id']]):
            result.append({'doc_id':doc['id'],'source_hash':hashlib.sha256(doc['text'].encode()).hexdigest(),
                           'subjects':[],'unassigned':[],'document_hold':'DOCUMENT_MASK_MISMATCH','reused_tokens':reused})
        else:
            subjects,unassigned=group_records(doc['text'],records)
            result.append({'doc_id':doc['id'],'source_hash':hashlib.sha256(doc['text'].encode()).hexdigest(),
                           'subjects':subjects,'unassigned':unassigned,'document_hold':None if subjects else 'NO_PERSON_DETECTED','reused_tokens':reused})
        if progress:progress({'stage':'분석 중','done':index,'total':len(docs)})
    missing=sorted(set(stored)-{d['id'] for d in docs})
    if missing:
        raise PersonDictionaryError(f'DB 문서 중 원문을 찾지 못한 문서가 {len(missing)}개 있습니다.')
    return result


def validate_subject(subject,mapping):
    for r in subject['checks']:
        saved=mapping.get(r['token'])
        if saved is None:return 'TOKEN_MAPPING_MISSING'
        if saved['normalized']!=r['normalized']:return 'TOKEN_MAPPING_CONFLICT'
        if saved['label']!=r['label']:
            # 기존 num namespace가 만든 전화 형태 토큰은 전화 분류만 허용합니다.
            if not (r['label']=='phone_number' and saved['label'] in pii_pipeline.NUMERIC and r['token'].startswith('<PHONE_')):
                return 'TOKEN_MAPPING_CONFLICT'
    return subject['force_hold']


def public_subject(subject):
    return {k:v for k,v in subject.items() if k not in ('checks','force_hold')}


def state_key(doc,subject,sensitivity='balanced'):
    value=f"{POLICY_VERSION}|{sensitivity}|{doc['doc_id']}|{doc['source_hash']}|{subject['owner_key']}"
    return hashlib.sha256(value.encode()).hexdigest()


def process_prepared(prepared,people,mapping,state,persist=None,sensitivity='balanced',manual=None,shared=None,prior=None):
    """manual: {(doc_id, owner_key): (status, pid, role)} 관리자 수동 판정. 자동 판정이 건너뜁니다.
    shared: 관리자가 공유 값(대표번호 등)으로 표시한 토큰. 연결 근거와 저장 대상에서 뺍니다.
    prior: {(doc_id, owner_key): (pid, status, reason, source_hash, role)} 이전 자동 판정에서 PID가 붙은 언급.
    정책 버전이 바뀌어 state 캐시가 무효가 되어도, 같은 원문으로 이미 붙은 언급은 자기 PID와 다시 비교하지 않고 유지합니다."""
    if sensitivity not in PROFILES: raise PersonDictionaryError('민감도 설정을 확인하세요.')
    manual=manual or {}; shared=set(shared or ()); prior=prior or {}
    known_roles={}
    for status,pid,role in manual.values():
        if pid is not None and role:known_roles.setdefault(pid,set()).add(role)
    for item in state.values():
        if item.get('role'):known_roles.setdefault(item['pid'],set()).add(item['role'])
    for pid,_status,_reason,_hash,role in prior.values():
        if role:known_roles.setdefault(pid,set()).add(role)
    events=[]; new_state=dict(state)
    next_virtual=-1
    for doc in prepared:
        if doc['document_hold']:
            reason=doc['document_hold']
            events.append({'doc_id':doc['doc_id'],'action':'hold_document','reason':reason,'message':REASONS[reason]})
        for feature in doc['unassigned']:
            if (doc['doc_id'],f"orphan|{feature['label']}|{feature['token']}|{feature['line']}") in manual:
                events.append({'doc_id':doc['doc_id'],'action':'manual','reason':'MANUAL_DECISION','label':feature['label'],'token':feature['token'],'line':feature['line'],'message':REASONS['MANUAL_DECISION']})
                continue
            events.append({'doc_id':doc['doc_id'],'action':'hold_feature',**feature,'message':REASONS[feature['reason']]})
        for subject in doc['subjects']:
            if shared:
                subject={**subject,'values':{c:[t for t in v if t not in shared] for c,v in subject['values'].items()}}
            fixed=manual.get((doc['doc_id'],subject['owner_key']))
            if fixed:
                events.append({'doc_id':doc['doc_id'],**public_subject(subject),'action':'manual','reason':'MANUAL_DECISION',
                               'pid':fixed[1],'candidates':[],'manual_status':fixed[0],'message':REASONS['MANUAL_DECISION']})
                continue
            reason=validate_subject(subject,mapping)
            key=state_key(doc,subject,sensitivity)
            cached=state.get(key)
            before=prior.get((doc['doc_id'],subject['owner_key']))
            if before and before[3]!=doc['source_hash']:before=None
            if reason:
                decision={'action':'hold','reason':reason,'pid':None,'candidates':[]}
            elif cached and any(p['pid']==cached['pid'] and all(set(subject['values'][c])<=set(p[c]) for c in COLUMNS) for p in people):
                decision={'action':'unchanged','reason':'ALREADY_LINKED','pid':cached['pid'],'candidates':[cached['pid']]}
            elif before and any(p['pid']==before[0] and all(set(subject['values'][c])<=set(p[c]) for c in COLUMNS) for p in people):
                decision={'action':'unchanged','reason':before[2] or 'ALREADY_LINKED','pid':before[0],'candidates':[before[0]],'keep_status':before[1]}
            else:
                decision=decide(subject['values'],people,sensitivity,subject['role'],known_roles)
            if decision['action']=='create':
                skip={c:set(v) for c,v in decision.get('exclude',{}).items()}
                values={c:[t for t in v if t not in skip.get(c,set())] for c,v in subject['values'].items()}
                pid=persist('create',None,values) if persist else next_virtual
                next_virtual-=1
                people.append({'pid':pid,**clean_values(values)})
                decision['pid']=pid
            elif decision['action']=='merge':
                person=next(p for p in people if p['pid']==decision['pid'])
                skip={c:set(v) for c,v in decision.get('exclude',{}).items()}
                merged={c:sorted(set(person[c])|(set(subject['values'][c])-skip.get(c,set()))) for c in COLUMNS}
                if merged!={c:person[c] for c in COLUMNS}:
                    if persist:persist('merge',person['pid'],merged)
                    person.update(merged)
            if decision['action'] in ('create','merge','unchanged'):
                new_state[key]={'pid':decision['pid'],'doc_id':doc['doc_id'],'role':subject['role']}
                known_roles.setdefault(decision['pid'],set()).add(subject['role'])
            events.append({'doc_id':doc['doc_id'],**public_subject(subject),**decision,'message':REASONS[decision['reason']]})
    return events,new_state,people


MENTION_STATUS = {'create': 'created', 'merge': 'linked', 'unchanged': 'linked', 'hold': 'held'}
MENTION_COLUMNS = ('doc_id', 'owner_key', 'role', 'name_token') + COLUMNS + (
    'pid', 'status', 'reason', 'policy_version', 'sensitivity', 'source_hash')


def mention_rows(prepared, events, sensitivity):
    """판정 이벤트를 vault.person_mention 행으로 바꿉니다. 원문·정규화 값은 넣지 않고 토큰만 씁니다."""
    hashes = {doc['doc_id']: doc['source_hash'] for doc in prepared}
    rows = {}
    for e in events:
        base = {'doc_id': e['doc_id'], 'policy_version': POLICY_VERSION, 'sensitivity': sensitivity,
                'source_hash': hashes[e['doc_id']], 'reason': e['reason']}
        if e['action'] in MENTION_STATUS and e.get('owner_key'):
            values = clean_values(e.get('values') or {})
            row = {**base, 'owner_key': e['owner_key'], 'role': e.get('role'), 'name_token': e.get('name_token'),
                   **values, 'pid': e['pid'] if e['action'] != 'hold' else None,
                   'status': e.get('keep_status') or MENTION_STATUS[e['action']]}
        elif e['action'] == 'hold_feature':
            column = LABEL_TO_COLUMN.get(e['label'])
            if column is None:
                continue
            values = clean_values({column: [e['token']]})
            row = {**base, 'owner_key': f"orphan|{e['label']}|{e['token']}|{e['line']}", 'role': None,
                   'name_token': None, **values, 'pid': None, 'status': 'held'}
        else:
            continue
        rows[(row['doc_id'], row['owner_key'])] = row
    return list(rows.values())


def prior_links(cur):
    """이전 자동 판정에서 PID가 붙은 언급. 0003 이전 DB면 빈 값입니다."""
    cur.execute("SELECT to_regclass('vault.person_mention')")
    if cur.fetchone()[0] is None:
        return {}
    cur.execute("""SELECT doc_id,owner_key,pid,status,reason,source_hash,role FROM vault.person_mention
        WHERE decided_by='auto' AND pid IS NOT NULL AND status IN ('created','linked')""")
    return {(d, o): (pid, st, r, h, role) for d, o, pid, st, r, h, role in cur.fetchall()}


def manual_decisions(cur):
    """관리자 수동 판정과 공유 값 토큰. 0003 이전 DB면 빈 값입니다."""
    cur.execute("SELECT to_regclass('vault.person_mention')")
    if cur.fetchone()[0] is None:
        return {}, set()
    cur.execute("SELECT doc_id,owner_key,status,pid,role," + '||'.join(COLUMNS) + " FROM vault.person_mention WHERE decided_by='manual'")
    manual, shared = {}, set()
    for doc_id, owner_key, status, pid, role, tokens in cur.fetchall():
        manual[(doc_id, owner_key)] = (status, pid, role)
        if status == 'shared':
            shared |= set(tokens or [])
    return manual, shared


def write_mentions(cur, rows):
    """자동 판정 행만 갱신합니다. 관리자가 수동 판정한 행(decided_by='manual')은 덮어쓰지 않습니다."""
    cur.execute("SELECT to_regclass('vault.person_mention')")
    if cur.fetchone()[0] is None:
        raise PersonDictionaryError('vault.person_mention 테이블이 없습니다. DB 버전 0003을 먼저 적용하세요.')
    cols = ','.join(MENTION_COLUMNS)
    marks = ','.join(['%s'] * len(MENTION_COLUMNS))
    updates = ','.join(f'{c}=EXCLUDED.{c}' for c in MENTION_COLUMNS if c not in ('doc_id', 'owner_key'))
    cur.executemany(f"""INSERT INTO vault.person_mention({cols}) VALUES({marks})
        ON CONFLICT (doc_id, owner_key) DO UPDATE SET {updates}
        WHERE vault.person_mention.decided_by='auto'""",
        [[r[c] for c in MENTION_COLUMNS] for r in rows])
    keep = [r['doc_id'] + '\x1f' + r['owner_key'] for r in rows]
    cur.execute("""DELETE FROM vault.person_mention WHERE decided_by='auto'
        AND doc_id || chr(31) || owner_key <> ALL(%s::text[])""", (keep,))
    return len(rows), cur.rowcount


def build_existing(apply=False,progress=None,sensitivity=None,source=None,expected_plan=None):
    # 상태 파일 저장까지 실행 간 순서를 보장합니다. CLI와 관리 화면이 같은 잠금을 씁니다.
    from server_admin.security import prepare_private_dir
    prepare_private_dir()
    with db.vault_conn() as guard,guard.cursor() as lock:
        lock.execute("SET LOCAL lock_timeout='5s'")
        lock.execute('SELECT pg_advisory_xact_lock(721121102)')
        return _build_existing(apply,progress,sensitivity,source,expected_plan)


def _build_existing(apply=False,progress=None,sensitivity=None,source=None,expected_plan=None):
    sensitivity=sensitivity or os.getenv('PID_SENSITIVITY','balanced')
    if sensitivity not in PROFILES: raise PersonDictionaryError('민감도 설정을 확인하세요.')
    prepared=prepare_documents(progress,source)
    with db.vault_conn() as conn,conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='5s'")
        cur.execute('SELECT pg_advisory_xact_lock(721121101)')
        state=read_json(report_dir()/'state.json',{})
        if apply:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='vault' AND table_name='person' AND data_type='ARRAY'")
            if {row[0] for row in cur.fetchall()}!=set(COLUMNS):
                raise PersonDictionaryError('person.data1~7 배열 전환을 먼저 적용해야 합니다.')
            cur.execute('LOCK TABLE vault.token_map IN SHARE MODE')
            cur.execute('SELECT pid FROM vault.person ORDER BY pid FOR UPDATE')
            cur.fetchall()
        people=current_people(conn)
        before=len(people)
        cur.execute('SELECT token,label,normalized FROM vault.token_map')
        mapping={token:{'label':label,'normalized':normalized} for token,label,normalized in cur.fetchall()}
        def persist(action,pid,values):
            args=[values[c] for c in COLUMNS]
            if action=='create':
                cur.execute('''INSERT INTO vault.person(data1,data2,data3,data4,data5,data6,data7)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING pid''',args)
                return cur.fetchone()[0]
            cur.execute('''UPDATE vault.person SET data1=%s,data2=%s,data3=%s,data4=%s,data5=%s,data6=%s,data7=%s WHERE pid=%s''',args+[pid])
            return pid
        manual,shared=manual_decisions(cur)
        prior=prior_links(cur)
        plan_people=[{'pid':p['pid'],**clean_values(p)} for p in people]
        plan=hashlib.sha256(json.dumps([POLICY_VERSION,sensitivity,prepared,plan_people,mapping,state,
            sorted([list(k),list(v)] for k,v in manual.items()),sorted(shared),
            sorted([list(k),list(v)] for k,v in prior.items())],sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
        if apply and (not expected_plan or expected_plan!=plan):
            raise PersonDictionaryError('미리보기 이후 원문·DB·설정이 변경되었거나 미리보기가 없습니다. 다시 미리보기를 실행하세요.')
        events,new_state,people=process_prepared(prepared,people,mapping,state,persist if apply else None,sensitivity,manual,shared,prior)
        mention_written=write_mentions(cur,mention_rows(prepared,events,sensitivity)) if apply else None
    # DB commit 이후에만 상태·결과를 저장합니다. 파일 실패를 DB 실패로 숨기지 않습니다.
    from collections import Counter
    result={'plan':plan,'sensitivity':sensitivity,'profile_description':PROFILES[sensitivity],'policy_version':POLICY_VERSION,'created_at':datetime.now(timezone.utc).isoformat(),
            'applied':apply,'documents':len(prepared),'before_people':before,'after_people':len(people),
            'actions':dict(Counter(e['action'] for e in events)),
            'reasons':dict(Counter(e['reason'] for e in events)), 'events':events,
            'source':'rag.chunks + 원문 id/text','gold_labels_used':False,'synthetic_included':False,
            'existing_tokens_reused':sum(doc.get('reused_tokens',0) for doc in prepared),
            'query_token_alignment_warning':bool(sum(doc.get('reused_tokens',0) for doc in prepared)),
            'mentions_written':mention_written[0] if mention_written else None,
            'mentions_removed':mention_written[1] if mention_written else None}
    if apply:
        atomic_json(report_dir()/'state.json',new_state)
        atomic_json(report_dir()/'latest_report.json',result)
    else:atomic_json(report_dir()/f'dry_run_{sensitivity}.json',result)
    if progress:progress({'stage':'완료','done':len(prepared),'total':len(prepared)})
    return result


def list_people(limit=200):
    with db.vault_conn() as conn:
        result=current_people(conn)[:limit]
    return [{k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in p.items()} for p in result]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='미리보기 계획이 일치할 때 vault.person에 반영')
    parser.add_argument('--sensitivity',choices=tuple(PROFILES),default=os.getenv('PID_SENSITIVITY','balanced'))
    parser.add_argument('--source',type=Path,help='원문 JSONL 경로 (id/text 사용)')
    parser.add_argument('--plan',help='직전 미리보기의 plan 값')
    args=parser.parse_args()
    result=build_existing(args.apply,lambda p:print(json.dumps(p,ensure_ascii=False),flush=True) if p['done']%20==0 or p['done']==p['total'] else None,sensitivity=args.sensitivity,source=args.source,expected_plan=args.plan)
    print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
