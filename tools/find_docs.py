"""정답 문서 찾기 도우미 — holdout 원문에서 키워드로 문서 ID 찾기 (로컬 전용, API 호출 없음)

    python tools/find_docs.py 보이스피싱
    python tools/find_docs.py 윤선영 대출          # 모든 키워드가 들어간 문서 (공백 무시하고 비교)
    python tools/find_docs.py H0023               # 문서 ID로 원문 보기

원문에는 개인정보가 있으니 화면 캡처·공유하지 마세요. 질의를 만들 때 정답 문서 ID를 확인하는 용도입니다.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    words = sys.argv[1:]
    if not words:
        sys.exit(__doc__)
    docs = [json.loads(l) for l in open(os.path.join(ROOT, "data", "holdout_labeled.jsonl"), encoding="utf-8") if l.strip()]
    if len(words) == 1 and words[0].upper().startswith("H") and words[0][1:].isdigit():
        for d in docs:
            if d["id"] == words[0].upper():
                print(f"[{d['id']}] {d.get('doc_type', '')}\n{d['text']}")
                return
        sys.exit("없는 문서 ID")
    keys = [w.replace(" ", "") for w in words]
    hits = [d for d in docs if all(k in d["text"].replace(" ", "") for k in keys)]
    for d in hits:
        flat = d["text"].replace("\n", " ")
        at = flat.replace(" ", "").find(keys[0])
        first = flat.split(" ")[0] if at < 0 else flat[max(0, at - 20):at + 60]
        print(f"{d['id']}  [{d.get('doc_type', '')}]  …{first}…")
    print(f"\n{len(hits)}건")


if __name__ == "__main__":
    main()
