"""실전 2 산출물 폴더(deliverables/) 만들기

    python tools/make_deliverables.py                          # 기본: outputs/run_v3_recheck.jsonl 기준
    python tools/make_deliverables.py --run outputs/run_v3.jsonl
    python tools/make_deliverables.py --skip-tokens            # 토큰 채점 생략 (탐지기를 다시 돌리지 않음)

만드는 파일 (deliverables/)
  README.md                  각 파일 설명, 재현 방법
  token_consistency.md       ③ 원문 노출(미탐)·경계 오류·쪼개짐·합쳐짐 채점과 목록
  retrieval_eval_v3.md       ④ 최종 v3 전체·유형별·질의별 결과
  retrieval_versions.md      ④ outputs/run_*.jsonl 전부를 같은 기준으로 다시 채점한 비교 표
  run_v3_recheck.jsonl       ④ 질의별 검색 결과 (복사)
  demo_hcx007.md             ⑤ outputs/demo_hcx007.md 복사 (있으면)

개인정보 처리
  - 목록에는 원문 대신 가린 값만 씀 (이름은 첫 글자 + ○, 번호·주소는 라벨과 길이만)
  - 정답 라벨·인물 ID는 채점에만 사용 (가명처리에는 쓰지 않음)
  - 탐지·가명처리는 로컬에서만 실행 (API 호출 없음)
"""
import argparse
import collections
import glob
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metrics import evaluate, fmt, load_jsonl, table  # noqa: E402

OUT = os.path.join(ROOT, "deliverables")
DOC_ID = re.compile(r"^H\d{3,}$")
PERSON_ID = re.compile(r"^HP\d+$")


# ───────────────────────── ④ 검색 ─────────────────────────
def load_gold(path):
    qs = {q["qid"]: q for q in load_jsonl(os.path.join(ROOT, "data", "holdout_retrieval_queries.jsonl"))}
    gold = {}
    for g in load_jsonl(path):
        gold[g["qid"]] = {**g, "query": qs.get(g["qid"], {}).get("query", "")}
    return gold


def write_retrieval(run_path, gold):
    runs = load_jsonl(run_path)
    overall, by_type, per_q = evaluate(runs, gold)
    lines = [f"# 검색 평가 결과 — v3 (holdout 질의 {overall['n']}개)", "",
             f"결과 파일: `{os.path.basename(run_path)}` · 정답: `data/holdout_retrieval_queries_gold.jsonl`", "",
             "Confuse@10 = 혼동 문서가 정의된 질의만, 상위 10개 중 혼동 문서가 차지한 비율의 평균 (낮을수록 좋음, 공식 채점과 같은 정의)", "",
             "## 전체", "", table([("v3", overall)], "모드"), "",
             "## 유형별", "", table(list(by_type.items()), "유형"), "",
             "## 질의별", "",
             "질의 원문에는 개인정보가 있어 싣지 않음 (원문은 data/ 파일, 분석은 check_queries.py)", "",
             "| qid | 유형 | 정답 수 | 질의 토큰 수 | 첫 정답 순위 | Hit@5 |", "| --- | --- | --- | --- | --- | --- |"]
    for s in sorted(per_q, key=lambda s: s["qid"]):
        lines.append(f"| {s['qid']} | {s['type']} | {s['n_rel']} | {s['n_tokens']} | {s['first'] or '없음'} | "
                     f"{'O' if s['hit5'] else 'X'} |")
    fails = [s for s in per_q if not s["hit5"]]
    lines += ["", f"Hit@5 실패 {len(fails)}개: " + ", ".join(f"{s['qid']}({s['type']})" for s in fails)]
    open(os.path.join(OUT, "retrieval_eval_v3.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return overall


def write_versions(gold):
    rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "outputs", "run_*.jsonl"))):
        try:
            overall, _, _ = evaluate(load_jsonl(p), gold)
        except (KeyError, json.JSONDecodeError):
            continue
        if overall:
            rows.append((os.path.basename(p).replace("run_", "").replace(".jsonl", ""), overall))
    lines = ["# 검색 버전 비교", "",
             "outputs/run_*.jsonl 을 모두 같은 기준(tools/metrics.py)으로 다시 채점한 표입니다.",
             "파일 이름이 곧 버전입니다. 이전 키로 만든 결과와 키 교체 후 결과가 섞여 있으면 이름으로 구분하세요.", "",
             table(rows, "결과 파일") if rows else "(outputs/run_*.jsonl 없음)", "",
             "| 버전 | 검색 방식 |", "| --- | --- |",
             "| v0 | 스타터 기본 설정 |", "| v1 | 의미 검색만 (로컬 / API 임베딩) |",
             "| v2 | 질의 토큰 하나라도 가진 문서 먼저 → 의미 검색 |",
             "| v3 | 토큰 모두 → 하나라도 → 의미 검색 (채택) |",
             "| v4 | 의미 점수 + 0.5 × 토큰 일치율 |"]
    open(os.path.join(OUT, "retrieval_versions.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return len(rows)


# ───────────────────────── ③ 토큰 일관성 ─────────────────────────
def mask_value(label, text):
    t = text.replace(" ", "")
    if label == "person":
        return t[:1] + "○" * (len(t) - 1)
    return f"[{label} {len(t)}자]"


def person_kind(e):
    t = e["text"]
    n = len(t.replace(" ", ""))
    if e.get("form") == "given":
        return "성 없이 이름만"
    if n == 2 and " " in t:
        return "띄어 쓴 외자"
    if n == 2:
        return "외자"
    if n == 3:
        return "세 글자"
    return f"{n}글자 / {e.get('form', '-')}"


def load_person_ids():
    """data/ 의 인물 정답 파일에서 {(doc_id, start, end): HPxxx} 를 찾음 (형식이 달라도 최대한 읽음)"""
    found, used = {}, None

    def walk(o, doc, pid):
        if isinstance(o, dict):
            for v in o.values():
                if isinstance(v, str) and DOC_ID.match(v):
                    doc = v
                if isinstance(v, str) and PERSON_ID.match(v):
                    pid = v
            if "start" in o and "end" in o and doc and pid:
                found[(doc, int(o["start"]), int(o["end"]))] = pid
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v, doc, pid)
        elif isinstance(o, list):
            for v in o:
                walk(v, doc, pid)

    for p in sorted(glob.glob(os.path.join(ROOT, "data", "*person*"))):
        if "holdout" not in os.path.basename(p) or not p.endswith((".jsonl", ".json")):
            continue
        try:
            rows = load_jsonl(p) if p.endswith(".jsonl") else [json.load(open(p, encoding="utf-8"))]
        except json.JSONDecodeError:
            continue
        for r in rows:
            walk(r, None, None)
        if found:
            used = os.path.basename(p)
            break
    return found, used


def write_tokens(data_path):
    from starter import pii_pipeline   # 탐지 모델은 여기서만 불러옴

    docs = load_jsonl(data_path)
    pid_of, pid_file = load_person_ids()
    total = collections.Counter()
    exposed = collections.Counter()
    exposed_rows, person_miss = [], collections.Counter()
    boundary = collections.Counter()
    pid_tokens, token_pids = collections.defaultdict(set), collections.defaultdict(set)
    pid_docs, token_docs = collections.defaultdict(set), collections.defaultdict(set)

    for d in docs:
        doc_id, text = d.get("id") or d.get("doc_id"), d["text"]
        _, records = pii_pipeline.pseudonymize(text, pii_pipeline.detect(text))
        for e in d.get("entities", []):
            lab = e["label"]
            total[lab] += 1
            ov = [r for r in records if r["start"] < e["end"] and e["start"] < r["end"]]
            if not ov:
                exposed[lab] += 1
                kind = person_kind(e) if lab == "person" else "-"
                if lab == "person":
                    person_miss[kind] += 1
                exposed_rows.append((doc_id, lab, kind, mask_value(lab, e["text"])))
                continue
            if not any(r["start"] == e["start"] and r["end"] == e["end"] for r in ov):
                boundary[lab] += 1
            if lab == "person":
                pid = pid_of.get((doc_id, e["start"], e["end"]))
                if pid:
                    tok = ov[0]["token"]
                    pid_tokens[pid].add(tok)
                    token_pids[tok].add(pid)
                    pid_docs[pid].add(doc_id)
                    token_docs[tok].add(doc_id)

    n_all, n_exp = sum(total.values()), sum(exposed.values())
    splits = {p: t for p, t in pid_tokens.items() if len(t) > 1}
    merges = {t: p for t, p in token_pids.items() if len(p) > 1}

    L = ["# 토큰 일관성 채점 결과 (holdout 문서 {})".format(len(docs)), "",
         "탐지기: KLUE-RoBERTa-small + 후처리 + 규칙 (pii_pipeline.detect), 가명처리: pseudonymize (팀 HMAC 키)",
         "정답 라벨(entities)과 인물 ID는 채점에만 사용. 아래 목록의 값은 모두 가린 값입니다.", "",
         "## 요약", "",
         "| 항목 | 값 | 뜻 |", "| --- | --- | --- |",
         f"| 원문 노출 (미탐) | {n_exp} / {n_all} ({n_exp / n_all:.1%}) | 정답 개인정보 중 탐지 구간과 전혀 겹치지 않은 것 |",
         f"| 경계 오류 | {sum(boundary.values())} | 겹치지만 시작·끝이 정답과 다름 (일부만 가려지거나 더 넓게 가려짐) |"]
    if pid_of:
        L += [f"| 쪼개짐 | {len(splits)}명 | 같은 인물(정답 ID)이 2개 이상의 토큰으로 나뉨 |",
              f"| 합쳐짐 | {len(merges)}개 토큰 | 서로 다른 인물이 같은 토큰을 받음 (동명이인) |"]
    else:
        L += ["| 쪼개짐 · 합쳐짐 | 계산 못 함 | data/ 에서 holdout 인물 정답 파일(HPxxx ID)을 찾지 못함 |"]
    L += ["", "## 라벨별 원문 노출·경계 오류", "",
          "| 라벨 | 정답 수 | 원문 노출 | 노출률 | 경계 오류 |", "| --- | --- | --- | --- | --- |"]
    for lab in sorted(total):
        L.append(f"| {lab} | {total[lab]} | {exposed[lab]} | {exposed[lab] / total[lab]:.1%} | {boundary[lab]} |")
    if person_miss:
        L += ["", "## 이름 미탐 유형", "", "| 유형 | 건수 |", "| --- | --- |"]
        L += [f"| {k} | {v} |" for k, v in person_miss.most_common()]
    L += ["", "## 원문 노출 목록", "", "| 문서 | 라벨 | 유형 | 값(가림) |", "| --- | --- | --- | --- |"]
    L += [f"| {a} | {b} | {c} | {v} |" for a, b, c, v in sorted(exposed_rows)]
    if pid_of:
        L += ["", "## 쪼개짐 목록", "", f"인물 정답: `{pid_file}`", "",
              "| 인물 ID | 토큰 수 | 등장 문서 |", "| --- | --- | --- |"]
        L += [f"| {p} | {len(t)} | {', '.join(sorted(pid_docs[p]))} |" for p, t in sorted(splits.items())]
        L += ["", "## 합쳐짐 목록 (동명이인)", "", "| 토큰 | 인물 ID | 등장 문서 |", "| --- | --- | --- |"]
        L += [f"| `{t}` | {', '.join(sorted(p))} | {', '.join(sorted(token_docs[t]))} |" for t, p in sorted(merges.items())]
    L += ["", "## 원인 요약", "",
          "- 원문 노출: 외자·성 없이 쓴 이름 등 학습 데이터(dev)에 적은 형태를 모델이 놓침",
          "- 쪼개짐: 대부분 탐지 누락·경계 오류. 정규화(공백·조사 제거)가 맞아도 구간이 틀리면 다른 토큰이 됨",
          "- 합쳐짐: 토큰을 이름 문자열만으로 만들어 동명이인이 같은 토큰을 받음 → 검색에서 Confuse@10 상승"]
    open(os.path.join(OUT, "token_consistency.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    return n_exp, n_all, len(splits) if pid_of else None, len(merges) if pid_of else None


# ───────────────────────── README ─────────────────────────
def write_readme(made):
    L = ["# 실전 2 산출물", "",
         "| 산출물 | 파일 |", "| --- | --- |",
         "| ① 챗봇 데모 | `server.py`, `web/index.html`(어흥이), `web/simple.html`, `app.py`(Streamlit), `starter/chat.py` |",
         "| ② 가명처리 모듈 | `starter/pii_pipeline.py`, `starter/rules_detector.py`, 모델 `models/roberta_small_v1/`(레포 밖) |",
         "| ③ 토큰 일관성 채점 결과 | `deliverables/token_consistency.md` |",
         "| ④ 검색 평가 결과 | `deliverables/retrieval_eval_v3.md`, `deliverables/retrieval_versions.md`, `deliverables/run_v3_recheck.jsonl`, `deliverables/queries30_eval_v3.md` |",
         "| ⑤ 대화 시연 예시 | `deliverables/demo_hcx005.md`, `deliverables/demo_hcx007.md` |", "",
         "## 이번에 만든 파일", ""] + [f"- {m}" for m in made] + ["",
         "## 재현", "", "```",
         "docker compose up -d",
         "python -m starter.ingest --data data/holdout_labeled.jsonl --reset",
         "python -m starter.run_queries --out outputs/run_v3_recheck.jsonl   # SEARCH_MODE=v3",
         "python tools/eval_queries.py --queries data/queries30.csv        # 팀이 정한 질의 30개",
         "python tools/make_deliverables.py",
         "uvicorn server:app --port 8000", "```", "",
         ".env(API 키·HMAC 키)와 모델 폴더는 레포에 없습니다. 팀 비공개 채널로 받으세요."]
    open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="outputs/run_v3_recheck.jsonl")
    ap.add_argument("--data", default="data/holdout_labeled.jsonl")
    ap.add_argument("--gold", default="data/holdout_retrieval_queries_gold.jsonl")
    ap.add_argument("--skip-tokens", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    made = []

    gold = load_gold(os.path.join(ROOT, a.gold))
    run_path = os.path.join(ROOT, a.run)
    o = write_retrieval(run_path, gold)
    shutil.copy(run_path, os.path.join(OUT, os.path.basename(run_path)))
    made += [f"retrieval_eval_v3.md — Hit@1 {fmt(o['hit1'])}, Hit@5 {fmt(o['hit5'])}, MRR {fmt(o['mrr'])}, Confuse@10 {fmt(o['conf'])}",
             os.path.basename(run_path)]
    made.append(f"retrieval_versions.md — 결과 파일 {write_versions(gold)}개 비교")

    if not a.skip_tokens:
        n_exp, n_all, s, m = write_tokens(os.path.join(ROOT, a.data))
        made.append(f"token_consistency.md — 원문 노출 {n_exp}/{n_all} ({n_exp / n_all:.1%})"
                    + (f", 쪼개짐 {s}명, 합쳐짐 {m}개 토큰" if s is not None else ", 쪼개짐·합쳐짐은 인물 정답 파일 없어 생략"))

    demo = os.path.join(ROOT, "outputs", "demo_hcx007.md")
    if os.path.exists(demo):
        shutil.copy(demo, os.path.join(OUT, "demo_hcx007.md"))
        made.append("demo_hcx007.md")
    if not os.path.exists(os.path.join(OUT, "demo_hcx005.md")):
        made.append("demo_hcx005.md — 없음: 노션 '챗봇 데모 시연 기록'의 HCX-005 내용을 붙여 넣어 만드세요")

    write_readme(made)
    print("deliverables/ 생성")
    for m in made:
        print("  -", m)


if __name__ == "__main__":
    main()
