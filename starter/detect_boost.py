"""탐지 보강 후처리 (종합 단계)

어떤 탐지기(모델·규칙·LLM)의 결과 spans 에도 그대로 덧붙여 쓰는 후처리입니다.
    spans = enhance(text, spans)        # [{"label", "start", "end"}] -> 같은 형식

1) 주소   : 우편번호 '(05299)', 건물명 '○○아파트 104동 432호', '502호 (범어동)' 같은 참고항목까지 경계를 넓히고,
            도로명이 없는 지번 주소 '○○구 ○○동 325-6', '○○군 ○○읍 252-4' 를 새로 잡음
2) 이름   : (a) 규칙 탐지기의 '강한 문맥' 이름을 합침 (지금까지는 버려지고 있었음)
            (b) 외자 '박준', 띄어 쓴 외자 '박 준' — 호칭·직급·항목명 같은 강한 문맥이 있을 때만
            (c) 성 없이 이름만 '시우 드림', '시우 씨' — 서명·호칭이 있을 때만
3) 전파   : 문서 안에서 한 번 사람으로 확인된 이름은 띄어쓰기·조사가 달라도 다른 등장을 모두 잡음
            (쪼개짐의 주원인인 '어떤 데선 잡고 어떤 데선 놓침' 대응)

설계 원칙
  - 데이터셋 인물 목록은 쓰지 않음. 일반 성씨 목록 + 한국어 문서 관례(호칭·직급·서명·항목명)만 사용
  - 약한 근거 하나로는 이름으로 보지 않음 → 근거가 강한 것만 추가해 정밀도 손실을 줄임
  - 원문 위치를 바꾸지 않음 (모든 결과는 원문 기준 [start, end))
"""
import re

from .rules_detector import (COMPOUND, LEFT_LABEL, ORG_WORD, SIDO, STOPWORDS, SUFFIX, SURNAMES, TITLES,
                             detect_person, is_title_word)
from .rules_detector import _candidates as _rule_candidates

HANGUL = re.compile(r"[가-힣]")
SUR_ALT = "|".join(COMPOUND) + "|[" + "".join(sorted(SURNAMES)) + "]"
TITLE_ALT = "|".join(sorted((t for t in TITLES if t not in ("드림", "올림")), key=len, reverse=True))

# ================================================================ 1. 주소
_TAIL = (r"(?:\s+[가-힣A-Za-z0-9]+(?:아파트|빌라|맨션|오피스텔|타워|빌딩|하우스|타운|프라자|플라자|스퀘어))?"   # 건물명
         r"(?:\s*,?\s*(?:[A-Za-z]?\d+동\s*)?(?:지하\s*)?\d+(?:호|층)(?:\s*\d+호)?)?"                        # 동·호·층
         r"(?:\s*\([가-힣0-9]+(?:동|가|리)(?:,\s*[가-힣0-9]+)?\))?")                                      # 참고항목
_ZIP = r"(?:\(\d{5}\)\s*)?"
_SIGUNGU = r"(?:\s+[가-힣]+(?:시|군|구))*"
ADDR_ROAD = re.compile(_ZIP + SIDO + _SIGUNGU + r"(?:\s+[가-힣]+(?:읍|면))?"
                       r"\s+[가-힣0-9]+(?:로|길)(?:\s*\d+번?길)?\s*\d+(?:-\d+)?" + _TAIL)
ADDR_JIBUN = re.compile(_ZIP + SIDO + _SIGUNGU +
                        r"(?:\s+[가-힣]+(?:읍|면))?\s+[가-힣0-9]+(?:동|리|가)\s*(?:산\s*)?\d+(?:-\d+)?(?:번지)?" + _TAIL)
ADDR_EUP = re.compile(_ZIP + SIDO + _SIGUNGU + r"\s+[가-힣]+(?:읍|면)\s*(?:산\s*)?\d+(?:-\d+)?(?:번지)?" + _TAIL)
ZIP_LEFT = re.compile(r"\(\d{5}\)\s*$")
TAIL_RE = re.compile(_TAIL)


def _overlap(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


ADDR_HINT = re.compile(r"\d|(?:특별시|광역시|자치시|자치도|도|시|군|구|읍|면|동|리|가|로|길|번지|아파트|빌라|빌딩|타워|호|층)(?![가-힣])")


def _plausible_address(text, sp):
    """숫자도 주소 단위어(시·구·동·로·길…)도 없는 조각('선우')은 주소가 아님"""
    return bool(ADDR_HINT.search(text[sp["start"]:sp["end"]]))


def refine_addresses(text, spans):
    """주소 경계 보정 + 지번 주소 추가."""
    cut = []
    for sp in spans:                      # 빈 줄을 넘어간 주소는 빈 줄 앞에서 자름 ('…779-15⏎⏎선우')
        if sp["label"] == "address":
            k = text.find("\n\n", sp["start"], sp["end"])
            if k >= 0:
                sp = {**sp, "end": k}
            if not _plausible_address(text, sp):
                continue
        cut.append(sp)
    spans = cut
    full = []
    for rx in (ADDR_ROAD, ADDR_JIBUN, ADDR_EUP):
        full += [{"label": "address", "start": m.start(), "end": m.end()} for m in rx.finditer(text)]
    # 같은 위치에서 여러 패턴이 맞으면 가장 긴 것
    full.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    best = []
    for f in full:
        if not any(_overlap(f, b) for b in best):
            best.append(f)

    out = []
    for sp in spans:
        if sp["label"] != "address":
            out.append(sp)
            continue
        s, e = sp["start"], sp["end"]
        for f in best:                      # 규칙 전체 매치와 겹치면 둘을 합친 범위로 넓힘
            if _overlap(sp, f):
                s, e = min(s, f["start"]), max(e, f["end"])
        m = ZIP_LEFT.search(text[:s])       # 우편번호가 바로 앞에 있으면 포함
        if m:
            s = m.start()
        m = TAIL_RE.match(text, e)          # 동·호·층·참고항목이 뒤에 남아 있으면 포함
        if m and m.end() > e:
            e = m.end()
        while e > s and text[e - 1] in " ,\t":
            e -= 1
        out.append({**sp, "start": s, "end": e})
    # 기존 결과와 겹치지 않는 새 주소 추가
    for f in best:
        if not any(_overlap(f, o) for o in out):
            out.append(f)
    return _resolve(out)


# ================================================================ 2. 이름 근거
# 사람 이름 앞에 오는 역할어 (콜론 없이도): '고객 김민준의', '심사역 안윤이', '소속 박도윤이', 'Dear 이서연'
ROLE_LEFT = re.compile(
    r"(?:^|[^가-힣])(?:심사역|상담원|상담사|담당자|담당|직원|소속|신청인|민원인|대리인|위임인|수임인|송금인|"
    r"수취인|예금주|명의자|본인|성명|이름|Dear|To|Hi|Mr\.|Ms\.)\s*[:：]?\s*$")
# '고객' 은 '고객 신용도', '고객 변경' 처럼 일반어 앞에도 오므로, 이름 뒤에 조사·님이 붙었을 때만 근거로 씀 ('고객 김민준의')
ROLE_WEAK = re.compile(r"(?:^|[^가-힣])고객\s+$")
TITLE_SLASH = re.compile(rf"(?:{TITLE_ALT})\s*/\s*$")          # '고객센터 / 대리 / 김민준'
RIGHT_STRONG = re.compile(
    rf"^(?:(?:님|씨)(?:께서|에게|께|의|이|가|은|는|도|과|와)?(?![가-힣])"   # 님·씨
    rf"|[ \t]?(?:{TITLE_ALT})(?:님)?(?:께서|에게|께|은|는|이|가|을|를|의|과|와|도)?(?![가-힣])"   # 직급·호칭
    rf"|[ \t]+님?[ \t]*귀하"                                          # '정하은 님 귀하'
    rf"|[ \t]+(?:드림|올림)[ \t]*(?:\(|$|\n))")                      # 서명
# 외자 뒤 조사 ('박준이', '박준의'). 부르는 말 '아/야'는 넣지 않음: '김수아'를 '김수'+'아'로 자르는 오류 방지
JOSA = re.compile(r"^(?:께서|에게서|에게|한테|이랑|랑|이가|이는|이를|이의|은|는|이|가|을|를|의|께|와|과|도|만|으로|로)$")

# 성씨 글자로 시작하는 흔한 두 글자 일반어 — 외자·이름만 후보에서만 쓰는 차단 목록 (일반 어휘, 인물 이름 아님)
COMMON2 = set("""고객 공지 공유 공문 구분 구매 금액 기간 기준 기록 기타 김치 나중 남은 노력 도움 문의 문서 민원 반송 방문 배송 백업
변경 변동 보고 부서 부고 사용 사유 사항 서명 서비스 선택 성명 소속 소명 손님 송금 수신 승인 시간 신고 신규 신청 안내 양식
여부 연락 연체 영업 오류 오늘 오후 오전 왕복 우리 원본 위임 유의 유형 육아 이번 이상 이용 이후 인사 인증 임시 장인 장례
전체 전화 전원 정보 정상 정책 정리 제출 조치 조회 주소 주의 지급 지점 진행 차량 채권 채무 최근 최종 추가 출장 편의 표준
하루 한도 한번 함께 해외 허용 현재 홍보 확인 황금 회의 회원 선생 사장 기사 여러 반장 은행 우편 연휴 명의 방법 경우 모두""".split())
ROLE_WORDS = {"심사역", "상담원", "상담사", "담당자", "소명자", "신청자", "신청인", "민원인", "대리인", "위임인", "수임인",
              "송금인", "수취인", "예금주", "명의자", "작성자", "보고자", "점검자", "검토자", "참석자", "처리자"}
GIVEN_STOP = COMMON2 | {t for t in TITLES} | ROLE_WORDS | {"고객님", "여러분"}
ORG_END = re.compile(r"(?:팀|센터|본부|지점|부서|사업부|실|국|과|부)$")
# 외자 후보의 두 번째 글자가 동사 활용·조사 끝이면 일반어일 가능성이 큼 ('위한', '대한', '관한')
VERBISH_END = set("한된할될는은을의로서고며면게지기도만")


def _line_left(text, s):
    return text[text.rfind("\n", 0, s) + 1:s]


def _line_right(text, e):
    j = text.find("\n", e)
    return text[e:] if j < 0 else text[e:j]


def _table_cell_with_pii(text, s, e, others):
    """표 칸 '| 이름 |' 이고 같은 줄에 다른 개인정보(전화·이메일 등)가 있음."""
    if not (re.search(r"\|\s*$", _line_left(text, s)) and re.match(r"\s*\|", _line_right(text, e))):
        return False
    ls, le = text.rfind("\n", 0, s) + 1, len(text) if text.find("\n", e) < 0 else text.find("\n", e)
    return any(o["label"] != "person" and ls <= o["start"] and o["end"] <= le for o in others)


def left_strong(text, s, rest=""):
    left = _line_left(text, s)
    if LEFT_LABEL.search(left) or ORG_WORD.search(left) or ROLE_LEFT.search(left) or TITLE_SLASH.search(left):
        return True
    return bool(rest) and bool(ROLE_WEAK.search(left))


def right_strong(text, e):
    return bool(RIGHT_STRONG.match(text[e:]))


# ================================================================ 3. 이름 후보
def _single_char_names(text, others):
    """외자 '박준' / 띄어 쓴 외자 '박 준'."""
    out = []
    for m in re.finditer(rf"(?<![가-힣])({SUR_ALT})( ?)([가-힣])(?=[가-힣]*)", text):
        s, e, spaced = m.start(), m.end(), bool(m.group(2))
        after = re.match(r"[가-힣]*", text[e:]).group(0)
        word = text[s:e].replace(" ", "")
        if word in COMMON2 or word in STOPWORDS or is_title_word(word) or word[-1] in VERBISH_END:
            continue
        if spaced:
            # '이 건', '그 외' 같은 일반 표현과 구분: 바로 뒤에 님·씨·직급·호칭이 오거나,
            # '대상 고객: 박 준⏎' 처럼 항목명 뒤에 값으로 홀로 있을 때만
            if after and not after.startswith(("님", "씨")):
                continue
            alone = re.match(r"[ \t]*(?:$|\n|\(|,|/|\|)", text[e:])
            if right_strong(text, e) or (alone and LEFT_LABEL.search(_line_left(text, s))):
                out.append((s, e, "spaced1"))
            continue
        if after and not after.startswith(("님", "씨")) and not JOSA.match(after):
            continue                                     # '박준호' 같은 세 글자는 여기서 다루지 않음
        if after and JOSA.match(after):
            # '박준이' 는 세 글자 이름일 수도 있음 → 왼쪽 근거(항목명·역할어)가 있을 때만 외자+조사로 봄
            # 단 '전예은 책임'처럼 세 글자 전체 뒤에 직급·님이 오면 세 글자 이름으로 봄
            if right_strong(text, e + len(after)):
                continue
            if left_strong(text, s):
                out.append((s, e, "single"))
            continue
        # 조직명 왼쪽 근거('콜센터 위탁')만으로는 약함 → 외자는 오른쪽 근거나 항목명·역할어가 함께 있어야 함
        left = _line_left(text, s)
        left_ok = bool(LEFT_LABEL.search(left) or ROLE_LEFT.search(left) or TITLE_SLASH.search(left))
        # '처리: 고객보호팀 강혁⏎', '담당: 카드운영팀 송현 (내선…)' — 조직명 뒤에 값으로 홀로 있을 때는 인정
        alone = re.match(r"[ \t]*(?:$|\n|\(|,|/|\|)", text[e:])
        left_ok = left_ok or bool(alone and ORG_WORD.search(left))
        if right_strong(text, e) or left_ok or _table_cell_with_pii(text, s, e, others):
            out.append((s, e, "single"))
    return out


def _full_names(text, others):
    """세 글자(복성 네 글자) 이름 중 규칙 탐지기가 못 보는 문맥: 역할어 '송금인: ○○○', '고객 ○○○의', 표 칸."""
    out = []
    for s, e, kind, sur, given in _rule_candidates(text):
        rest = re.match(r"[가-힣]*", text[e:]).group(0)
        word = text[s:e].replace(" ", "")
        if (not SUFFIX.match(rest) or word in STOPWORDS or word in ROLE_WORDS or given in GIVEN_STOP
                or ORG_END.search(word) or is_title_word(word)):
            continue
        if left_strong(text, s, rest) or (not rest and _table_cell_with_pii(text, s, e, others)):
            out.append((s, e, "full"))
    return out


def _given_only_names(text):
    """성 없이 이름만: '인사팀 시우 드림', '시우 씨', '시우님'."""
    out = []
    for m in re.finditer(r"(?<![가-힣])([가-힣]{2})(?=[ \t]*(?:드림|올림)[ \t]*(?:\(|$|\n))", text, re.M):
        left = _line_left(text, m.start())
        if m.group(1) not in GIVEN_STOP and (not left.strip() or ORG_WORD.search(left)):
            out.append((m.start(), m.end(), "given"))
    for m in re.finditer(r"(?<![가-힣])([가-힣]{2})[ \t]?(?:씨|님)(?:께서|에게|께|의|이|가|은|는|도)?(?![가-힣])", text):
        if m.group(1) not in GIVEN_STOP:
            out.append((m.start(1), m.end(1), "given"))
    # 역할어 + 이름만: '상담사 재원: …', '담당자 시우님' (메신저·녹취 화자)
    for m in re.finditer(r"(?:^|[^가-힣])(?:상담사|상담원|담당자|심사역|직원|고객)[ \t]+([가-힣]{2})(?=[ \t]*:|[ \t]?(?:님|씨))",
                         text, re.M):
        if m.group(1) not in GIVEN_STOP:
            out.append((m.start(1), m.end(1), "given"))
    return out


# ================================================================ 4. 문서 단위 전파
def _propagate(text, names, given_tails):
    """확인된 이름의 다른 등장 (띄어쓰기·조사 변형 포함)."""
    out = []
    for n in names:
        sur = n[:2] if n[:2] in COMPOUND else n[:1]
        given = n[len(sur):]
        forms = {re.escape(n)}
        if given:
            forms.add(re.escape(sur) + r"[ \t]" + re.escape(given))
        for m in re.finditer(rf"(?<![가-힣])(?:{'|'.join(forms)})", text):
            after = re.match(r"[가-힣]*", text[m.end():]).group(0)
            if not after or SUFFIX.match(after) or is_title_word(after):
                out.append((m.start(), m.end(), "prop"))
    for g in given_tails:   # 세 글자 이름의 뒷 두 글자를 부르는 경우 '지영 씨', '지영아'
        for m in re.finditer(rf"(?<![가-힣]){re.escape(g)}(?=(?:아|야|이랑|랑|씨|[ \t]씨|님|에게)(?![가-힣]))", text):
            out.append((m.start(), m.end(), "prop"))
    return out


def boost_persons(text, spans, kiwi=None):
    others = [s for s in spans if s["label"] != "person"]
    taken = [(o["start"], o["end"]) for o in others]

    def free(s, e):
        return not any(s < te and ts < e for ts, te in taken)

    found = [(s["start"], s["end"], "base") for s in spans if s["label"] == "person"]
    found += [(s, e, "rule") for s, e in detect_person(text, taken, kiwi) if free(s, e)]
    found += [c for c in _full_names(text, others) if free(c[0], c[1])]
    found += [c for c in _single_char_names(text, others) if free(c[0], c[1])]
    found += [c for c in _given_only_names(text) if free(c[0], c[1])]

    # 확인된 이름 (전파의 씨앗): 두 글자 이상, 일반어 아님
    names, tails = set(), set()
    for s, e, src in found:
        n = text[s:e].replace(" ", "")
        if len(n) >= 2 and n not in COMMON2 and n not in STOPWORDS:
            names.add(n)
            core = n[2:] if n[:2] in COMPOUND else n[1:]
            if len(n) >= 3 and len(core) == 2 and core not in GIVEN_STOP:
                tails.add(core)
    found += [c for c in _propagate(text, names, tails) if free(c[0], c[1])]

    # '박준이'(세 글자로 잡힘)인데 같은 문서에 '박준'이 따로 확인됐으면 외자+조사로 경계를 줄임
    # 홀로 쓰인(뒤에 조사 없는) 두 글자 등장만 '외자 확인'으로 봄
    singles = {text[s:e] for s, e, k in found
               if e - s == 2 and (not re.match(r"[가-힣]", text[e:e + 1]) or text[e:e + 1] in ("님", "씨"))}
    # 외자 후보가 같은 자리에서 강한 왼쪽 문맥으로 잡혔으면('심사역 안윤이 조건') 세 글자 대신 외자+조사로 봄.
    # 조사 판정: kiwi 가 마지막 글자를 조사(J*)로 보면 그대로, kiwi 가 없으면 '하은·다은'처럼 이름 끝에 흔한
    # 은·는 은 빼고 이·가·을·를·의 만 조사로 봄
    single_at = {s for s, e, k in found if k == "single" and e - s == 2}
    josa_at = set()
    if kiwi is not None:
        josa_at = {t.start for t in kiwi.tokenize(text) if t.tag.startswith("J") and t.len == 1}
    fixed = []
    for s, e, k in found:
        w = text[s:e]
        if len(w) == 3 and JOSA.match(w[2]) and not right_strong(text, e):
            josa = (e - 1) in josa_at if kiwi is not None else w[2] in "이가을를의"
            if w[:2] in singles or (s in single_at and josa):
                e -= 1
        fixed.append((s, e, k))

    persons = [{"label": "person", "start": s, "end": e} for s, e, _ in fixed]
    return _resolve(others + persons)


# ================================================================ 5. 합치기
def _resolve(spans):
    """겹침 정리: 같은 시작이면 긴 것, 숫자·주소 등 비인물 라벨을 이름보다 우선."""
    pri = lambda s: 0 if s["label"] != "person" else 1
    kept = []
    for s in sorted(spans, key=lambda s: (pri(s), s["start"], -(s["end"] - s["start"]))):
        if not any(_overlap(s, k) for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s["start"])


def enhance(text, spans, address=True, person=True, kiwi=None):
    spans = [dict(s) for s in spans]
    if address:
        spans = refine_addresses(text, spans)
    if person:
        spans = boost_persons(text, spans, kiwi)
    return spans
