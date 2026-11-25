"""기존 탐지 결과의 문장·행 관계로 인물별 정보를 보수적으로 묶습니다.

entities/person_id 등 정답 라벨은 입력받지 않습니다. 외부 LLM도 호출하지 않습니다.
관계가 모호하면 이름에 임의로 붙이지 않고 미배정으로 남깁니다.
"""
import re
from .person_rules import LABEL_TO_COLUMN, COLUMNS, clean_values

ROLE_BEFORE = re.compile(r'(고객|신청인|신청자|대상\s*고객|상담|상담원|담당자|담당|심사역|직원|위임자|대리인|수취인|송금인|대표자|지원자|작성자|소명자|확인자|처리자|기안자|요청자\s*성명)\s*[:：]?\s*$')
CUSTOMER_AFTER = re.compile(r'^\s*(?:고객|님\s*귀하)')
EMPLOYEE_AFTER = re.compile(r'^\s*(?:책임|과장|대리|부장|팀장|사원|주임|심사역|상담원)')
INHERIT_LABEL = re.compile(r'^\s*(?:[-·]\s*)?(?:본인\s*확인|주민등록번호|휴대전화|전화번호|연락처|이메일|출금\s*계좌|배송지|청구서\s*주소|카드번호|발급\s*카드번호|결과\s*:)')
GAP = re.compile(r'^[\s()（）\[\]:：/,·.\-]*(?:(?:님|씨|고객|과장|대리|책임|사원|부장|팀장|주임|의|와|및|주민등록번호|휴대전화|전화번호|전화|연락처|번호|이메일|계좌번호|카드번호|카드|값|[0-9]+)[\s()（）\[\]:：/,·.\-]*)*$')


def role_for(text, mention, start, end):
    suffix = text[mention['end']:min(end,mention['end']+18)]
    if CUSTOMER_AFTER.match(suffix):return '고객/신청인'
    before = text[max(start,mention['start']-32):mention['start']]
    match = ROLE_BEFORE.search(before)
    if match:
        value = re.sub(r'\s+','',match[1])
        if value in ('고객','대상고객','신청인','신청자'):return '고객/신청인'
        if value in ('상담','상담원','담당자','담당','심사역','직원','작성자','확인자','처리자'):return '직원/담당자'
        return value
    if EMPLOYEE_AFTER.match(suffix):return '직원/담당자'
    return '역할 미확인'


# 문서 전체에서 역할이 하나뿐인 인물에게만 귀속하는 보조 규칙 (이름이 없는 항목 줄·화자 줄)
PERSONAL_FIELD = re.compile(r'^\s*(?:[-·•]\s*)?(?:본인\s*)?(?:주민등록번호|주민번호|휴대전화|휴대폰|전화번호|연락처|이메일|e-?mail|출금\s*계좌|입금\s*계좌|환급\s*계좌|계좌번호|카드번호|발급\s*카드번호|주소|거주지|배송지|청구서\s*주소)\s*[:：]', re.I)
REPRESENTATIVE_FIELD = re.compile(r'^\s*(?:[-·•]\s*)?대표자\s*(?:연락처|휴대전화|전화번호|이메일|주민등록번호)\s*[:：]')
CUSTOMER_SPEAKER = re.compile(r'^\s*(?:고객|신청인|신청자)\s*[:：]')
NOT_PERSONAL = re.compile(r'대표\s*번호|대표\s*전화|고객\s*센터|콜\s*센터|회사|법인|사업장|가맹점|본사|지점|기관|센터|팀\b|부서|담당\s*부서')


def _document_role_fallback(mentions, unassigned):
    """줄에 이름이 없어 주인을 못 정한 정보 중, 항목 이름이나 화자가 역할을 분명히 가리키고
    그 역할의 인물이 문서 전체에서 정확히 한 사람일 때만 귀속합니다. 회사·대표번호 표지가 있으면 귀속하지 않습니다."""
    def only(role):
        found={(m['token'],m['role']) for m in mentions if m['role']==role}
        if len(found)!=1:return None
        token=next(iter(found))[0]
        return next(m for m in mentions if m['token']==token and m['role']==role)
    kept=[]
    for item in unassigned:
        line=item.pop('_text');record=item.pop('_record')
        owner=None
        if not NOT_PERSONAL.search(line):
            if REPRESENTATIVE_FIELD.match(line):owner=only('대표자')
            elif CUSTOMER_SPEAKER.match(line) or PERSONAL_FIELD.match(line):owner=only('고객/신청인')
        if owner is None:
            kept.append(item)
        else:
            owner['features'].append(record)
            owner['evidence'].append('single_role_in_document')
    return kept


def group_records(text, records):
    lines=[]; pos=0
    for line in text.splitlines(keepends=True):
        lines.append((pos,pos+len(line),line))
        pos+=len(line)
    mentions=[]
    for r in records:
        if r['label']!='person':continue
        line_no=next((i for i,(a,b,_) in enumerate(lines) if a<=r['start']<b),None)
        if line_no is None:continue
        a,b,_=lines[line_no]
        mentions.append({'record':r,'start':r['start'],'end':r['end'],'token':r['token'],
                         'role':role_for(text,r,a,b),'line':line_no,'features':[], 'evidence':['name_mention']})
    unassigned=[]
    active=None
    for index,(a,b,line) in enumerate(lines):
        names=[m for m in mentions if m['line']==index]
        identity={(m['token'],m['role']) for m in names}
        features=[r for r in records if r['label']!='person' and a<=r['start']<b]
        for r in features:
            owner=None; evidence=None
            if len(identity)==1:
                owner=min(names,key=lambda m:abs(m['start']-r['start']))
                evidence='single_named_owner_in_line'
            elif names:
                previous=[m for m in names if m['end']<=r['start']]
                if previous:
                    candidate=max(previous,key=lambda m:m['end'])
                    gap=text[candidate['end']:r['start']]
                    for v in sorted(records,key=lambda z:z['start'],reverse=True):
                        if candidate['end']<=v['start'] and v['end']<=r['start']:
                            rel_a=v['start']-candidate['end'];rel_b=v['end']-candidate['end']
                            gap=gap[:rel_a]+' 값 '+gap[rel_b:]
                    if len(gap)<=90 and GAP.fullmatch(gap):
                        owner=candidate;evidence='explicit_adjacent_contact'
            elif INHERIT_LABEL.match(line) and active is not None:
                owner=active;evidence='labelled_continuation'
            # 우편 수신자: 주소 바로 다음 줄에 '이름 님 귀하'가 명시된 경우만 연결.
            if owner is None and r['label']=='address':
                following=[m for m in mentions if m['line']==index+1]
                if len({(m['token'],m['role']) for m in following})==1:
                    candidate=following[0]
                    if re.match(r'^\s*님\s*귀하',text[candidate['end']:lines[index+1][1]]):
                        owner=candidate;evidence='next_line_addressee'
            if owner is None:
                unassigned.append({'label':r['label'],'token':r['token'],'line':index+1,'reason':'OWNER_AMBIGUOUS','_record':r,'_text':line})
            else:
                owner['features'].append(r)
                owner['evidence'].append(evidence)
        if len(identity)==1:
            active=names[-1]
        elif names:
            active=None
        elif not line.strip() or (not features and not INHERIT_LABEL.match(line)):
            # 빈 줄은 구역이 바뀐 것으로 보고 앞 인물의 항목 상속을 끊습니다.
            active=None

    unassigned=_document_role_fallback(mentions,unassigned)
    buckets={}
    for m in mentions:
        group=buckets.setdefault((m['token'],m['role']),[])
        group.append(m)
    # 같은 이름의 역할 미확인 언급을 유일한 명시 역할에만 연결합니다.
    for key in list(buckets):
        if key[1]!='역할 미확인':continue
        known=[k for k in buckets if k[0]==key[0] and k[1]!='역할 미확인']
        if len(known)==1:
            buckets[known[0]].extend(buckets.pop(key))
    subjects=[]
    for (token,role),items in buckets.items():
        values={c:[] for c in COLUMNS}; checks=[]
        values['data2']=[token]
        phone_sets=set()
        for m in items:
            checks.append(m['record'])
            checks.extend(m['features'])
            phones=tuple(sorted({r['token'] for r in m['features'] if r['label']=='phone_number'}))
            if phones:phone_sets.add(phones)
            for r in m['features']:
                if r['label'] in LABEL_TO_COLUMN:
                    values[LABEL_TO_COLUMN[r['label']]].append(r['token'])
        subjects.append({'owner_key':f'{token}|{role}','name_token':token,'role':role,'values':clean_values(values),
            'lines':sorted({m['line']+1 for m in items}),'evidence':sorted({e for m in items for e in m['evidence']}),
            'force_hold':'LOCAL_IDENTITY_AMBIGUOUS' if len(phone_sets)>1 else None,'checks':checks})
    return subjects,unassigned
