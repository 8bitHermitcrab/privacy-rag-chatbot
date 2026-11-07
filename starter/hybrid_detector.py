"""RoBERTa(현재 파이프라인) × koelectra-pii 하이브리드 — 넓게 잡고, 근거로 거르고, 애매한 건 검수로

    from starter.hybrid_detector import combine
    spans, review = combine(text, base_spans, koelectra_spans, kiwi=kiwi)

  1. 넓게 잡기  : 현재 파이프라인 결과(base) + koelectra 이름 후보
  2. 근거로 거르기 (koelectra 에만 있는 이름 후보)
       - 필터(is_valid_name)에 걸리면 버림: 한 글자, 한글 아님, 조직명·일반어·장례 용어, '김 과장'
         (예외 single_given_ok: '린 님'처럼 성씨가 아닌 한 글자 이름 + 님·씨, 확신도 0.9 이상)
       - 근거가 있으면 채택: kiwi 고유명사 / 앞뒤 호칭·직급·항목명 / 같은 문서에서 이미 확인된 이름
       - 근거가 없으면 koelectra 확신도 ≥ TAU_ACCEPT 일 때만 채택
       - TAU_REVIEW ≤ 확신도 < TAU_ACCEPT 는 '검수 대기열'로 (자동으로 가리진 않되 사람이 확인)
  3. (선택) base 이름 검증 : DROP_UNAGREED=1 이면 base 에만 있고 koelectra 가 동의하지 않으며
       근거도 없는 이름을 버림 → 오탐은 줄지만 미탐 위험. dev 에서 노출이 늘지 않을 때만 켜세요.
  4. 경계     : 두 모델이 겹치면 base 구간 유지, 조사·괄호는 _trim 으로 정리
  5. 최종 후처리(post_filter): 성+직급('김 과장', 팀 기준으로 끄고 켬: PII_SURNAME_TITLE_IS_PII),
       경조사 용어('장인상'의 '장인'), 다른 번호 항목('사건 번호: 2026-280674')을 계좌·카드로 본 것 제거

임계값·옵션은 .env 로 (PII_HYBRID_TAU_ACCEPT, PII_HYBRID_TAU_REVIEW, PII_HYBRID_DROP_UNAGREED, PII_HYBRID_NUM_UNION),
tools/tune_hybrid.py 로 개선용 데이터에서 고르고 다른 데이터로 확인합니다.
"""
import os
import re

from . import pii_pipeline as P
from .detect_boost import COMMON2, GIVEN_STOP, _resolve, left_strong, right_strong
from .rules_detector import COMPOUND, STOPWORDS, SURNAMES, is_title_word

TAU_ACCEPT = float(os.getenv("PII_HYBRID_TAU_ACCEPT", "0.9"))
TAU_REVIEW = float(os.getenv("PII_HYBRID_TAU_REVIEW", "0.5"))
DROP_UNAGREED = os.getenv("PII_HYBRID_DROP_UNAGREED", "0") == "1"
NUM_UNION = os.getenv("PII_HYBRID_NUM_UNION", "0") == "1"     # koelectra 번호류도 합치기 (형식 검증 통과 시)
# 필터 강도: strict = 한글 2~4글자만 (기존), loose = 영문 이름·모양이 깨진 긴 구간도 허용,
#            none = koelectra 결과를 그대로 받고 팀 기준(post_filter)만 적용, 번호는 최소 검증만
NAME_FILTER = os.getenv("PII_HYBRID_NAME_FILTER", "strict")

# 번호류 형식 검증 (일반 형식만: 자릿수·@ — 특정 값이나 문서 문구는 쓰지 않음)
_DIGITS = re.compile(r"\d")
NUM_OK = {
    "phone_number": lambda w: 9 <= len(_DIGITS.findall(w)) <= 13,
    "resident_registration_number": lambda w: len(_DIGITS.findall(w)) == 13,
    "card_number": lambda w: 15 <= len(_DIGITS.findall(w)) <= 16,
    "account_number": lambda w: 10 <= len(_DIGITS.findall(w)) <= 16,
    "email": lambda w: "@" in w and "." in w.split("@")[-1],
}
# 최소 검증 (name_filter='none' 일 때): 숫자 4개 이상 또는 '@' — 은행 이름 '국민'처럼 숫자 없는 것만 거름.
# 모델이 카드를 계좌로 보는 식으로 라벨을 바꿔 달아도 가려지기만 하면 노출은 막히므로 라벨별 자릿수는 보지 않음
NUM_MIN = lambda w: "@" in w or len(_DIGITS.findall(w)) >= 4   # noqa: E731

# 조직명 끝말 (일반 어휘). '○○보안', '○○시스템' 같은 회사명이 이름으로 잡히는 것 방지
ORG_SUFFIX = re.compile(r"(?:보안|시스템|시스템즈|솔루션|컨설팅|테크|텍|소프트|네트웍스|정보통신|통신|전자|건설|물산|"
                        r"은행|카드|캐피탈|보험|증권|생명|화재|금융|산업|상사|그룹|홀딩스|랩|랩스|파트너스|코리아|"
                        r"센터|팀|본부|지점|부서|사업부|협회|재단|학교|대학교|병원|의원|약국)$")
# 경조사 용어 (일반 어휘)
EVENT_WORD = re.compile(r"^(?:조부|조모|외조부|외조모|부친|모친|장인|장모|빙부|빙모|시부|시모|본인)(?:상|님상)?$"
                        r"|(?:결혼|회갑|칠순|팔순|돌잔치|부고|별세)$")


def is_valid_name(name):
    """사람 이름일 수 있는 모양인가 (모델 후보용 필터)"""
    core = name.replace(" ", "")
    if not re.fullmatch(r"[가-힣]{2,4}", core):            # 한 글자·영문·숫자 섞임 제외
        return False
    if core in COMMON2 or core in STOPWORDS or core in GIVEN_STOP:
        return False
    if ORG_SUFFIX.search(core):
        return False
    if EVENT_WORD.search(core):
        return False
    sur = 2 if core[:2] in COMPOUND else 1
    if is_title_word(core[sur:]):                           # '김과장', '박 대리' — 이름 없이 성 + 직급
        return False
    return True


# ---------------------------------------------------------------- 최종 후처리 (두 모델 공통 오탐)
# '김 과장'처럼 성 + 직급만 있는 표현을 개인정보로 볼지는 팀 기준. 0 = 정답 데이터 기준(개인정보 아님) → 제거
SURNAME_TITLE_IS_PII = os.getenv("PII_SURNAME_TITLE_IS_PII", "0") == "1"
# 계좌·카드처럼 생겼지만 다른 번호인 항목명 ('사건 번호: 경찰청 2026-280674', '접수번호 2026-1234')
OTHER_NUMBER_LEFT = re.compile(r"(?:사건|접수|문서|민원|주문|예약|송장|운송장|등록|관리|공문|결재|품의|요청|티켓|신고)\s*번호"
                               r"\s*[:：]?\s*(?:[가-힣]+\s*)?$")


def _surname_title(core):
    sur = 2 if core[:2] in COMPOUND else 1
    return core[:sur] in SURNAMES | set(COMPOUND) and is_title_word(core[sur:])


def post_filter(text, spans):
    """최종 결과에서 일반 규칙으로 확실한 오탐 제거 → (남길 spans, 제거 목록)"""
    keep, dropped = [], []
    for sp in spans:
        w = text[sp["start"]:sp["end"]]
        core = w.replace(" ", "")
        reason = None
        if sp["label"] == "person":
            if not SURNAME_TITLE_IS_PII and _surname_title(core):
                reason = "성+직급"
            elif not SURNAME_TITLE_IS_PII and (core in SURNAMES or core in COMPOUND):
                reason = "성만"                 # '강씨', '윤님'의 '강'·'윤' — 팀 기준: 성만으로는 개인정보 아님
            elif core in COMMON2 or EVENT_WORD.search(core) or EVENT_WORD.search(core + text[sp["end"]:sp["end"] + 1]):
                reason = "일반어·경조사"      # '장인상'의 '장인'
        elif sp["label"] in ("account_number", "card_number"):
            line_left = text[text.rfind("\n", 0, sp["start"]) + 1:sp["start"]]
            if OTHER_NUMBER_LEFT.search(line_left) and not re.search(r"계좌|카드|입금|출금|이체", line_left):
                reason = "다른 번호 항목"
        if reason:
            dropped.append({**sp, "reason": reason})
        else:
            keep.append(sp)
    return keep, dropped


def single_given_ok(text, sp, min_prob=0.9):
    """한 글자 이름 예외: '린 님', '솔 씨' 처럼 한 글자 이름 + 님·씨 로 부를 때만.
    성씨 한 글자('김 님', '박 씨')는 이름이 아니라 성만 부른 것이라 제외. 모델 확신도도 높아야 함."""
    w = text[sp["start"]:sp["end"]]
    if not re.fullmatch(r"[가-힣]", w) or w in SURNAMES or sp.get("prob", 0) < min_prob:
        return False
    before = text[sp["start"] - 1:sp["start"]] if sp["start"] else ""
    if re.match(r"[가-힣]", before):                     # 단어 중간 글자 제외
        return False
    return bool(re.match(r"[ \t]?(?:님|씨)(?![가-힣])|[ \t]?(?:님|씨)(?:께서|에게|께|이|가|은|는|의|도|과|와)(?![가-힣])",
                         text[sp["end"]:]))


def loose_name_ok(text, sp):
    """느슨한 이름 필터: 걸러야 확실한 것만 거름 (규칙을 줄이는 쪽)
    - 글자(한글·영문)가 2개 미만이면 제외 — '김', '윤' 처럼 성 한 글자만 부른 것 (강사 데이터 기준: 개인정보 아님)
    - 성 + 직급/호칭만 있으면 제외 — '심대리', '김 과장'
    - 일반어 목록에 있으면 제외
    그 밖은 허용 — 영문 이름('Namgoong Soo', 'David'), 모양이 깨진 긴 구간('현지이나 선우대')"""
    w = text[sp["start"]:sp["end"]]
    core = re.sub(r"[^가-힣A-Za-z]", "", w)
    if len(core) < 2:
        return single_given_ok(text, sp)
    hangul = re.sub(r"[^가-힣]", "", w)
    if hangul == core and (hangul in COMMON2 or hangul in STOPWORDS or hangul in GIVEN_STOP):
        return False
    if hangul == core and _surname_title(hangul):
        return False
    if len(hangul) == 1 and hangul == core:
        return False
    return True


def _overlap(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def _kiwi_nnp(kiwi, text):
    if kiwi is None:
        return []
    return [(t.start, t.start + t.len) for t in kiwi.tokenize(text) if t.tag == "NNP"]


def _evidence(text, sp, nnp, known):
    """채택 근거 이름표 (없으면 빈 목록)"""
    s, e = sp["start"], sp["end"]
    why = []
    if any(ns <= s and e <= ne + 1 for ns, ne in nnp) or any(s <= ns and ne <= e for ns, ne in nnp):
        why.append("kiwi")
    rest = re.match(r"[가-힣]*", text[e:]).group(0)
    if right_strong(text, e) or left_strong(text, s, rest):
        why.append("문맥")
    if text[s:e].replace(" ", "") in known:
        why.append("문서내")
    return why


def combine(text, base, kspans, kiwi=None, tau_accept=None, tau_review=None, drop_unagreed=None, num_union=None,
            name_filter=None):
    """base: 현재 파이프라인 결과, kspans: koelectra 결과(prob 포함) → (최종 spans, 검수 목록)"""
    tau_accept = TAU_ACCEPT if tau_accept is None else tau_accept
    tau_review = TAU_REVIEW if tau_review is None else tau_review
    drop_unagreed = DROP_UNAGREED if drop_unagreed is None else drop_unagreed
    num_union = NUM_UNION if num_union is None else num_union
    name_filter = NAME_FILTER if name_filter is None else name_filter

    # base 의 팀 기준 오탐('김 과장', 일반어 등)을 먼저 걸러야, 그 자리와 겹친 koelectra 의 진짜 이름이 건너뛰어지지 않음
    base, _ = post_filter(text, [dict(x) for x in base])
    k_person = [k for k in kspans if k["label"] == "person"]
    nnp = _kiwi_nnp(kiwi, text)
    base_persons = [b for b in base if b["label"] == "person"]
    known = {text[b["start"]:b["end"]].replace(" ", "") for b in base_persons
             if any(_overlap(b, k) for k in k_person)}          # 두 모델이 동의한 이름 = 확인된 이름

    out, review = [], []
    # 3. base 검증 (선택)
    for b in base:
        if b["label"] == "person" and drop_unagreed and not any(_overlap(b, k) for k in k_person):
            if not _evidence(text, b, nnp, known):
                review.append({**b, "source": "roberta만", "reason": "koelectra 미동의·근거 없음"})
                continue
        out.append({k: b[k] for k in ("label", "start", "end")})

    # 2. koelectra 에만 있는 이름 후보
    for k in k_person:
        if any(_overlap(k, o) for o in out):
            continue
        name = text[k["start"]:k["end"]]
        if name_filter == "none":
            ok = True
        elif name_filter == "loose":
            ok = loose_name_ok(text, k)
        else:
            ok = is_valid_name(name) or single_given_ok(text, k)
        if not ok:
            continue
        why = _evidence(text, k, nnp, known)
        cand = {"label": "person", "start": k["start"], "end": k["end"]}
        if not post_filter(text, [cand])[0]:      # 팀 기준으로 버릴 후보는 자리도 차지하지 않게
            continue
        if why or k.get("prob", 0) >= tau_accept:
            out.append(cand)
        elif k.get("prob", 0) >= tau_review:
            review.append({**cand, "prob": round(k.get("prob", 0), 3), "source": "koelectra만", "reason": "근거 없음"})
    # 4. (선택) koelectra 번호류: 겹치지 않고 형식 검증을 통과한 것만
    if num_union:
        for k in kspans:
            if k["label"] not in NUM_OK:          # 번호류만 (이름은 위에서 처리)
                continue
            w = text[k["start"]:k["end"]]
            valid = NUM_MIN(w) if name_filter == "none" else NUM_OK[k["label"]](w)
            if valid and not any(_overlap(k, o) for o in out):
                out.append({x: k[x] for x in ("label", "start", "end")})
    out, _ = post_filter(text, out)
    return P._drop_overlaps(_resolve(out)), review


# ---------------------------------------------------------------- 파이프라인 연결
_kdet = None


def koelectra():
    """koelectra 탐지기 (한 번만 로드)"""
    global _kdet
    if _kdet is None:
        from .hf_detector import KOELECTRA_ID, KOELECTRA_MAP, HFDetector
        _kdet = HFDetector(os.getenv("PII_HYBRID_MODEL", KOELECTRA_ID), KOELECTRA_MAP)
    return _kdet


def detect_with_review(text, use_model=True):
    """하이브리드 탐지 + 검수 대기열. 반환 (spans, review)"""
    old, P.HYBRID = P.HYBRID, False          # detect() 안의 하이브리드는 끄고 base 만 받음
    try:
        base = P.detect(text, use_model=use_model)
    finally:
        P.HYBRID = old
    return combine(text, base, koelectra().predict(text), kiwi=getattr(P, "_kiwi", None))


def recheck(answer_text):
    """출력 재검사용: 토큰 상태 답변에서 원문 개인정보가 남았는지. 적재와 '다른' 탐지기(koelectra+정규식)를 씀.
    재검사는 오탐 비용이 작으므로(조금 더 가릴 뿐) 근거 없는 후보도 필터만 통과하면 잡습니다."""
    masked = re.sub(r"<[A-Z_]+_[0-9a-f]{6,}>", lambda m: "□" * len(m.group(0)), answer_text)   # 토큰은 건너뜀
    ks = [k for k in koelectra().predict(masked)
          if k["label"] != "person" or is_valid_name(masked[k["start"]:k["end"]]) or single_given_ok(masked, k)]
    rules = [r for r in P.rule_detect(masked) if r["label"] != "person"]
    return P._drop_overlaps(_resolve([{x: s[x] for x in ("label", "start", "end")} for s in ks + rules]))
