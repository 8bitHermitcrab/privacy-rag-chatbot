"""인물 연결 정책. DB·외부 API를 사용하지 않는 순수 판단 함수."""
LABEL_TO_COLUMN = {
    'phone_number':'data1', 'person':'data2', 'email':'data3', 'address':'data4',
    'resident_registration_number':'data5', 'account_number':'data6', 'card_number':'data7',
}
COLUMNS = tuple(f'data{i}' for i in range(1,8))
REASONS = {
    'NO_CANDIDATE':'기존 후보가 없어 신규 PID 생성',
    'NAME_PHONE_MATCH':'이름·전화번호가 일치하는 후보 1개에 연결',
    'RRN_MATCH':'이름·주민번호가 일치하는 후보 1개에 연결',
    'NAME_ONLY':'이름만 일치하여 연결 근거 부족',
    'PHONE_DIFFERENT':'이름은 같지만 전화번호가 달라 보류',
    'MULTIPLE_MATCHES':'자동 연결 조건을 만족하는 PID가 여러 개',
    'RRN_CONFLICT':'주민번호가 충돌하여 보류',
    'IDENTITY_CONFLICT':'식별 근거가 서로 다른 PID를 가리킴',
    'OTHER_SHARED_VALUE':'다른 정보는 겹치지만 동일인 근거 부족',
    'LOCAL_IDENTITY_AMBIGUOUS':'같은 문서에서 동명이인·복수 연락처 관계가 모호함',
    'OWNER_AMBIGUOUS':'개인정보의 주인을 특정하지 못함',
    'TOKEN_MAPPING_MISSING':'기존 token_map에 해당 토큰이 없음',
    'TOKEN_MAPPING_CONFLICT':'기존 토큰의 원문 정규화 값 또는 종류가 다름',
    'DOCUMENT_MASK_MISMATCH':'현재 탐지 결과와 DB 가명처리 본문이 달라 보류',
    'NO_PERSON_DETECTED':'현재 탐지기가 이름을 찾지 못함',
    'ALREADY_LINKED':'같은 문서·근거·정책으로 이미 연결되어 재사용',
}


def clean_values(row):
    result = {}
    for col in COLUMNS:
        value = row.get(col)
        if isinstance(value,str):
            value = [value]
        result[col] = sorted({v for v in (value or []) if isinstance(v,str) and v.strip()})
    return result


PROFILES = {
    'strict': '엄격: 이름과 주민번호 일치',
    'balanced': '기본: 이름과 주민번호 또는 전화번호 일치',
    'relaxed': '완화: 기본 조건 또는 이름과 이메일·계좌·카드 중 두 종류 일치',
}
REASONS.update({
    'POLICY_INSUFFICIENT':'선택한 민감도의 연결 근거가 부족함',
    'SECONDARY_MATCH':'이름과 보조 정보 두 종류가 일치함',
    'ROLE_CONFLICT':'이전 연결 기록의 역할과 달라 보류',
    'NAME_REQUIRED':'이름 토큰이 없어 보류',
    'MULTIPLE_RRN':'한 인물 묶음에 주민번호가 여러 개라 보류',
    'HOMONYM_RRN_DIFFERENT':'이름은 같지만 주민번호가 모든 후보와 달라 다른 사람으로 새 PID 생성',
    'NEW_NAME_SHARED_ADDRESS':'처음 나온 이름이며 주소만 다른 인물과 같아 새 PID 생성',
    'MANUAL_DECISION':'관리자가 수동으로 판정한 항목이라 자동 판정을 건너뜀',
})
POLICY_VERSION = 'pid-rules-2026-11-21-v3'


def decide(values, people, sensitivity='balanced', role=None, known_roles=None):
    """새로 나온 인물 묶음을 기존 PID에 연결할지, 새 PID를 만들지, 보류할지 정합니다.

    v3 변경점
    - 한 묶음에 주민번호가 둘 이상이면 보류 (한 사람은 주민번호가 하나)
    - 이름이 같아도 모든 동명 후보와 주민번호가 다르고 다른 일치 근거가 없으면 새 PID (동명이인)
    - 주민번호로 연결할 때는 전화번호가 달라도 연결 (복수 연락처 허용). 그 외 근거는 기존처럼 보류
    - 엄격 모드에서는 전화번호 후보 수를 판단에 쓰지 않음
    - 다른 PID가 이미 가진 전화·이메일·계좌·카드 값은 연결 대상에 추가하지 않음 (exclude)
    - 이름 후보가 없고 겹치는 값이 주소뿐이면 새 PID (가족·같은 건물)
    """
    if sensitivity not in PROFILES:
        raise ValueError('민감도 설정을 확인하세요.')
    incoming = {c:set(v) for c,v in clean_values(values).items()}
    candidates = [p for p in people if any(incoming[c] & set(p.get(c) or []) for c in COLUMNS)]
    ids = [p['pid'] for p in candidates]
    def hold(reason):
        return {'action':'hold','reason':reason,'pid':None,'candidates':ids}
    if not incoming['data2']:
        return hold('NAME_REQUIRED')
    if len(incoming['data5'])>1:
        # 한 사람은 주민번호가 하나뿐이라 어느 값이 맞는지 알 수 없습니다(계좌번호 오탐 등).
        # 주민번호는 저장·연결 근거에서 빼고 나머지 정보로만 판단합니다.
        reduced={**{c:sorted(v) for c,v in incoming.items()},'data5':[]}
        result=decide(reduced,people,sensitivity,role,known_roles)
        result.setdefault('exclude',{})['data5']=sorted(incoming['data5'])
        result['rrn_ambiguous']=True
        return result
    if not candidates:
        return {'action':'create','reason':'NO_CANDIDATE','pid':None,'candidates':[]}
    names = [p for p in candidates if incoming['data2'] & set(p.get('data2') or [])]
    rrns = [p for p in candidates if incoming['data5'] & set(p.get('data5') or [])]
    phones = [p for p in names if incoming['data1'] & set(p.get('data1') or [])]
    use_phone = sensitivity!='strict'
    if len(rrns)>1 or (use_phone and len(phones)>1):
        return hold('MULTIPLE_MATCHES')
    if rrns and (rrns[0] not in names or (use_phone and phones and phones[0]['pid']!=rrns[0]['pid'])):
        return hold('IDENTITY_CONFLICT')
    matches=[]
    for p in names:
        rrn=bool(incoming['data5'] & set(p.get('data5') or []))
        phone=bool(incoming['data1'] & set(p.get('data1') or []))
        secondary=sum(bool(incoming[c] & set(p.get(c) or [])) for c in ('data3','data6','data7'))
        if rrn or (use_phone and phone) or (sensitivity=='relaxed' and secondary>=2):
            matches.append((p,'RRN_MATCH' if rrn else 'NAME_PHONE_MATCH' if phone else 'SECONDARY_MATCH'))
    if len(matches)>1:
        return hold('MULTIPLE_MATCHES')
    if matches:
        target,reason=matches[0]
        if incoming['data5'] and target.get('data5') and not incoming['data5'] <= set(target['data5']):
            return hold('RRN_CONFLICT')
        # 주민번호가 일치하면 다른 전화번호는 추가 연락처로 봅니다. 그 밖의 근거에서는 확인을 기다립니다.
        if reason!='RRN_MATCH' and incoming['data1'] and target.get('data1') and not incoming['data1'] & set(target['data1']):
            return hold('PHONE_DIFFERENT')
        roles=set((known_roles or {}).get(target['pid'], []))-{'역할 미확인'}
        if role and role!='역할 미확인' and roles and role not in roles:
            return hold('ROLE_CONFLICT')
        others={c:set() for c in ('data1','data3','data6','data7')}
        for p in people:
            if p['pid']!=target['pid']:
                for c in others: others[c] |= set(p.get(c) or [])
        exclude={c:sorted(incoming[c] & others[c]) for c in others if incoming[c] & others[c]}
        result={'action':'merge','reason':reason,'pid':target['pid'],'candidates':ids}
        if exclude: result['exclude']=exclude
        return result
    if any(incoming['data1'] and p.get('data1') and not incoming['data1'] & set(p['data1']) for p in names) and not incoming['data5']:
        return hold('PHONE_DIFFERENT')
    if any(incoming['data5'] and p.get('data5') and not incoming['data5'] & set(p['data5']) for p in names):
        # 동명 후보 모두 주민번호가 있고 모두 다르며, 전화 등 다른 일치 근거가 없으면 다른 사람(동명이인)으로 새 PID
        if names and all(p.get('data5') for p in names) and not rrns and not phones:
            return {'action':'create','reason':'HOMONYM_RRN_DIFFERENT','pid':None,'candidates':ids}
        return hold('RRN_CONFLICT')
    if names:
        return hold('NAME_ONLY' if not any(incoming[c] for c in COLUMNS if c!='data2') else 'POLICY_INSUFFICIENT')
    shared=[c for c in COLUMNS if any(incoming[c] & set(p.get(c) or []) for p in candidates)]
    if shared==['data4']:
        return {'action':'create','reason':'NEW_NAME_SHARED_ADDRESS','pid':None,'candidates':ids}
    return hold('OTHER_SHARED_VALUE')
