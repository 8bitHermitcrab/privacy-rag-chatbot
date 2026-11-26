"""탐지 → 가명처리 결과를 만들어 토큰 일관성 채점까지 한 번에

    python -m tools.eval_token_consistency --data data/dev.jsonl --gold-persons data/dev_persons_gold.jsonl
    python -m tools.eval_token_consistency ... --no-model          # 모델 없이 규칙만

이름 토큰은 HMAC("person:이름") 하나뿐이라, 동명이인은 '합쳐짐 - 동명이인'으로 집계됩니다.
동명이인·같은 사람 구분은 인물사전(starter.person_dictionary)에서 따로 평가합니다.
결과: outputs/pseudo_<이름>.jsonl, 채점표 출력
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from starter import config, pii_pipeline as P


def build(docs, use_model=True):
    out = []
    for d in docs:
        spans = P.detect(d["text"], use_model=use_model)
        _, records = P.pseudonymize(d["text"], spans)
        out.append({"id": d["id"], "entities": [{k: r[k] for k in ("label", "start", "end", "token")} for r in records]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--gold-persons", required=True)
    ap.add_argument("--no-model", action="store_true")
    a = ap.parse_args()
    docs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    pred = build(docs, use_model=not a.no_model)
    config.OUT_DIR.mkdir(exist_ok=True)
    tag = Path(a.data).stem
    path = config.OUT_DIR / f"pseudo_{tag}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for p in pred:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    subprocess.run([sys.executable, str(config.ROOT / "eval" / "check_token_consistency.py"), "--gold", a.gold_persons,
                    "--pred", str(path), "--out", str(config.OUT_DIR / f"token_consistency_{tag}.csv")], check=True)


if __name__ == "__main__":
    main()
