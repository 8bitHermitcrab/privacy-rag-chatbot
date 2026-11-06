"""탐지 → 가명처리(→ 동명이인 구분) 결과를 만들어 토큰 일관성 채점까지 한 번에

    python -m tools.eval_token_consistency --data data/dev.jsonl --gold-persons data/dev_persons_gold.jsonl
    python -m tools.eval_token_consistency ... --no-model          # 모델 없이 규칙만
    python -m tools.eval_token_consistency ... --no-resolver       # 동명이인 구분 끄기 (실전 2 방식)

결과: outputs/pseudo_<이름>.jsonl, outputs/person_registry.json, 채점표 출력
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from starter import config, pii_pipeline as P
from starter.person_resolver import PersonResolver


def build(docs, use_model=True, resolver=True):
    staged = []
    P.LINK_GIVEN_NAMES = not resolver   # 동명이인 구분을 켜면 이름만 연결은 resolver 가 역할을 보고 함
    for d in docs:
        spans = P.detect(d["text"], use_model=use_model)
        _, records = P.pseudonymize(d["text"], spans)
        staged.append({"id": d["id"], "text": d["text"], "spans": spans, "records": records})
    res = PersonResolver(P._token).fit(staged) if resolver else None
    out = []
    for st in staged:
        if res:
            _, records = P.pseudonymize(st["text"], st["spans"], override=res.overrides(st["id"]))
        else:
            records = st["records"]
        out.append({"id": st["id"], "entities": [{k: r[k] for k in ("label", "start", "end", "token")} for r in records]})
    return out, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--gold-persons", required=True)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--no-resolver", action="store_true")
    a = ap.parse_args()
    docs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    pred, res = build(docs, use_model=not a.no_model, resolver=not a.no_resolver)
    config.OUT_DIR.mkdir(exist_ok=True)
    tag = Path(a.data).stem + ("_noresolver" if a.no_resolver else "")
    path = config.OUT_DIR / f"pseudo_{tag}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for p in pred:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    if res:
        res.save(config.OUT_DIR / "person_registry.json")
    subprocess.run([sys.executable, str(config.ROOT / "eval" / "check_token_consistency.py"), "--gold", a.gold_persons,
                    "--pred", str(path), "--out", str(config.OUT_DIR / f"token_consistency_{tag}.csv")], check=True)


if __name__ == "__main__":
    main()
