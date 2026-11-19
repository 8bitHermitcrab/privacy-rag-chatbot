"""탐지 통합 규칙: V6 경계 + 하이브리드 라벨 + 서로 보충

- 둘 다 잡은 자리: 범위는 V6, 라벨은 가장 많이 겹친 하이브리드 구간의 라벨
  (V6 는 경계가 정확하고, 하이브리드는 번호 라벨을 정규식으로 검증해서 라벨이 정확함)
- V6 만 잡은 자리: V6 그대로 추가 (하이브리드 미탐 보충 — 예: 팀 기준 필터가 지운 '선우 님')
- 하이브리드만 잡은 자리: 하이브리드 그대로 추가 (V6 미탐 보충 — 예: 드문 성 + 띄어쓰기 '금 재원')
- 한쪽만 잡은 구간은 review 로 돌려줌 → 적재 때 검수 대기열에 위치만 기록 (가리는 것은 그대로)

팀 기준 필터(성+직급 제거 등)는 하이브리드 쪽에만 이미 적용되어 있음. 통합 결과 전체에 다시 적용하면
V6 가 잡은 진짜 이름까지 지워져 노출이 생김 (블라인드 측정: 노출 0 → 2).
측정 근거: 블라인드 171문서 노출 0 / 정확 일치 0.992, holdout 88 질의 Hit@5 1.000 · MRR 0.918
(tools/combine_predictions.py 의 bfill_alab 과 같은 규칙)
"""


def _ov(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def _overlap_len(a, b):
    return min(a["end"], b["end"]) - max(a["start"], b["start"])


def merge(hyb, v6):
    """(spans, review) — review: [{"label","start","end","source": "v6_only"|"hybrid_only"}]"""
    out, review = [], []
    for v in v6:
        hits = [h for h in hyb if _ov(v, h)]
        if hits:
            v = {**v, "label": max(hits, key=lambda h: _overlap_len(v, h))["label"]}
        else:
            review.append({**v, "source": "v6_only"})
        out.append({"label": v["label"], "start": v["start"], "end": v["end"]})
    for h in hyb:
        if not any(_ov(h, v) for v in v6):
            out.append({"label": h["label"], "start": h["start"], "end": h["end"]})
            review.append({**h, "source": "hybrid_only"})
    out.sort(key=lambda s: (s["start"], s["end"]))
    review.sort(key=lambda s: (s["start"], s["end"]))
    return out, review
