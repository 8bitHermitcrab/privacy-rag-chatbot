"""규칙 기반 개인정보 탐지기 (정규식 + 문맥 규칙 + Kiwi 형태소 분석 선택 사용)."""
import re

# ---------------------------------------------------------------- 1. 숫자·형식 개인정보 (정규식)
# 우선순위: 앞에 있는 라벨이 먼저 자리를 차지하고, 겹치는 뒤 라벨은 버림
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RRN = re.compile(r"(?<![\d-])\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[- ]?[1-8]\d{6}(?![\d-])")
CARD = re.compile(r"(?<![\d-])\d{4}([- ]?)\d{4}\1\d{4}\1\d{4}(?![\d-])")
# 휴대폰 010/011/016~019, 서울 02, 지역 031~064, 인터넷 070
# 1588·1599 같은 회사 대표번호는 0으로 시작하지 않으므로 자연스럽게 빠짐
PHONE = re.compile(r"(?<![\d.-])(?:01[016789]|02|0[3-6][1-5]|070)([-. ]?)\d{3,4}\1\d{4}(?![\d-])")
ACCOUNT = re.compile(r"(?<![\d-])\d{3,6}-\d{2,6}-\d{2,7}(?:-\d{1,3})?(?![\d-])")
DATE_LIKE = re.compile(r"^(?:19|20)\d{2}-\d{1,2}-\d{1,2}$")

SIDO = (r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시"
        r"|경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|전라남도|경상북도|경상남도|제주특별자치도"
        r"|서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)")
ADDRESS = re.compile(
    SIDO
    + r"(?:\s+[가-힣]+(?:시|군|구))*"            # 시·군·구 (여러 단계)
    + r"(?:\s+[가-힣]+(?:읍|면))?"
    + r"\s+[가-힣0-9]+(?:로|길)(?:\s*\d+번?길)?"  # 도로명
    + r"\s*\d+(?:-\d+)?"                          # 건물번호
    + r"(?:\s*,?\s*(?:\d+동\s*)?(?:지하\s*)?\d+(?:호|층))?"  # 상세주소
)

# ---------------------------------------------------------------- 2. 이름 (성씨 목록 + 조사·호칭 문맥)
# 데이터셋 인물 목록(personas.json)은 쓰지 않음 → 일반 성씨 목록만 사용
SURNAMES = set("김이박최정강조윤장임한오서신권황안송류유홍전고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어편용예봉")
COMPOUND = ["남궁", "황보", "제갈", "선우", "독고", "사공", "서문"]

# 이름 바로 뒤에 붙을 수 있는 것 (호칭 + 조사)
SUFFIX = re.compile(
    r"^(?:님|씨|군|양)?"
    r"(?:께서|에게서|에게|한테|이랑|랑|이가|이는|이를|이의|이와|이도|이고|이며|이었|이라|입니다|이에요|"
    r"은|는|이|가|을|를|의|께|와|과|도|만|으로|로|아|야)?$")
# 이름 뒤 다음 단어가 이것이면 사람일 가능성이 높음 (호칭·직급·역할)
TITLES = ("고객", "님", "씨", "대리", "과장", "차장", "부장", "팀장", "사원", "주임", "선임", "책임", "수석",
          "매니저", "실장", "본부장", "이사", "대표", "상담원", "드림", "올림", "선생", "교수", "변호사", "원장", "점장", "소장")
TITLE_WORDS = set(TITLES)
# 이름 앞에 오면 사람일 가능성이 높은 표현 ('성명:', '담당자:' 등)
LEFT_LABEL = re.compile(
    r"(?:^|[^가-힣])(?:성명|이름|고객명|고객|상담원|상담자|담당자|담당|처리자|처리 담당|보고자|작성자|민원인|대상|참석자|참석|불참|"
    r"점검자|검토자|신청인|수령인|예금주|대표자|발신|수신|갑|을)\s*[:：]\s*$"   # 콜론이 있어야 '항목명: 값'으로 봄
    r"|대표이사\s+$")
ORG_WORD = re.compile(r"[가-힣A-Za-z]+(?:팀|센터|지점|본부|부서|사업부)\s+$")   # 'IT운영팀 이준우'
# 성씨 글자로 시작하는 흔한 일반 명사 (문서 양식 항목명 등). 데이터셋 인물 이름이 아니라 일반어만 넣음
# ⚠ 이 목록은 dev 오탐을 보고 만든 것이라 dev에 맞춰져 있음 → holdout에서는 새 단어로 오탐이 생길 수 있음
#   (Kiwi를 켜면 일반명사(NNG)로 분석되는 후보를 자동으로 거르므로 이 목록 의존도가 줄어듦)
STOPWORDS = set("""고객명 연락처 이메일 민원인 유형별 담당자 처리자 작성자 점검자 참석자 검토자 보고자 상담원 상담자
신청서 신청인 전화번호 주소지 정보를 정보가 서비스 문의함 문의는 안내함 설명함 신고함 공유함 요청함
이용자 이름은 성명은 전체에 정책상 주소가 연락처는""".split())
HANGUL = re.compile(r"[가-힣]")


def _rest(text, end):
    """이름 후보 뒤에 붙은 한글 덩어리 (조사·호칭 판단용)."""
    j = end
    while j < len(text) and HANGUL.match(text[j]):
        j += 1
    return text[end:j], j


def _next_word(text, j):
    m = re.match(r"\s*\(?([가-힣]+)", text[j:])
    return m.group(1) if m else ""


def _strong_context(text, s, e):
    """앞뒤 문맥만으로 '사람 이름'이라고 볼 근거가 있는가."""
    rest, j = _rest(text, e)
    if rest.startswith(("님", "씨")):
        return True
    nxt = _next_word(text, j)
    if not rest and any(nxt.startswith(t) for t in TITLES if t not in ("드림", "올림")):
        return True
    if not rest and re.match(r"\s*(드림|올림)\s*$", text[j:].split("\n")[0]):   # 메일 서명 '최혜진 드림'
        return True
    line_start = text.rfind("\n", 0, s) + 1
    if not rest and s == line_start and text[j:j + 1] == ":":   # 메신저 화자 '김서연: ...' (줄 맨 앞)
        return True
    left = text[line_start:s]
    if LEFT_LABEL.search(left):
        return True
    if ORG_WORD.search(left):                      # 'IT운영팀 이준우', '고객보호팀 최지영'
        return True
    if re.search(r"(?:성명|참석자?|불참|점검자|참석)\s*[:：].*,\s*$", left):  # 이름 나열 '정서현, 이서연'
        return True
    return False


def _candidates(text):
    """(start, end, kind, surname, given)  kind: full | spaced"""
    out = []
    sur_alt = "|".join(COMPOUND) + "|[" + "".join(sorted(SURNAMES)) + "]"
    for m in re.finditer(rf"(?<![가-힣])({sur_alt})([가-힣]{{2}})", text):
        out.append((m.start(), m.end(), "full", m.group(1), m.group(2)))
    for m in re.finditer(rf"(?<![가-힣])({sur_alt}) ([가-힣]{{2}})", text):
        out.append((m.start(), m.end(), "spaced", m.group(1), m.group(2)))
    return out


def detect_person(text, taken, kiwi=None):
    """taken: 이미 다른 라벨이 차지한 구간 (주소 속 '온누리로' 같은 오탐 방지)."""
    def free(s, e):
        return not any(s < te and ts < e for ts, te in taken)

    nnp, nng = set(), set()
    if kiwi is not None:   # 형태소 분석: 고유명사(NNP) / 일반명사(NNG)로 통째로 분석된 구간
        for t in kiwi.tokenize(text):
            if t.tag == "NNP":
                nnp.add((t.start, t.start + t.len))
            elif t.tag == "NNG" and t.len >= 3:
                nng.add((t.start, t.start + t.len))

    found, names = [], set()
    cands = [c for c in _candidates(text) if free(c[0], c[1])]
    for s, e, kind, sur, given in cands:
        rest, _ = _rest(text, e)
        if not SUFFIX.match(rest) or text[s:e].replace(" ", "") in STOPWORDS:
            continue
        if (s, e) in nng:          # '연락처', '이메일'처럼 Kiwi가 일반명사 한 덩어리로 본 후보는 버림
            continue
        if given in TITLE_WORDS or (given + rest)[:2] in TITLE_WORDS:   # '김 과장', '허 부장님'
            continue
        if _strong_context(text, s, e) or (kind == "full" and (s, e) in nnp):
            found.append((s, e))
            names.add(sur + given)

    # 문서 안 전파: 한 번 사람으로 확인된 이름은 문맥이 약한 다른 등장도 잡음
    for s, e, kind, sur, given in cands:
        rest, _ = _rest(text, e)
        if sur + given in names and SUFFIX.match(rest) and given not in TITLE_WORDS:
            found.append((s, e))

    # 이름만 부르는 경우: '지영 씨', '승현아', 확인된 이름의 뒷 두 글자
    for m in re.finditer(r"(?<![가-힣])([가-힣]{2}) ?씨(?:께서|에게|가|는|의|도|와)?(?![가-힣])", text):
        if m.group(1) not in TITLE_WORDS and free(m.start(1), m.end(1)):
            found.append((m.start(1), m.end(1)))
    if kiwi is not None:   # Kiwi가 고유명사로 본 두 글자 + 부르는 말 ('은서랑', '소율이랑')
        for t in kiwi.tokenize(text):
            if t.tag == "NNP" and t.len == 2 and free(t.start, t.start + t.len):
                after = text[t.start + t.len:t.start + t.len + 3]
                if re.match(r"(아|야|이랑|랑|이가|이는|씨)(?![가-힣])", after):
                    found.append((t.start, t.start + t.len))
    givens = {n[-2:] for n in names}
    for g in givens:
        for m in re.finditer(rf"(?<![가-힣]){g}(?=(아|야|이랑|랑|씨| 씨|님)(?![가-힣]))", text):
            if free(m.start(), m.end()):
                found.append((m.start(), m.end()))
    return sorted(set(found))


# ---------------------------------------------------------------- 3. 합치기
def detect(text, kiwi=None):
    ents, taken = [], []

    def add(label, s, e):
        if any(s < te and ts < e for ts, te in taken):
            return
        taken.append((s, e))
        ents.append({"label": label, "start": s, "end": e})

    for m in EMAIL.finditer(text):
        add("email", m.start(), m.end())
    for m in RRN.finditer(text):
        add("resident_registration_number", m.start(), m.end())
    for m in CARD.finditer(text):
        add("card_number", m.start(), m.end())
    for m in PHONE.finditer(text):
        add("phone_number", m.start(), m.end())
    for m in ACCOUNT.finditer(text):
        if not DATE_LIKE.match(m.group()):
            add("account_number", m.start(), m.end())
    for m in ADDRESS.finditer(text):
        add("address", m.start(), m.end())
    for s, e in detect_person(text, list(taken), kiwi):
        add("person", s, e)
    return sorted(ents, key=lambda x: x["start"])
