"""토큰 분류 모델 추가 학습 (CPU) — 규칙 대신 데이터로 탐지기를 개선

    # A. koelectra 이어 학습: 원래 라벨 29종을 그대로 두고 우리 문서로 더 학습 → 하이브리드의 koelectra 를 교체
    python -m tools.train_ner --base atonlee/koelectra-ko-pii-ner --keep-labels \\
        --train data/dev.jsonl --eval data/pii_synth_finance_gen_hard_dev.jsonl --out models/koelectra_ft
    #   → .env:  PII_HYBRID_MODEL=models/koelectra_ft

    # B. KLUE-RoBERTa-small 다시 학습: 파이프라인 라벨(B-person 등)로 → 기존 RoBERTa 모델 교체
    python -m tools.train_ner --base klue/roberta-small \\
        --train data/dev.jsonl --eval data/pii_synth_finance_gen_hard_dev.jsonl --out models/roberta_small_v2
    #   → .env:  PII_MODEL_DIR=models/roberta_small_v2

데이터 형식: 우리 jsonl (text + entities[label, start, end]). 여러 파일은 --train a.jsonl b.jsonl
⚠ 학습에 쓸 데이터 범위(dev 만? 개선용 hard_dev 도?)는 팀 조건·강사님 확인을 따르세요.
⚠ holdout 은 학습·검증에 절대 쓰지 말 것 (최종 평가용). --eval 에는 학습에 안 쓴 데이터만.
에폭마다 --eval 데이터의 '마스킹 재현율·노출·오탐'을 출력하고, 노출이 가장 적은 에폭을 저장합니다.
"""
import argparse
import json
import random
import time
from pathlib import Path

from starter import pii_pipeline as P
from starter.hf_detector import KOELECTRA_MAP

LABELS7 = list(P.LABELS7)
# 우리 라벨 → koelectra 라벨 (keep-labels 모드)
OURS_TO_KO = {"person": "NAME", "phone_number": "PHONE", "email": "EMAIL", "address": "ADDRESS",
              "resident_registration_number": "RRN", "account_number": "ACCOUNT_NUMBER", "card_number": "CARD_NUMBER"}


def load(paths):
    docs = []
    for p in paths:
        docs += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return docs


def char_tags(text, entities, name_of):
    """글자마다 (라벨, 시작 여부)"""
    tags = [None] * len(text)
    for e in sorted(entities, key=lambda x: x["start"]):
        lab = name_of(e["label"])
        if lab is None:
            continue
        for i in range(e["start"], min(e["end"], len(text))):
            if tags[i] is None:
                tags[i] = (lab, i == e["start"])
    return tags


def token_labels(offsets, ctags, label2id):
    """토큰 라벨: 개체 시작 글자를 덮거나 앞 토큰과 라벨이 다르면 B-, 이어지면 I-. 특수 토큰은 -100"""
    out, prev = [], None
    for s, e in offsets:
        if s == e:
            out.append(-100)
            prev = None
            continue
        covered = [ctags[i] for i in range(s, min(e, len(ctags))) if ctags[i]]
        if not covered:
            out.append(label2id["O"])
            prev = None
            continue
        lab = covered[0][0]
        bio = "B" if prev != lab or any(c[1] for c in covered if c[0] == lab) else "I"
        out.append(label2id.get(f"{bio}-{lab}", label2id["O"]))
        prev = lab
    return out


def build_examples(docs, tok, label2id, name_of, max_len=512):
    ex = []
    for d in docs:
        for off, piece in P._chunks(d["text"]):
            ents = [{**e, "start": e["start"] - off, "end": e["end"] - off} for e in d["entities"]
                    if off <= e["start"] and e["end"] <= off + len(piece)]
            enc = tok(piece, return_offsets_mapping=True, truncation=True, max_length=max_len)
            labels = token_labels(enc["offset_mapping"], char_tags(piece, ents, name_of), label2id)
            ex.append({"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"], "labels": labels})
    return ex


def batches(ex, bs, pad_id, shuffle):
    idx = list(range(len(ex)))
    if shuffle:
        random.shuffle(idx)
    for i in range(0, len(idx), bs):
        chunk = [ex[j] for j in idx[i:i + bs]]
        n = max(len(c["input_ids"]) for c in chunk)
        yield {k: [c[k] + [pad] * (n - len(c[k])) for c in chunk]
               for k, pad in (("input_ids", pad_id), ("attention_mask", 0), ("labels", -100))}


def evaluate(model, tok, docs, keep_labels):
    """파이프라인과 같은 방식으로 구간을 뽑아 노출·오탐 채점"""
    import torch
    from starter.hf_detector import HFDetector
    from tools.eval_detect_boost import score
    model.eval()
    if keep_labels:
        det = object.__new__(HFDetector)
        det.torch, det.tok, det.model = torch, tok, model
        det.id2label = model.config.id2label
        det.mapping = {k.upper(): v for k, v in KOELECTRA_MAP.items()}
        det.unmapped = __import__("collections").Counter()
        preds = [det.predict(d["text"]) for d in docs]
    else:
        preds = []
        with torch.no_grad():
            for d in docs:
                sp = []
                for off, piece in P._chunks(d["text"]):
                    enc = tok(piece, return_offsets_mapping=True, truncation=True, max_length=512, return_tensors="pt")
                    offsets = enc.pop("offset_mapping")[0].tolist()
                    enc.pop("token_type_ids", None)
                    ids = model(**enc).logits[0].argmax(-1).tolist()
                    sp += P._tags_to_spans(offsets, [model.config.id2label[i] for i in ids], off)
                preds.append([s for s in sp if s["label"] in LABELS7])
    lab, misses = score(docs, preds)
    ex = sum(lab[l]["노출"] for l in LABELS7)
    gold = sum(lab[l]["gold"] for l in LABELS7)
    return {"노출": ex, "이름노출": lab["person"]["노출"], "오탐": sum(lab[l]["오탐"] for l in LABELS7),
            "재현율": 1 - ex / max(gold, 1), "미탐유형": dict(misses)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="시작 모델 (HF id 또는 폴더)")
    ap.add_argument("--keep-labels", action="store_true", help="시작 모델의 라벨 체계를 유지 (koelectra 이어 학습)")
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--eval", help="학습에 안 쓴 검증 데이터 (holdout 금지)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--lr", type=float, default=None, help="기본: keep-labels 2e-5, 새 라벨 5e-5")
    ap.add_argument("--bs", type=int, default=8)
    ap.add_argument("--threads", type=int)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    if a.eval and "holdout" in a.eval:
        raise SystemExit("holdout 은 검증에 쓰지 마세요 (최종 평가용). 학습에 안 쓴 개선용 데이터를 쓰세요.")

    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    if a.threads:
        torch.set_num_threads(a.threads)

    tok = AutoTokenizer.from_pretrained(a.base)
    if a.keep_labels:
        model = AutoModelForTokenClassification.from_pretrained(a.base)
        label2id = {v: int(k) for k, v in model.config.id2label.items()}
        if "O" not in label2id:
            raise SystemExit("시작 모델 라벨에 'O' 가 없습니다. --keep-labels 없이 실행하세요.")
        name_of = OURS_TO_KO.get
        missing = [f"B-{v}" for v in OURS_TO_KO.values() if f"B-{v}" not in label2id]
        if missing:
            raise SystemExit(f"시작 모델에 없는 라벨: {missing}")
    else:
        names = ["O"] + [f"{p}-{l}" for l in LABELS7 for p in ("B", "I")]
        label2id = {n: i for i, n in enumerate(names)}
        model = AutoModelForTokenClassification.from_pretrained(
            a.base, num_labels=len(names), id2label=dict(enumerate(names)), label2id=label2id,
            ignore_mismatched_sizes=True)
        name_of = lambda l: l if l in LABELS7 else None   # noqa: E731

    train_docs = load(a.train)
    eval_docs = load([a.eval]) if a.eval else []
    train_ex = build_examples(train_docs, tok, label2id, name_of)
    print(f"학습 문서 {len(train_docs)} → 조각 {len(train_ex)}, 검증 문서 {len(eval_docs)}")

    lr = a.lr or (2e-5 if a.keep_labels else 5e-5)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps = a.epochs * ((len(train_ex) + a.bs - 1) // a.bs)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: max(0.0, 1 - s / max(steps, 1)))

    if eval_docs:
        r = evaluate(model, tok, eval_docs, a.keep_labels)
        print(f"[학습 전] 재현율 {r['재현율']:.3f} 노출 {r['노출']} (이름 {r['이름노출']}) 오탐 {r['오탐']}")
    best = None
    for ep in range(1, a.epochs + 1):
        model.train()
        t0, tot = time.perf_counter(), 0.0
        for b in batches(train_ex, a.bs, tok.pad_token_id or 0, True):
            out = model(**{k: torch.tensor(v) for k, v in b.items()})
            out.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            tot += out.loss.item()
        msg = f"[에폭 {ep}] 손실 {tot:.2f} ({time.perf_counter() - t0:.0f}초)"
        if eval_docs:
            r = evaluate(model, tok, eval_docs, a.keep_labels)
            msg += f" | 재현율 {r['재현율']:.3f} 노출 {r['노출']} (이름 {r['이름노출']}) 오탐 {r['오탐']}"
            if best is None or (r["노출"], r["오탐"]) < (best["노출"], best["오탐"]):
                best = {**r, "epoch": ep}
                model.save_pretrained(a.out)
                tok.save_pretrained(a.out)
                msg += "  ← 저장"
        print(msg)
    if not eval_docs:
        model.save_pretrained(a.out)
        tok.save_pretrained(a.out)
    Path(a.out, "train_info.json").write_text(json.dumps(
        {"base": a.base, "keep_labels": a.keep_labels, "train": a.train, "eval": a.eval, "epochs": a.epochs,
         "lr": lr, "best": best}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {a.out}" + (f" (에폭 {best['epoch']}, 노출 {best['노출']}, 미탐 유형 {best['미탐유형']})" if best else ""))


if __name__ == "__main__":
    main()
