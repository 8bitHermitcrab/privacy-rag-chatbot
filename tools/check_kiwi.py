"""kiwi 가 탐지 결과에 실제로 반영되는지 확인

    python -m tools.check_kiwi                                   # 1~2단계만 (예문)
    python -m tools.check_kiwi --data data/dev.jsonl             # 3~4단계: 데이터 전체 비교
    python -m tools.check_kiwi --data data/dev.jsonl --no-model  # 모델 없이 (규칙만)

1. pii_pipeline._kiwi 가 진짜 Kiwi 객체인가 (None 이면 설치·로드 실패)
2. 예문에서 kiwi 가 고유명사(NNP)로 보는 것 — 형태소 분석이 기대대로 쪼개는지 눈으로 확인
3. 규칙 탐지기 단독: kiwi 없음 vs 있음 → 결과가 달라진 문서 수, kiwi 덕분에 새로 잡힌 구간
4. detect() 최종 결과: kiwi 없음 vs 있음 → 여기서도 달라져야 '반영됐다'고 말할 수 있음
   정답(entities)이 있으면 이름 노출·오탐 변화와 처리 시간도 같이 출력
"""
import argparse
import collections
import json
import time

from starter import pii_pipeline as P
from starter.rules_detector import detect as rule_detect

EXAMPLES = ["윤 선영 고객(01003492063)이 추가 한도를 문의함. 윤선영은 서류를 준비한 뒤 다시 연락하겠다고 함.",
            "은서랑 같이 한결 플러스 카드 신청서를 작성했어요.",
            "상담원 김은영이 고객센터 대표번호 1599-0000으로 안내함."]


def spans_text(text, spans, label=None):
    return [(s["label"], text[s["start"]:s["end"]]) for s in spans if label is None or s["label"] == label]


def with_kiwi(k, fn, *a, **kw):
    old = getattr(P, "_kiwi", None)
    P._kiwi = k
    try:
        return fn(*a, **kw)
    finally:
        P._kiwi = old


def person_stats(docs, preds):
    ex = fp = 0
    for d, pr in zip(docs, preds):
        golds = [g for g in d.get("entities", []) if g["label"] == "person"]
        ex += sum(1 for g in golds if not any(p["start"] < g["end"] and g["start"] < p["end"] for p in pr))
        allg = d.get("entities", [])
        fp += sum(1 for p in pr if p["label"] == "person"
                  and not any(p["start"] < g["end"] and g["start"] < p["end"] for g in allg))
    return ex, fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--show", type=int, default=15, help="새로 잡힌 구간 예시 개수")
    a = ap.parse_args()

    # 1
    kiwi = getattr(P, "_kiwi", None)
    print("1) pii_pipeline._kiwi =", type(kiwi).__module__ + "." + type(kiwi).__name__)
    if kiwi is None:
        print("   ✗ kiwi 가 None 입니다. pip install kiwipiepy 확인, _kiwi 를 만드는 코드가 detect() 보다 위에서 실행되는지 확인")
        return

    # 2
    print("\n2) 형태소 분석 (NNP=고유명사, J*=조사)")
    for t in EXAMPLES:
        toks = [f"{x.form}/{x.tag}" for x in kiwi.tokenize(t)]
        print("  ", t[:30], "…\n     ", " ".join(toks))
        print("      규칙(kiwi 없음):", spans_text(t, rule_detect(t, kiwi=None), "person"))
        print("      규칙(kiwi 있음):", spans_text(t, rule_detect(t, kiwi=kiwi), "person"))

    if not a.data:
        return
    docs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    use_model = not a.no_model

    # 3
    changed, new = 0, collections.Counter()
    for d in docs:
        t = d["text"]
        r0, r1 = rule_detect(t, kiwi=None), rule_detect(t, kiwi=kiwi)
        k0 = {(s["label"], s["start"], s["end"]) for s in r0}
        k1 = {(s["label"], s["start"], s["end"]) for s in r1}
        if k0 != k1:
            changed += 1
            for lab, s, e in k1 - k0:
                new[(lab, t[s:e], t[max(0, s - 8):e + 8].replace("\n", "⏎"))] += 1
    print(f"\n3) 규칙 탐지기 단독: kiwi 로 결과가 달라진 문서 {changed}/{len(docs)}")
    for (lab, w, ctx), c in new.most_common(a.show):
        print(f"   + {lab} '{w}'  …{ctx}…" + (f" ×{c}" if c > 1 else ""))

    # 4
    t0 = time.perf_counter()
    p0 = with_kiwi(None, lambda: [P.detect(d["text"], use_model=use_model) for d in docs])
    t1 = time.perf_counter()
    p1 = with_kiwi(kiwi, lambda: [P.detect(d["text"], use_model=use_model) for d in docs])
    t2 = time.perf_counter()
    diff = sum(1 for x, y in zip(p0, p1) if x != y)
    n = len(docs)
    print(f"\n4) detect(use_model={use_model}) 최종 결과: kiwi 로 달라진 문서 {diff}/{n}  "
          f"(처리 시간 {1000 * (t1 - t0) / n:.1f} → {1000 * (t2 - t1) / n:.1f} ms/문서)")
    if any("entities" in d for d in docs):
        (e0, f0), (e1, f1) = person_stats(docs, p0), person_stats(docs, p1)
        print(f"   이름 노출 {e0} → {e1},  이름 오탐 {f0} → {f1}")
    if changed and not diff:
        print("   ✗ 규칙 결과는 바뀌는데 최종 결과는 그대로입니다 → 규칙의 person 결과가 최종 합치기에서 버려지고 있음"
              "\n     (pii_pipeline._union 은 규칙 결과 중 번호류·주소만 합침)")


if __name__ == "__main__":
    main()
