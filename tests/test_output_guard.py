"""출력 재검사 테스트 (모델 없이 가짜 탐지기로)"""
import re

from starter import output_guard as G

A = "<PERSON_9b99d483>"     # 고객
B = "<PERSON_d8034c0f>"     # 심사역
P = "<PHONE_dbc0ab6f>"
X = "<PERSON_359c112b>"     # 다른 문서에만 있는 사람

DOCS = [
    {"doc_id": "H0179", "tokens": [A, B, P],
     "masked_text": f"{A} 님 귀하 카드 이용한도 상향 심사 결과. 담당 {B} ({P})"},
    {"doc_id": "H0133", "tokens": [A, X],
     "masked_text": f"주간 VOC 요약 VOC-3110 | {A} 고객 | 포인트 소멸 문의. 문의 김민수 010-1111-2222"},
]
QT = [A]


def fake_detect(text):
    """전화번호와 '김민수'만 잡는 가짜 탐지기. 토큰 자리의 OOO 도 일부러 잡아 무시되는지 확인"""
    spans = [{"label": "phone_number", "start": m.start(), "end": m.end()}
             for m in re.finditer(r"01[0-9]-\d{3,4}-\d{4}", text)]
    spans += [{"label": "person", "start": m.start(), "end": m.end()} for m in re.finditer("김민수", text)]
    spans += [{"label": "person", "start": m.start(), "end": m.end()} for m in re.finditer("OOO", text)]
    return spans


def run(ans, **kw):
    return G.check(ans, QT, DOCS, detect_fn=fake_detect, **kw)


def test_citation_format_fixed_and_unknown_removed():
    out, r = run(f"<H0179> {A} 고객이 한도 상향을 신청함. [H9999] 참고")
    assert "[H0179]" in out and "<H0179>" not in out and "H9999" not in out
    assert r["citations_fixed"] == 1 and r["citations_removed"] == 1


def test_unknown_and_malformed_tokens():
    out, r = run(f"[H0179] {A} 와 <PERSON_aaaaaaaa> 와 <PERSON_9b99d48> 와 <PHONE dbc0ab6f>")
    assert A in out and "<PERSON_aaaaaaaa>" not in out
    assert r["unknown_tokens"] == 1 and r["malformed_tokens"] == 2
    assert out.count(G.UNKNOWN) == 3


def test_raw_pii_masked_with_source():
    out, r = run(f"[H0133] {A} 고객 문의는 김민수 010-1111-2222 담당. [H0179] 추가 연락처 010-9999-8888")
    assert "010-1111-2222" not in out and "김민수" not in out and "010-9999-8888" not in out
    sources = sorted(i["source"] for i in r["raw_pii"])
    assert sources == ["ingest_miss", "ingest_miss", "llm_generated"]
    assert all("raw" not in i and "text" not in i for i in r["raw_pii"])   # 보고에 원문 없음
    assert A in out                                                        # 토큰은 재탐지에서 건드리지 않음


def test_sentence_with_other_persons_token_dropped():
    out, r = run(f"[H0179] {A} 고객이 한도 상향을 신청함. [H0179] {X} 고객도 같은 건을 신청함.")
    assert X not in out and A in out
    assert r["unsupported_sentences"] == 1


def test_flag_instead_of_drop():
    out, r = run(f"[H0179] {X} 고객이 신청함.", drop_unsupported=False)
    assert out.startswith("⚠") and r["unsupported_sentences"] == 1


def test_citation_after_period_attaches_to_sentence():
    out, r = run(f"{A} 고객이 한도 상향을 신청함. [H0179]")
    assert r["uncited_token_sentences"] == 0 and r["unsupported_sentences"] == 0
    assert A in out


def test_bullet_line_citation_inherited():
    out, r = run(f"- [H0133] {A} 고객이 포인트 소멸을 문의함. 처리 중인 {A} 고객 건임.")
    assert r["uncited_token_sentences"] == 0 and r["unsupported_sentences"] == 0


def test_uncited_token_sentence_counted_not_dropped():
    out, r = run(f"{A} 고객의 대출 상담 내역은 없습니다.")
    assert r["uncited_token_sentences"] == 1 and A in out


def test_clean_answer_unchanged():
    ans = f"- [H0179] {A} 고객이 카드 이용한도 상향을 신청해 승인됨"
    out, r = run(ans)
    assert out == ans and r["changed"] is False


def test_empty_answer_passthrough():
    out, r = run(None)
    assert out is None
