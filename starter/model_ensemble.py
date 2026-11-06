"""KLUE-RoBERTa-small × 규칙 앙상블 (종합 단계, 재학습 없음 · CPU)

지금 파이프라인은 모델 출력을 글자마다 argmax 한 번으로만 씁니다. 그러면
  - 모델이 'person 일 확률 0.35, O 0.65' 로 망설인 이름은 그냥 사라지고
  - 규칙은 '문맥이 약한' 이름 후보를 버립니다.
둘 다 반쯤 맞는 신호를 버리는 셈이라, 이 모듈은 두 신호를 곱해서 씁니다.

    probs = person_probs(text)                # 글자별 P(person)  (여러 번 돌려 최댓값)
    spans = ensemble_persons(text, probs, base_spans)

1) 글자별 확률 + 시험 시점 증강(TTA) — 같은 문서를 조금 다르게 3번 보고 글자마다 최댓값
     a. 원래 청크 (학습 때와 같음)
     b. 청크 경계를 반쯤 민 것 → 경계에 걸려 문맥이 잘린 이름 구제
     c. 띄어 쓴 이름을 붙인 것 '윤 선영' → '윤선영' (원문 위치로 되돌림) → 띄어 쓴 외자·이름 구제
2) 낮은 임계값 구간      : P(person) ≥ TAU_SPAN 인 글자 덩어리 (argmax 보다 너그럽게)
3) 약한 규칙 후보 검증   : 규칙이 '이름 모양'만 보고 낸 후보(문맥 근거 없음)를 모델 확률 평균 ≥ TAU_CAND 일 때만 채택
     - 성+1~2글자, 띄어 쓴 성+이름, 이름만 두 글자(님·씨·아·야·화자·서명·조사 앞)
4) 경계 다듬기           : 앞뒤 공백·괄호, 이름 뒤 조사·호칭 제거 (pii_pipeline._trim 재사용)

임계값은 dev 로 tools/tune_ensemble.py 를 돌려 정하고, test 로 한 번 확인합니다.
"""
import os
import re

from . import pii_pipeline as P
from .detect_boost import COMMON2, GIVEN_STOP, STOPWORDS, _resolve
from .rules_detector import COMPOUND, SURNAMES, is_title_word

TAU_SPAN = float(os.getenv("PII_TAU_SPAN", "0.35"))   # 모델 단독 구간 임계값 (argmax 는 대략 0.5)
TAU_CAND = float(os.getenv("PII_TAU_CAND", "0.15"))   # 규칙 후보 검증 임계값 (후보 모양이 근거를 더하므로 더 낮게)
TTA = os.getenv("PII_TTA", "shift,join").split(",")    # 쓸 증강: shift(청크 밀기), join(띄어 쓴 이름 붙이기)

SUR_ALT = "|".join(COMPOUND) + "|[" + "".join(sorted(SURNAMES)) + "]"
HANGUL = re.compile(r"[가-힣]")


# ================================================================ 1. 글자별 P(person)
def _chunks(text, max_chars=350, first=None):
    """pii_pipeline._chunks 와 같은 줄 단위 청크. first 를 주면 첫 청크를 그 길이에서 끊어 경계를 민다."""
    out, start, buf, limit = [], 0, "", first or max_chars
    for line in text.splitlines(keepends=True):
        if buf and len(buf) + len(line) > limit:
            out.append((start, buf))
            start += len(buf)
            buf, limit = "", max_chars
        buf += line
    if buf:
        out.append((start, buf))
    return out


def _model_char_probs(text, chunks):
    import torch
    tok, model = P._load_model()
    pid = [i for i, l in model.config.id2label.items() if l.split("-", 1)[-1].lower() == "person" and l != "O"]
    prob = [0.0] * len(text)
    with torch.no_grad():
        for off, piece in chunks:
            enc = tok(piece, return_offsets_mapping=True, truncation=True, max_length=512, return_tensors="pt")
            offsets = enc.pop("offset_mapping")[0].tolist()
            enc.pop("token_type_ids", None)
            p = model(**enc).logits[0].softmax(-1)[:, pid].sum(-1).tolist()
            for (s, e), v in zip(offsets, p):
                for i in range(off + s, off + e):
                    if i < len(prob) and v > prob[i]:
                        prob[i] = v
    return prob


JOIN_RE = re.compile(rf"(?<![가-힣])({SUR_ALT}) ([가-힣]{{1,2}})(?![가-힣])")


def _joined(text):
    """'윤 선영' → '윤선영'. (새 문자열, 새 위치 → 원문 위치 표)"""
    out, idx, last = [], [], 0
    for m in JOIN_RE.finditer(text):
        seg = text[last:m.start(2) - 1]                      # 공백 바로 앞까지
        out.append(seg)
        idx += range(last, m.start(2) - 1)
        last = m.start(2)                                    # 공백 하나 건너뜀
    out.append(text[last:])
    idx += range(last, len(text))
    return "".join(out), idx


def person_probs(text, prob_fn=None):
    """글자별 P(person). prob_fn(text, chunks) 는 테스트용 (기본: KLUE 모델)."""
    prob_fn = prob_fn or _model_char_probs
    prob = prob_fn(text, _chunks(text))
    if "shift" in TTA:
        p2 = prob_fn(text, _chunks(text, first=175))
        prob = [max(a, b) for a, b in zip(prob, p2)]
    if "join" in TTA and JOIN_RE.search(text):
        jt, idx = _joined(text)
        p3 = prob_fn(jt, _chunks(jt))
        for j, i in enumerate(idx):
            if p3[j] > prob[i]:
                prob[i] = p3[j]
        # 붙여서 본 이름의 확률을 원문의 공백 칸에도 이어 줌 → '윤 선영' 이 한 구간이 되게
        for m in JOIN_RE.finditer(text):
            gap = m.start(2) - 1
            prob[gap] = min(prob[gap - 1], prob[gap + 1])
    return prob


# ================================================================ 2. 낮은 임계값 구간
def _prob_spans(text, prob, tau):
    out, i, n = [], 0, len(text)
    while i < n:
        if prob[i] >= tau and HANGUL.match(text[i]):
            j = i
            while j < n and (prob[j] >= tau and (HANGUL.match(text[j]) or text[j] == " ")):
                j += 1
            sp = P._trim(text, {"label": "person", "start": i, "end": j})
            if sp and len(text[sp["start"]:sp["end"]].replace(" ", "")) >= 2:
                out.append(sp)
            i = j
        else:
            i += 1
    return out


# ================================================================ 3. 약한 규칙 후보
NAME_TAIL = r"(?=(?:님|씨|[ \t]씨|[ \t]님|아|야|이랑|랑|에게|께서|께|은|는|이|가|을|를|의|과|와|도|만)?(?![가-힣]))"
GIVEN_CTX = re.compile(r"(?<![가-힣])([가-힣]{2})(?=(?:님|씨|[ \t]씨|[ \t]님|아|야|이랑|랑|에게)(?![가-힣])"
                       r"|[ \t]*:|[ \t]+(?:드림|올림))")


def _weak_candidates(text):
    out = []
    for m in re.finditer(rf"(?<![가-힣])({SUR_ALT})[ \t]?([가-힣]{{1,2}}){NAME_TAIL}", text):
        out.append((m.start(), m.end()))
    for m in GIVEN_CTX.finditer(text):
        out.append((m.start(1), m.end(1)))
    keep = []
    for s, e in out:
        w = text[s:e].replace(" ", "")
        if w in COMMON2 or w in STOPWORDS or w in GIVEN_STOP or is_title_word(w):
            continue
        keep.append((s, e))
    return keep


def _mean(prob, s, e, text):
    v = [prob[i] for i in range(s, e) if text[i] != " "]
    return sum(v) / len(v) if v else 0.0


# ================================================================ 4. 합치기
def ensemble_persons(text, prob, base_spans, tau_span=None, tau_cand=None):
    tau_span = TAU_SPAN if tau_span is None else tau_span
    tau_cand = TAU_CAND if tau_cand is None else tau_cand
    others = [s for s in base_spans if s["label"] != "person"]
    adds = _prob_spans(text, prob, tau_span)
    for s, e in _weak_candidates(text):
        if _mean(prob, s, e, text) >= tau_cand:
            adds.append({"label": "person", "start": s, "end": e})
    adds = [a for a in adds if not any(a["start"] < o["end"] and o["start"] < a["end"] for o in others)]
    # 기존(모델 argmax·규칙) 결과가 우선, 겹치지 않는 것만 더함
    base_persons = [s for s in base_spans if s["label"] == "person"]
    extra = [a for a in adds if not any(a["start"] < b["end"] and b["start"] < a["end"] for b in base_persons)]
    return _resolve(base_spans + extra)
