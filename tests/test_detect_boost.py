"""detect_boost · person_resolver 회귀 테스트 (데이터셋과 무관한 직접 만든 예문만 사용)

    python -m pytest tests/ -q        또는        python tests/test_detect_boost.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("HMAC_KEY", "test-key")

from starter import pii_pipeline as P                      # noqa: E402
from starter.detect_boost import enhance                   # noqa: E402
from starter.person_resolver import PersonResolver         # noqa: E402
from starter.rules_detector import detect as rule_detect   # noqa: E402


def found(text, label="person"):
    return [text[s["start"]:s["end"]] for s in enhance(text, rule_detect(text)) if s["label"] == label]


def test_single_char_names():
    # '남결이'는 세 글자 이름일 수도 있어 단독으로는 세 글자로 가림(노출은 없음). 같은 문서에 '남결'이 홀로 나오면 외자로 확정
    assert found("심사역 남결이 조건 안내 완료.") == ["남결"]          # 역할어 뒤 외자 + 조사 '이'
    assert found("심사역 김하은 확인.") == ["김하은"]                   # '은'으로 끝나는 세 글자 이름은 유지
    assert found("심사역 남결이 조건 안내 완료. 남결 심사역 확인") == ["남결", "남결"]
    assert found("위임인: 여솔") == ["여솔"]
    assert found("처리: 카드운영팀 마준\n다음 안건") == ["마준"]
    assert found("수임인: 탁 윤 고객님 확인함.") == ["탁 윤"]
    assert found("대상 고객: 탁 윤\n카드번호: 없음") == ["탁 윤"]


def test_three_char_not_cut():
    assert found("(담당 반다인 책임)") == ["반다인"]          # '반다'+'인' 으로 자르지 않음
    assert found("담당 기수아 매니저에게 전달") == ["기수아"]   # '기수'+'아' 로 자르지 않음


def test_given_only():
    assert found("총무팀 하랑 드림") == ["하랑"]
    assert found("상담사 다온: 네, 확인하겠습니다.") == ["다온"]
    assert found("상담해 주신 다온 님 덕분에 해결했어요") == ["다온"]


def test_propagation():
    assert found("기은솔 고객은 서류 준비. 기 은솔 고객님 확인. 은솔 씨에게 연락") == ["기은솔", "기 은솔", "은솔"]


def test_no_false_positives():
    for t in ["송금인: 육나리", "방문처: 협력업체 사무실", "안내는 고객센터로 문의.", "부고 장인상\n\n고객 확인함",
              "이 건은 전 직원 대상이며 최종 승인 필요.", "콜센터 위탁 운영 현황", "회신을 위한 고객 이름",
              "고객 신용도 하락으로 고객 변경 요청."]:
        names = found(t)
        assert all(n in ("육나리",) for n in names), (t, names)


def test_address_boundary():
    t = "배송지: (01234) 서울특별시 마포구 새벽로 12 하늘아파트 103동 1201호\n반송 주소: 경기도 수원시 팔달구 매산동 12-3"
    assert found(t, "address") == ["(01234) 서울특별시 마포구 새벽로 12 하늘아파트 103동 1201호",
                                   "경기도 수원시 팔달구 매산동 12-3"]
    t = "변경 주소: 대전광역시 서구 둔산로 45, 702호 (둔산동)\n충청북도 청주시 상당구 낭성면 33-1"
    assert found(t, "address") == ["대전광역시 서구 둔산로 45, 702호 (둔산동)", "충청북도 청주시 상당구 낭성면 33-1"]


def test_homonym_resolver():
    docs = {
        "A1": "[부고] 고객센터 마준기 대리 장인상\n연락처: 마준기 대리 010-1111-2222",
        "A2": "소명자: 고객센터 / 대리 / 마준기",
        "B1": "마준기 고객이 한도 상향을 문의함. 연락처 010-3333-4444",
        "B2": "1. 신청인: 마준기 (연락처 010-3333-4444)",
        "C1": "총무팀 반서윤 과장이 승인함.",
    }
    P.LINK_GIVEN_NAMES = False
    staged = []
    for i, t in docs.items():
        sp = P.detect(t, use_model=False)
        staged.append({"id": i, "text": t, "spans": sp, "records": P.pseudonymize(t, sp)[1]})
    res = PersonResolver(P._token).fit(staged)
    tok = {}
    for st in staged:
        _, recs = P.pseudonymize(st["text"], st["spans"], override=res.overrides(st["id"]))
        tok[st["id"]] = {r["original"]: r["token"] for r in recs if r["label"] == "person"}
    assert tok["A1"]["마준기"] == tok["A2"]["마준기"]                 # 같은 직원
    assert tok["B1"]["마준기"] == tok["B2"]["마준기"]                 # 같은 고객 (전화번호 공유)
    assert tok["A1"]["마준기"] != tok["B1"]["마준기"]                 # 동명이인 구분
    assert tok["C1"]["반서윤"] == P._token("person", "반서윤")       # 한 사람뿐이면 기존 토큰 그대로

    q = "직원 마준기 법인카드 소명"
    sp = P.detect(q, use_model=False)
    rec = P.pseudonymize(q, sp)[1]
    cands, amb = res.query_candidates(q, rec[0], rec)
    assert cands == [tok["A1"]["마준기"]] and not amb
    q = "마준기 관련 문서"
    sp = P.detect(q, use_model=False)
    rec = P.pseudonymize(q, sp)[1]
    if rec:   # 질의에 단서가 없으면 후보 전부 + 동명이인 표시
        cands, amb = res.query_candidates(q, rec[0], rec)
        assert amb and len(cands) == 2


def test_model_ensemble_logic():
    """실제 모델 대신 가짜 확률로 앙상블 로직만 확인"""
    from starter import model_ensemble as E
    names = {"하랑": 0.25, "탁윤": 0.6, "모세진": 0.4}

    def fake(text, chunks):
        p = [0.01] * len(text)
        for n, v in names.items():
            i = text.find(n)
            while i >= 0:
                p[i:i + len(n)] = [v] * len(n)
                i = text.find(n, i + 1)
        return p

    def run(t):
        pr = E.person_probs(t, prob_fn=fake)
        return [t[s["start"]:s["end"]] for s in E.ensemble_persons(t, pr, rule_detect(t), 0.35, 0.15)
                if s["label"] == "person"]

    assert run("하랑 님이 유학 중 결제 문의.") == ["하랑"]          # 낮은 확률 + 이름 모양 후보 → 채택
    assert run("담당 탁 윤 확인 완료") == ["탁 윤"]                  # 붙여 보기(TTA)로만 확률이 오름
    assert run("회의록 작성 모세진, 한도 상향 검토") == ["모세진"]     # argmax 미만이지만 임계값 이상
    assert run("한도 상향 검토 완료") == []


def test_hybrid_filter_and_combine():
    from starter.hybrid_detector import combine, is_valid_name
    for bad in ["김", "Saebom", "미르보안", "조모상", "장인상", "김 과장", "박대리", "고객님", "다"]:
        assert not is_valid_name(bad), bad
    for ok in ["윤선영", "윤 선영", "남궁민수", "하랑", "박준"]:
        assert is_valid_name(ok), ok

    t = "총무팀 하랑 드림. 오늘 시우가 왔다. 미르보안 담당 확인. 다온 관련 메모"
    pos = lambda w, p=0.95: {"label": "person", "start": t.index(w), "end": t.index(w) + len(w), "prob": p}
    base = [{"label": "person", "start": t.index("하랑"), "end": t.index("하랑") + 2}]
    ks = [pos("하랑"), pos("시우"), pos("미르보안"), pos("다온", 0.6)]
    spans, review = combine(t, base, ks, tau_accept=0.9, tau_review=0.5)
    got = [t[s["start"]:s["end"]] for s in spans]
    assert got == ["하랑", "시우"], got            # 미르보안은 필터, 다온은 근거 없고 확신도 낮아 검수로
    assert [t[r["start"]:r["end"]] for r in review] == ["다온"]

    # 미동의 제거: base 에만 있고 근거 없는 이름은 검수로 빠짐
    t2 = "정다운 금융 생활을 위해. 윤선영 고객 문의"
    b2 = [{"label": "person", "start": 0, "end": 3}, {"label": "person", "start": t2.index("윤선영"), "end": t2.index("윤선영") + 3}]
    spans, review = combine(t2, b2, [], drop_unagreed=True)
    assert [t2[s["start"]:s["end"]] for s in spans] == ["윤선영"] and len(review) == 1


def test_hf_decode_bioes():
    from starter.hf_detector import decode, merge_adjacent
    t = "윤선영은 서울특별시 강남구 테헤란로 1에"
    offs = [(0, 0), (0, 1), (1, 3), (3, 4), (4, 10), (10, 14), (14, 19), (19, 21), (21, 22), (0, 0)]
    tags = ["O", "B-NAME", "E-NAME", "O", "S-CITY", "S-COUNTY", "B-STREET", "E-STREET", "O", "O"]
    raw = decode(t, offs, tags, conf=[1, .9, .7, 1, .8, .8, .6, .6, 1, 1])
    assert [(r["type"], t[r["start"]:r["end"]], round(r["prob"], 2)) for r in raw][0] == ("NAME", "윤선영", 0.8)
    m = merge_adjacent(t, [{"label": "address", "start": r["start"], "end": r["end"], "prob": r["prob"]}
                           for r in raw if r["type"] != "NAME"])
    assert [t[x["start"]:x["end"]] for x in m] == ["서울특별시 강남구 테헤란로 1"]


def test_hybrid_single_char_given_name():
    from starter.hybrid_detector import combine
    t = "송금액: USD 3,000\n\n린 님이 유학 중인 가족에게 송금. 김 님 확인. 박 씨 방문. 솔씨는 동의함"
    def k(w, p=0.97, nth=0):
        i = -1
        for _ in range(nth + 1):
            i = t.index(w, i + 1)
        return {"label": "person", "start": i, "end": i + len(w), "prob": p}
    spans, _ = combine(t, [], [k("린"), k("김"), k("박"), k("솔"), k("린", 0.6)][:4])
    assert [t[s["start"]:s["end"]] for s in spans] == ["린", "솔"]      # 성씨 한 글자(김·박)는 제외
    spans, _ = combine(t, [], [k("린", 0.6)])
    assert spans == []                                                  # 확신도 낮으면 제외


def test_hybrid_post_filter():
    from starter.hybrid_detector import post_filter
    t = ("ith me or with 김 과장님 by email\n- 면접위원: 디지털전략팀 김 과장, 여신심사팀 양 대리\n"
         "[부고] 고객센터 박동현 팀장 장인상\n사건 번호: 경찰청 2026-280674\n입금 계좌번호: 110-280-674123\n"
         "윤선영 과장이 확인. 남궁 대리 참석")
    sp = lambda w, lab="person": {"label": lab, "start": t.index(w), "end": t.index(w) + len(w)}
    spans = [sp("김 과장"), sp("양 대리"), sp("장인"), sp("2026-280674", "account_number"),
             sp("110-280-674123", "account_number"), sp("박동현"), sp("윤선영"), sp("남궁 대리")]
    keep, dropped = post_filter(t, spans)
    assert [t[s["start"]:s["end"]] for s in keep] == ["110-280-674123", "박동현", "윤선영"], keep
    assert {d["reason"] for d in dropped} == {"성+직급", "일반어·경조사", "다른 번호 항목"}


def test_boundary_spaced_compound_and_bad_address():
    t = "배송지: 경기도 용인시 수지구 죽전동 779-15\n\n선우 이안 고객은 연회비 면제 조건을 문의함."
    i = t.index("선우")
    for base in ([{"label": "address", "start": t.index("경기도"), "end": t.index("779-15") + 6},
                  {"label": "address", "start": i, "end": i + 2}],            # 모델이 '선우'를 주소로
                 [{"label": "address", "start": t.index("경기도"), "end": i + 2}]):   # 빈 줄을 넘은 주소
        got = [(s["label"], t[s["start"]:s["end"]]) for s in enhance(t, base)]
        assert got == [("address", "경기도 용인시 수지구 죽전동 779-15"), ("person", "선우 이안")], got


def test_resolver_single_char_given_links_to_full():
    t = "송금인: 반린 (010-5094-3471)\n송금액: USD 3,000\n\n린 님이 유학 중인 가족에게 보내는 생활비"
    sp = [{"label": "person", "start": t.index("반린"), "end": t.index("반린") + 2},
          {"label": "phone_number", "start": t.index("010"), "end": t.index("010") + 13},
          {"label": "person", "start": t.index("린 님"), "end": t.index("린 님") + 1}]
    P.LINK_GIVEN_NAMES = False
    res = PersonResolver(P._token).fit([{"id": "X", "text": t, "spans": sp, "records": P.pseudonymize(t, sp)[1]}])
    _, rec = P.pseudonymize(t, sp, override=res.overrides("X"))
    toks = {r["original"]: r["token"] for r in rec if r["label"] == "person"}
    assert toks["반린"] == toks["린"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
