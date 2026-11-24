"""관리자 수동 인물 검토 (vault.person_mention 기반).

자동 규칙으로 판단할 수 없는 두 가지를 서버 관리자가 직접 처리합니다.
- 인물 보류: 이름은 있으나 PID가 정해지지 않은 등장 기록 → 기존 PID 연결 / 새 PID(동명이인) / 사람 아님
- 주인 미상 정보: 누구 것인지 모르는 전화·계좌 등 → 같은 문서 인물에 귀속 / 공유 값 / 무시
수동 판정은 decided_by='manual' 로 남고, 인물사전 자동 재생성이 덮어쓰지 않습니다.
화면에는 관리자 판단을 위해 원문 값을 보여 주며, 열람·판정은 감사 기록에 남깁니다.
"""
from . import db
from .person_rules import COLUMNS, LABEL_TO_COLUMN

COLUMN_LABEL = {c: l for l, c in LABEL_TO_COLUMN.items()}
LABEL_KO = {'phone_number': '전화번호', 'person': '이름', 'email': '이메일', 'address': '주소',
            'resident_registration_number': '주민번호', 'account_number': '계좌번호', 'card_number': '카드번호'}
ACTIVE = ('linked', 'created', 'manual_linked', 'manual_created')
PERSON_ACTIONS = ('link', 'create', 'dismiss')
FEATURE_ACTIONS = ('assign', 'shared', 'dismiss')


class ReviewError(ValueError):
    """화면에 그대로 보여 줄 수 있는 검토 오류."""


MENTION_SELECT = ('SELECT mention_id,doc_id,owner_key,role,name_token,' + ','.join(COLUMNS) +
                  ',pid,status,reason,decided_by,review_note,reviewed_at,updated_at FROM vault.person_mention')
MENTION_KEYS = ('mention_id', 'doc_id', 'owner_key', 'role', 'name_token') + COLUMNS + (
    'pid', 'status', 'reason', 'decided_by', 'review_note', 'reviewed_at', 'updated_at')


def _rows(cur, keys):
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _tokens(row):
    return sorted({t for c in COLUMNS for t in (row.get(c) or [])})


def _token_info(cur, tokens):
    if not tokens:
        return {}
    cur.execute('SELECT token,label,normalized,originals FROM vault.token_map WHERE token = ANY(%s)', (list(tokens),))
    return {t: {'label': l, 'value': (o or [n])[0], 'originals': list(o or [n])} for t, l, n, o in cur.fetchall()}


def _decode(row, info):
    """토큰 배열을 [{유형, 원문, 토큰}] 목록으로 바꿉니다."""
    out = []
    for c in COLUMNS:
        for t in row.get(c) or []:
            out.append({'column': c, 'label': LABEL_KO[COLUMN_LABEL[c]], 'token': t,
                        'value': info.get(t, {}).get('value', '(대응 없음)')})
    return out


def _shared_tokens(cur):
    cur.execute("SELECT " + '||'.join(COLUMNS) + " FROM vault.person_mention WHERE status='shared'")
    return {t for (arr,) in cur.fetchall() for t in (arr or [])}


def summary():
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT (owner_key LIKE 'orphan|%%') AS orphan, status, count(*)
                       FROM vault.person_mention GROUP BY 1,2""")
        rows = cur.fetchall()
    result = {'person_held': 0, 'feature_held': 0, 'manual': 0, 'auto_linked': 0}
    for orphan, status, n in rows:
        if status == 'held':
            result['feature_held' if orphan else 'person_held'] += n
        elif status.startswith('manual') or status in ('shared', 'dismissed'):
            result['manual'] += n
        else:
            result['auto_linked'] += n
    return result


def list_items(queue):
    """queue: person(인물 보류) / feature(주인 미상) / manual(수동 처리 완료)"""
    where = {'person': "status='held' AND owner_key NOT LIKE 'orphan|%%'",
             'feature': "status='held' AND owner_key LIKE 'orphan|%%'",
             'manual': "decided_by='manual'"}.get(queue)
    if where is None:
        raise ReviewError('목록 종류를 확인하세요.')
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute(MENTION_SELECT + ' WHERE ' + where + ' ORDER BY doc_id, mention_id LIMIT 1000')
        rows = _rows(cur, MENTION_KEYS)
        info = _token_info(cur, {t for r in rows for t in _tokens(r)})
    items = []
    for r in rows:
        decoded = _decode(r, info)
        name = next((d['value'] for d in decoded if d['column'] == 'data2'), None)
        others = [d for d in decoded if d['column'] != 'data2']
        items.append({'mention_id': r['mention_id'], 'doc_id': r['doc_id'], 'name': name, 'role': r['role'],
                      'status': r['status'], 'reason': r['reason'], 'pid': r['pid'],
                      'summary': ', '.join(f"{d['label']} {d['value']}" for d in others[:3]),
                      'kind': 'feature' if r['owner_key'].startswith('orphan|') else 'person'})
    return items


def _document(doc_id, info_cur):
    """가명처리 본문을 원문으로 되돌린 조각 목록. 토큰 조각에는 토큰을 함께 둡니다."""
    import re
    with db.rag_conn() as rconn, rconn.cursor() as rcur:
        rcur.execute('SELECT masked_text FROM rag.chunks WHERE doc_id=%s ORDER BY chunk_idx', (doc_id,))
        text = '\n'.join(r[0] for r in rcur.fetchall())
    pattern = re.compile(r'<[A-Z_]+_[0-9A-Za-z]+>')
    tokens = set(pattern.findall(text))
    info = _token_info(info_cur, tokens)
    parts, last = [], 0
    for m in pattern.finditer(text):
        if m.start() > last:
            parts.append({'text': text[last:m.start()]})
        t = m.group()
        parts.append({'text': info.get(t, {}).get('value', t), 'token': t,
                      'label': LABEL_KO.get(info.get(t, {}).get('label'), '')})
        last = m.end()
    if last < len(text):
        parts.append({'text': text[last:]})
    return parts


def _person_card(cur, pid, info, incoming):
    cur.execute('SELECT pid,' + ','.join(COLUMNS) + ' FROM vault.person WHERE pid=%s', (pid,))
    row = cur.fetchone()
    if row is None:
        return None
    person = dict(zip(('pid',) + COLUMNS, row))
    cur.execute("""SELECT doc_id, role, status FROM vault.person_mention WHERE pid=%s
                   AND status IN ('linked','created','manual_linked','manual_created') AND owner_key NOT LIKE 'orphan|%%'
                   ORDER BY doc_id""", (pid,))
    mentions = cur.fetchall()
    compare = []
    for c in COLUMNS:
        mine, theirs = set(incoming.get(c) or []), set(person[c] or [])
        if mine or theirs:
            state = 'same' if mine & theirs else 'different' if mine and theirs else 'missing'
            compare.append({'label': LABEL_KO[COLUMN_LABEL[c]], 'state': state,
                            'mine': [info.get(t, {}).get('value', '?') for t in sorted(mine)],
                            'theirs': [info.get(t, {}).get('value', '?') for t in sorted(theirs)]})
    return {'pid': pid, 'values': _decode(person, info), 'compare': compare,
            'roles': sorted({m[1] for m in mentions if m[1]}), 'documents': sorted({m[0] for m in mentions})}


def detail(mention_id):
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute(MENTION_SELECT + ' WHERE mention_id=%s', (mention_id,))
        rows = _rows(cur, MENTION_KEYS)
        if not rows:
            raise ReviewError('검토 항목을 찾지 못했습니다. 목록을 새로 고치세요.')
        m = rows[0]
        kind = 'feature' if m['owner_key'].startswith('orphan|') else 'person'
        mine = set(_tokens(m))
        candidates, targets = [], []
        if kind == 'person':
            # 자동 판정과 같은 기준: 어느 하나라도 토큰이 겹치는 PID
            cur.execute('SELECT pid FROM vault.person WHERE ' +
                        ' OR '.join(f'{c} && %s::text[]' for c in COLUMNS) + ' ORDER BY pid',
                        [list(mine)] * len(COLUMNS))
            pids = [r[0] for r in cur.fetchall()]
            if m['pid'] and m['pid'] not in pids:
                pids.append(m['pid'])
        else:
            cur.execute(MENTION_SELECT + """ WHERE doc_id=%s AND owner_key NOT LIKE 'orphan|%%' ORDER BY mention_id""",
                        (m['doc_id'],))
            targets = _rows(cur, MENTION_KEYS)
            pids = []
        cur.execute('SELECT ' + ','.join(COLUMNS) + ' FROM vault.person WHERE pid = ANY(%s)', (pids,))
        all_tokens = mine | {t for row in cur.fetchall() for arr in row for t in (arr or [])} | \
            {t for x in targets for t in _tokens(x)}
        info = _token_info(cur, all_tokens)
        for pid in pids:
            card = _person_card(cur, pid, info, m)
            if card:
                card['is_current'] = pid == m['pid']
                candidates.append(card)
        shared = _shared_tokens(cur)
        document = _document(m['doc_id'], cur)
    return {'mention': {**{k: m[k] for k in ('mention_id', 'doc_id', 'role', 'status', 'reason', 'pid', 'decided_by', 'review_note')},
                        'kind': kind, 'values': _decode(m, info), 'tokens': sorted(mine),
                        'shared_tokens': sorted(mine & shared),
                        'updated_at': m['updated_at'].isoformat(),
                        'reviewed_at': m['reviewed_at'].isoformat() if m['reviewed_at'] else None},
            'candidates': candidates,
            'targets': [{'mention_id': x['mention_id'], 'name': info.get(x['name_token'], {}).get('value'),
                         'role': x['role'], 'pid': x['pid'], 'status': x['status']} for x in targets],
            'document': document}


def _add_to_person(cur, pid, row, shared):
    for c in COLUMNS:
        values = sorted(set(row.get(c) or []) - shared)
        if values:
            cur.execute(f'UPDATE vault.person SET {c}=ARRAY(SELECT DISTINCT unnest(coalesce({c},%s::text[]) || %s::text[]) ORDER BY 1) WHERE pid=%s',
                        ([], values, pid))


def _remove_from_person(cur, pid, row, mention_id):
    """되돌릴 때: 같은 PID의 다른 등장 기록에 없는 토큰만 뺍니다. 남는 토큰이 없으면 PID를 지웁니다."""
    cur.execute("SELECT " + ','.join(COLUMNS) + """ FROM vault.person_mention WHERE pid=%s AND mention_id<>%s
                   AND status IN ('linked','created','manual_linked','manual_created')""", (pid, mention_id))
    keep = {c: set() for c in COLUMNS}
    for other in cur.fetchall():
        for c, arr in zip(COLUMNS, other):
            keep[c] |= set(arr or [])
    cur.execute('SELECT ' + ','.join(COLUMNS) + ' FROM vault.person WHERE pid=%s FOR UPDATE', (pid,))
    current = cur.fetchone()
    if current is None:
        return False
    remaining = [sorted(set(arr or []) - (set(row.get(c) or []) - keep[c])) for c, arr in zip(COLUMNS, current)]
    if not any(remaining):
        cur.execute('DELETE FROM vault.person WHERE pid=%s', (pid,))
        return True
    cur.execute('UPDATE vault.person SET ' + ','.join(f'{c}=%s' for c in COLUMNS) + ' WHERE pid=%s', remaining + [pid])
    return False


def decide(mention_id, action, expected_updated_at, pid=None, target_mention_id=None, note=None):
    """관리자 판정. expected_updated_at 으로 화면을 연 뒤 다른 변경이 있었는지 확인합니다."""
    note = (note or '').strip()[:500] or None
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='5s'")
        cur.execute('SELECT pg_try_advisory_xact_lock(721121102)')
        if not cur.fetchone()[0]:
            raise ReviewError('인물사전 생성·반영 작업이 진행 중입니다. 완료 후 다시 시도하세요.')
        cur.execute(MENTION_SELECT + ' WHERE mention_id=%s FOR UPDATE', (mention_id,))
        rows = _rows(cur, MENTION_KEYS)
        if not rows:
            raise ReviewError('검토 항목을 찾지 못했습니다.')
        m = rows[0]
        if m['updated_at'].isoformat() != expected_updated_at:
            raise ReviewError('화면을 연 뒤 이 항목이 바뀌었습니다. 다시 열어 확인하세요.')
        kind = 'feature' if m['owner_key'].startswith('orphan|') else 'person'
        shared = _shared_tokens(cur)
        if action == 'undo':
            if m['decided_by'] != 'manual':
                raise ReviewError('수동 판정한 항목만 되돌릴 수 있습니다.')
            # 등장 기록을 먼저 보류로 돌린 뒤 PID 정리 (PID 삭제 시 FK가 연결 상태 행을 건드리지 않도록)
            cur.execute("""UPDATE vault.person_mention SET status='held', decided_by='auto', pid=NULL,
                           reason='MANUAL_UNDONE', review_note=%s, reviewed_at=now() WHERE mention_id=%s""", (note, mention_id))
            removed = _remove_from_person(cur, m['pid'], m, mention_id) if m['pid'] and m['status'] in ACTIVE else False
            return {'mention_id': mention_id, 'doc_id': m['doc_id'], 'status': 'held', 'person_deleted': removed}
        if m['status'] != 'held':
            raise ReviewError('보류 상태인 항목만 판정할 수 있습니다. 수동 판정을 바꾸려면 먼저 되돌리세요.')
        if kind == 'person' and action not in PERSON_ACTIONS or kind == 'feature' and action not in FEATURE_ACTIONS:
            raise ReviewError('이 항목에 할 수 없는 처리입니다.')
        if action == 'link':
            if not isinstance(pid, int):
                raise ReviewError('연결할 PID를 선택하세요.')
            cur.execute('SELECT 1 FROM vault.person WHERE pid=%s FOR UPDATE', (pid,))
            if cur.fetchone() is None:
                raise ReviewError('선택한 PID가 없습니다.')
            _add_to_person(cur, pid, m, shared)
            status, reason, target = 'manual_linked', 'MANUAL_LINK', pid
        elif action == 'create':
            values = [sorted(set(m.get(c) or []) - shared) for c in COLUMNS]
            if not any(values):
                raise ReviewError('공유 값을 빼면 남는 정보가 없어 새 PID를 만들 수 없습니다.')
            cur.execute('INSERT INTO vault.person(' + ','.join(COLUMNS) + ') VALUES(' + ','.join(['%s'] * 7) + ') RETURNING pid', values)
            target = cur.fetchone()[0]
            status, reason = 'manual_created', 'MANUAL_CREATE'
        elif action == 'assign':
            if not isinstance(target_mention_id, int):
                raise ReviewError('정보를 귀속할 인물을 선택하세요.')
            cur.execute("""SELECT pid, status FROM vault.person_mention WHERE mention_id=%s AND doc_id=%s
                           AND owner_key NOT LIKE 'orphan|%%'""", (target_mention_id, m['doc_id']))
            found = cur.fetchone()
            if not found:
                raise ReviewError('같은 문서의 인물만 선택할 수 있습니다.')
            if found[0] is None or found[1] not in ACTIVE:
                raise ReviewError('선택한 인물에 PID가 없습니다. 먼저 인물 보류 목록에서 그 인물을 처리하세요.')
            target = found[0]
            if set(_tokens(m)) & shared:
                raise ReviewError('공유 값으로 표시된 정보는 개인에게 귀속할 수 없습니다.')
            _add_to_person(cur, target, m, shared)
            status, reason = 'manual_linked', 'MANUAL_ASSIGN'
        elif action == 'shared':
            status, reason, target = 'shared', 'MANUAL_SHARED', None
        else:
            status, reason, target = 'dismissed', 'MANUAL_DISMISS', None
        cur.execute("""UPDATE vault.person_mention SET status=%s, reason=%s, pid=%s, decided_by='manual',
                       review_note=%s, reviewed_at=now() WHERE mention_id=%s""", (status, reason, target, note, mention_id))
        return {'mention_id': mention_id, 'doc_id': m['doc_id'], 'status': status, 'pid': target}
