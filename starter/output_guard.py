"""출력 재검사: LLM 답변을 화면에 보내기 전에 코드로 거릅니다 (LLM 추가 호출 없음).

    from starter.output_guard import check
    safe_answer, report = check(answer, query_tokens, docs)

단계
  ① 근거 표기 정리   <H0179>·(H0179) → [H0179], 이번에 찾지 않은 문서 인용은 제거
  ② 토큰 검사       답변의 <LABEL_xxxxxxxx> 가 질문·참고 문서에 실제로 있던 토큰인지. 모르는/깨진 토큰 → [확인 불가]
  ③ 원문 재탐지     토큰 자리를 "OOO" 로 비우고 탐지기를 다시 돌려 남은 개인정보를 ⟨가림:LABEL⟩ 로 가림
                    출처 구분: 참고 문서에 그 문자열이 그대로 있으면 ingest_miss(적재 때 누락), 없으면 llm_generated
  ④ 근거 대조       문장 속 토큰이 그 문장이 인용한 문서에 정말 있는지. 없으면 문장 제거(다른 사람과 섞인 문장)

원칙
  - 보고(report)에는 원문을 넣지 않습니다. 개수·라벨·문서 ID·위치만 남깁니다.
  - 탐지 함수는 detect_fn 으로 바꿀 수 있습니다 (기본: pii_pipeline.detect). 테스트는 가짜 탐지기를 씁니다.

.env 선택 항목
  RECHECK=0              재검사 끄기 (비교 실험용)
  RECHECK_DROP_UNSUPPORTED=0   ④에서 문장을 지우지 않고 ⚠ 표시만
"""
import os
import re

TOKEN_RE = re.compile(r"<[A-Z]+(?:_[A-Z]+)*_[0-9a-f]{8}>")
# 토큰처럼 보이지만 형식이 깨진 것: 자릿수 다름, 대문자 16진, 공백·콜론 섞임
TOKENISH_RE = re.compile(r"<\s*[A-Z]{2,}(?:_[A-Z]+)*[\s_:\-]+[0-9a-fA-F]{4,16}\s*>")
CITE_RE = re.compile(r"\[([A-Za-z]{1,3}\d{2,6})\]")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n")

UNKNOWN = "[확인 불가]"
PLACEHOLDER = "OOO"

ENABLED = os.getenv("RECHECK", "1") != "0"
DROP_UNSUPPORTED = os.getenv("RECHECK_DROP_UNSUPPORTED", "1") != "0"


def _mask_mark(label):
    return f"⟨가림:{label.upper()}⟩"


# ------------------------------------------------------------------ ① 근거 표기
def fix_citations(text, doc_ids):
    """<H0179>·(H0179)·【H0179】 → [H0179]. 이번 참고 문서에 없는 인용은 지웁니다."""
    fixed = removed = 0
    for did in doc_ids:
        pat = re.compile(r"[<(【]\s*" + re.escape(did) + r"\s*[>)】]")
        text, n = pat.subn(f"[{did}]", text)
        fixed += n

    def keep(m):
        nonlocal removed
        if m.group(1) in doc_ids:
            return m.group(0)
        removed += 1
        return ""
    text = CITE_RE.sub(keep, text)
    return text, {"citations_fixed": fixed, "citations_removed": removed}


# ------------------------------------------------------------------ ② 토큰
def allowed_tokens(query_tokens, docs):
    allowed = set(query_tokens or [])
    for d in docs:
        allowed |= set(d.get("tokens") or [])
        allowed |= set(TOKEN_RE.findall(d.get("masked_text") or ""))
    return allowed


def check_tokens(text, allowed):
    unknown = malformed = 0

    def known(m):
        nonlocal unknown
        if m.group(0) in allowed:
            return m.group(0)
        unknown += 1
        return UNKNOWN
    text = TOKEN_RE.sub(known, text)

    def broken(m):
        nonlocal malformed
        if TOKEN_RE.fullmatch(m.group(0)):   # 정상 토큰은 그대로
            return m.group(0)
        malformed += 1
        return UNKNOWN
    text = TOKENISH_RE.sub(broken, text)
    return text, {"unknown_tokens": unknown, "malformed_tokens": malformed}


# ------------------------------------------------------------------ ③ 원문 재탐지
def _blank_tokens(text):
    """토큰 자리를 OOO 로 바꾼 글과, 바꾼 글 위치 → 원래 위치 변환 정보를 돌려줌"""
    out, segs, last, shift = [], [], 0, 0   # segs: (new_start, new_end, old_start, is_placeholder)
    pos_new = 0
    for m in TOKEN_RE.finditer(text):
        plain = text[last:m.start()]
        segs.append((pos_new, pos_new + len(plain), last, False))
        out.append(plain)
        pos_new += len(plain)
        segs.append((pos_new, pos_new + len(PLACEHOLDER), m.start(), True))
        out.append(PLACEHOLDER)
        pos_new += len(PLACEHOLDER)
        last = m.end()
    plain = text[last:]
    segs.append((pos_new, pos_new + len(plain), last, False))
    out.append(plain)
    return "".join(out), segs


def _to_old(span, segs):
    """바꾼 글 기준 구간 → 원래 글 기준 구간. 토큰 자리(OOO)와 겹치면 None"""
    s, e = span["start"], span["end"]
    old = []
    for ns, ne, os_, ph in segs:
        if e <= ns or s >= ne:
            continue
        if ph:
            return None
        old.append((os_ + max(s, ns) - ns, os_ + min(e, ne) - ns))
    if not old:
        return None
    return old[0][0], old[-1][1]


def redetect(text, docs, detect_fn):
    blanked, segs = _blank_tokens(text)
    spans = []
    for sp in detect_fn(blanked) or []:
        o = _to_old(sp, segs)
        if o and text[o[0]:o[1]].strip():
            spans.append({"label": sp["label"], "start": o[0], "end": o[1]})
    if not spans:
        return text, {"raw_pii_masked": 0, "raw_pii": []}
    doc_text = "\n".join(d.get("masked_text") or "" for d in docs)
    items, out, last = [], [], 0
    for sp in sorted(spans, key=lambda x: x["start"]):
        if sp["start"] < last:          # 겹치는 구간은 앞의 것만
            continue
        raw = text[sp["start"]:sp["end"]]
        source = "ingest_miss" if raw.strip() and raw.strip() in doc_text else "llm_generated"
        docs_hit = [d["doc_id"] for d in docs if raw.strip() and raw.strip() in (d.get("masked_text") or "")]
        items.append({"label": sp["label"], "start": sp["start"], "end": sp["end"],
                      "source": source, "doc_ids": docs_hit})          # 원문은 넣지 않음
        out.append(text[last:sp["start"]])
        out.append(_mask_mark(sp["label"]))
        last = sp["end"]
    out.append(text[last:])
    return "".join(out), {"raw_pii_masked": len(items), "raw_pii": items}


# ------------------------------------------------------------------ ④ 근거 대조
def ground_sentences(text, query_tokens, docs, drop=True):
    doc_tokens = {d["doc_id"]: set(d.get("tokens") or []) | set(TOKEN_RE.findall(d.get("masked_text") or ""))
                  for d in docs}
    q = set(query_tokens or [])
    unsupported, uncited, kept = 0, 0, []
    for line in text.split("\n"):
        parts = []
        for p in SENT_SPLIT_RE.split(line):
            if p is None:
                continue
            if parts and CITE_RE.sub("", p).strip() == "":   # "…함. [H0179]" 처럼 인용만 남은 조각은 앞 문장에 붙임
                parts[-1] = parts[-1] + " " + p
            else:
                parts.append(p)
        line_cites = [c for c in CITE_RE.findall(line) if c in doc_tokens]
        new_parts = []
        for sent in parts:
            toks = set(TOKEN_RE.findall(sent))
            cites = [c for c in CITE_RE.findall(sent) if c in doc_tokens] or line_cites   # 같은 줄(글머리표)의 인용을 물려받음
            if toks and not cites:
                uncited += 1
            elif toks and cites:
                support = set().union(*(doc_tokens[c] for c in cites)) | q
                if not toks <= support:
                    unsupported += 1
                    if drop:
                        continue
                    sent = "⚠ " + sent
            new_parts.append(sent)
        if new_parts or not line.strip():
            kept.append(" ".join(new_parts))
    return "\n".join(kept), {"unsupported_sentences": unsupported, "uncited_token_sentences": uncited}


# ------------------------------------------------------------------ 전체
def check(answer, query_tokens, docs, detect_fn=None, drop_unsupported=None):
    """반환: (안전한 답변, 보고). answer 가 비어 있으면 그대로 돌려줌"""
    report = {"enabled": ENABLED}
    if not answer or not ENABLED:
        return answer, report
    if detect_fn is None:
        from .pii_pipeline import detect as detect_fn
    drop = DROP_UNSUPPORTED if drop_unsupported is None else drop_unsupported
    doc_ids = {d["doc_id"] for d in docs}

    text, r1 = fix_citations(answer, doc_ids)
    text, r2 = check_tokens(text, allowed_tokens(query_tokens, docs))
    text, r3 = redetect(text, docs, detect_fn)
    text, r4 = ground_sentences(text, query_tokens, docs, drop=drop)
    report.update(r1, **r2, **r3, **r4)
    report["changed"] = text != answer
    return text, report
