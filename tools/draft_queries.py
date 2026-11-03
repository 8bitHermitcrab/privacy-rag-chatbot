"""평가용 질문 만들기 — holdout 원문·정답 라벨·인물 ID로 질문 문장과 정답 문서를 만듦

    python tools/draft_queries.py --preset A --name 인정        # 나(A): 이름·사람 유형 10개
    python tools/draft_queries.py --plan person_all:3,role_agent:2 --prefix A --name 인정
    python tools/draft_queries.py --preset A --name 인정 --seed 7   # 다른 인물·문서로 다시 뽑기

→ data/queries_team/<이름>.csv  (qid,query,type,relevant_doc_ids,confusable_doc_ids,memo)

분담 프리셋 (4명 × 10개 = 40개, 유형마다 담당 1명)
  A: person_all 3, name_topic 3, name_variant 2, role_agent 2
  B: homonym_role 2, hybrid_homonym_semantic 2, hybrid_person_content 2, hybrid_status 2, hybrid_within_type 2
  C: value_exact 3, value_format_variant 3, address_exact 2, address_format_variant 2
  D: address_partial 3, hybrid_paraphrase 3, semantic_no_pii 4

정답 문서
  - 사람 질문: 정답 인물 ID(HPxxx) 기준으로 그 사람이 나온 문서 전부 → 같은 사람 문서는 빠짐없이, 동명이인은 섞이지 않음
  - 동명이인 이름은 동명이인 유형에서만 사용
  - 번호·주소: 숫자만 / 시도 이름 축약해 같은 값이면 같은 정답
평가용 질의를 만드는 데만 정답 라벨을 씁니다 (가명처리·검색에는 안 씀). 로컬 처리, API 호출 없음.

만든 뒤: memo 를 보고 몇 개는 find_docs.py 로 원문 확인 → 어색한 문장은 사람 말투로 고치기
"""
import argparse
import collections
import csv
import glob
import json
import os
import random
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_ID, PERSON_ID = re.compile(r"^H\d{3,}$"), re.compile(r"^HP\d+$")
STAFF = re.compile(r"(담당|상담원|상담사|접수자|검토자|승인자|처리자|과장|대리|책임|매니저|팀장|사원|주임|차장|부장|직원)")
STATUS = ["처리 중", "처리 완료", "승인", "반려", "보류", "정지", "해지"]
SIDO = r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
LONG_SIDO = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "광주광역시",
             "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도", "강원": "강원특별자치도",
             "제주": "제주특별자치도"}
NUM_LABELS = {"phone_number", "account_number", "card_number", "email"}
PRESETS = {
    "A": "person_all:3,name_topic:3,name_variant:2,role_agent:2",
    "B": "homonym_role:2,hybrid_homonym_semantic:2,hybrid_person_content:2,hybrid_status:2,hybrid_within_type:2",
    "C": "value_exact:3,value_format_variant:3,address_exact:2,address_format_variant:2",
    "D": "address_partial:3,hybrid_paraphrase:3,semantic_no_pii:4",
}
ALL = ",".join(f"{t}:1" for t in ["person_all", "name_topic", "name_variant", "role_agent", "homonym_role",
                                  "hybrid_homonym_semantic", "hybrid_person_content", "hybrid_status", "hybrid_within_type",
                                  "hybrid_paraphrase", "semantic_no_pii", "value_exact", "value_format_variant",
                                  "address_exact", "address_format_variant", "address_partial"])


def load_jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def person_ids():
    found = {}

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
        if "holdout" in os.path.basename(p) and p.endswith(".jsonl"):
            for r in load_jsonl(p):
                walk(r, None, None)
            if found:
                break
    return found


def title(text):
    first = re.sub(r"^\[[^\]]*\]\s*", "", text.strip().split("\n")[0].strip())
    return first if 2 <= len(first) <= 30 else ""


def digits(s):
    if "@" in s:
        return s.lower()
    d = re.sub(r"\D", "", s)
    return "0" + d[2:] if d.startswith("82") else d


def addr_key(s):
    s = s.replace(" ", "").replace(",", "")
    for short, long in LONG_SIDO.items():
        s = s.replace(long, short)
    return s


def region(addr):
    m = re.match(rf"\s*{SIDO}\S*\s+([가-힣]{{1,6}}?[시군구])(?=\s|$)", addr)
    return (m.group(1), m.group(2)) if m else None


class Builder:
    def __init__(self, seed):
        self.rnd = random.Random(seed)
        docs = load_jsonl(os.path.join(ROOT, "data", "holdout_labeled.jsonl"))
        self.used_q = {q["query"] for q in load_jsonl(os.path.join(ROOT, "data", "holdout_retrieval_queries.jsonl"))}
        pid_of = person_ids()
        self.has_pid = bool(pid_of)
        self.text = {d["id"]: d["text"] for d in docs}
        self.dtype = {d["id"]: d.get("doc_type", "") for d in docs}
        self.ttl = {d["id"]: title(d["text"]) for d in docs}
        self.P = collections.defaultdict(lambda: {"docs": set(), "names": collections.Counter(), "staff": set()})
        self.vals = collections.defaultdict(lambda: {"docs": set(), "forms": set(), "label": ""})
        self.addrs = collections.defaultdict(lambda: {"docs": set(), "forms": set()})
        for d in docs:
            t = d["text"]
            for e in d.get("entities", []):
                if e["label"] == "person":
                    pid = pid_of.get((d["id"], e["start"], e["end"])) or "NAME:" + e["text"].replace(" ", "")
                    p = self.P[pid]
                    p["docs"].add(d["id"])
                    if e.get("form", "full") == "full":
                        p["names"][e["text"].replace(" ", "")] += 1
                    ls, le = t.rfind("\n", 0, e["start"]) + 1, t.find("\n", e["end"])
                    ctx = t[max(ls, e["start"] - 10):min(le if le >= 0 else len(t), e["end"] + 8)]
                    if STAFF.search(ctx):
                        p["staff"].add(d["id"])
                elif e["label"] in NUM_LABELS:
                    v = self.vals[digits(e["text"])]
                    v["docs"].add(d["id"]); v["forms"].add(e["text"]); v["label"] = e["label"]
                elif e["label"] == "address":
                    v = self.addrs[addr_key(e["text"])]
                    v["docs"].add(d["id"]); v["forms"].add(e["text"])
        by_name = collections.defaultdict(list)
        for p in sorted(self.P):
            if self.name(p):
                by_name[self.name(p)].append(p)
        self.by_name = by_name
        self.homonyms = sorted(n for n, ps in by_name.items() if len(ps) > 1)
        self.people = sorted(p for p in self.P if self.name(p) and self.name(p) not in self.homonyms)
        self.rnd.shuffle(self.people); self.rnd.shuffle(self.homonyms)
        self.groups = collections.defaultdict(list)
        for x, t in self.ttl.items():
            if t:
                self.groups[t].append(x)
        self.out, self.taken, self.k, self.used_people = [], set(), collections.Counter(), set()

    def fresh(self, ps):
        """아직 질문에 안 쓴 인물부터 (같은 사람이 여러 질문에 몰리지 않게)"""
        return [p for p in ps if p not in self.used_people] + [p for p in ps if p in self.used_people]

    def name(self, p):
        n = self.P[p]["names"].most_common(1)
        return n[0][0] if n else None

    def tpl(self, t, opts):
        s = opts[self.k[t] % len(opts)]
        self.k[t] += 1
        return s

    def add(self, t, q, rel, conf=(), memo="", key=None):
        rel, conf = sorted(set(rel)), sorted(set(conf) - set(rel))
        if not rel or q in self.used_q or any(r["query"] == q for r in self.out) or (key and key in self.taken):
            return False
        if key:
            self.taken.add(key)
            for part in str(key).split(":"):
                if part in self.P:
                    self.used_people.add(part)
        self.out.append({"type": t, "query": q, "relevant_doc_ids": ";".join(rel),
                         "confusable_doc_ids": ";".join(conf), "memo": memo})
        return True

    # ── 이름·사람 ──
    def person_all(self):
        for p in self.fresh([p for p in self.people if len(self.P[p]["docs"]) >= 2]):
            n = self.name(p)
            q = self.tpl("pa", ["{n} 님 관련 기록 전부 찾아줘", "{n} 고객 이름으로 남은 문서 다 보여줘",
                                "{n} 님이 나오는 문서 모두 알려줘"]).format(n=n)
            if self.add("person_all", q, self.P[p]["docs"], memo=f"{p}: 등장 문서 {len(self.P[p]['docs'])}건 전부", key=f"pa:{p}"):
                return True

    def name_topic(self):
        for p in self.fresh(self.people):
            ts = sorted({self.dtype[x] for x in self.P[p]["docs"] if self.dtype[x]})
            if not ts:
                continue
            t = self.rnd.choice(ts)
            n = self.name(p)
            q = self.tpl("nt", ["{n} 님 {t} 건 찾아줘", "{n} 고객 {t} 관련 문서 있어?", "{n} 님의 {t} 기록 알려줘"]).format(n=n, t=t)
            if self.add("name_topic", q, [x for x in self.P[p]["docs"] if self.dtype[x] == t], memo=f"{p}: 문서 종류 '{t}'", key=f"nt:{p}"):
                return True

    def name_variant(self):
        for p in self.fresh([p for p in self.people if len(self.name(p)) in (2, 3)]):
            n = self.name(p)
            q = self.tpl("nv", ["{s} {g}님 문의 내역 있어?", "{s} {g} 고객 건 찾아줘", "{n}씨 관련 문서 보여줘"]).format(
                s=n[0], g=n[1:], n=n)
            if self.add("name_variant", q, self.P[p]["docs"], memo=f"{p}: 띄어쓰기·호칭만 바꾼 표기", key=f"nv:{p}"):
                return True

    def role_agent(self):
        for p in self.fresh([p for p in self.people if self.P[p]["staff"]]):
            n = self.name(p)
            q = self.tpl("ra", ["{n} 담당자가 처리한 건 보여줘", "{n} 직원이 맡았던 업무 찾아줘", "{n} 님이 담당한 문서 알려줘"]).format(n=n)
            if self.add("role_agent", q, self.P[p]["staff"], memo=f"{p}: 직원(담당·직함)으로 나온 문서", key=f"ra:{p}"):
                return True

    # ── 동명이인 ──
    def homonym_role(self):
        for n in self.homonyms:
            ps = self.by_name[n]
            staff = [p for p in ps if self.P[p]["staff"]]
            if staff:
                q = self.tpl("hr", ["{n} 직원이 맡았던 업무 기록 찾아줘", "{n} 담당자 건만 보여줘"]).format(n=n)
                if self.add("homonym_role", q, self.P[staff[0]]["staff"], [x for p in ps if p != staff[0] for x in self.P[p]["docs"]],
                            memo=f"동명이인 {len(ps)}명 중 직원 {staff[0]}, 나머지는 혼동", key="hr:" + n):
                    return True

    def hybrid_homonym_semantic(self):
        for n in self.homonyms:
            for p0 in self.by_name[n]:
                ts = sorted({self.dtype[x] for x in self.P[p0]["docs"] if self.dtype[x]})
                for t in ts:
                    q = self.tpl("hhs", ["{n} 님 {t} 관련 문서 있나요?", "{t} 건으로 연락했던 {n} 고객 찾아줘"]).format(n=n, t=t)
                    if self.add("hybrid_homonym_semantic", q, [x for x in self.P[p0]["docs"] if self.dtype[x] == t],
                                [x for p in self.by_name[n] if p != p0 for x in self.P[p]["docs"]],
                                memo=f"동명이인 중 {p0}의 '{t}' 문서, 나머지는 혼동", key=f"hhs:{p0}:{t}"):
                        return True

    # ── 사람 + 조건 ──
    def hybrid_person_content(self):
        for p in self.fresh(self.people):
            xs = [x for x in sorted(self.P[p]["docs"]) if self.ttl[x]]
            if xs:
                x, n = self.rnd.choice(xs), self.name(p)
                q = self.tpl("hpc", ["{n} 고객 {t} 찾아줘", "{n} 님 {t} 내용 확인하고 싶어"]).format(n=n, t=self.ttl[x])
                if self.add("hybrid_person_content", q, [y for y in self.P[p]["docs"] if self.ttl[y] == self.ttl[x]],
                            memo=f"{p}: 제목 '{self.ttl[x]}'", key="hpc:" + p):
                    return True

    def hybrid_status(self):
        for p in self.fresh(self.people):
            for s in STATUS:
                hit = [x for x in self.P[p]["docs"] if s in self.text[x]]
                if hit:
                    n = self.name(p)
                    q = self.tpl("hs", ["{n} 고객 건 중에 {s}인 거 있어?", "{n} 님 건 {s} 상태인 것만 보여줘"]).format(n=n, s=s)
                    if self.add("hybrid_status", q, hit, memo=f"{p}: 본문에 '{s}'", key=f"hs:{p}"):
                        return True

    def hybrid_within_type(self):
        for p in self.fresh(self.people):
            for t in sorted({self.dtype[x] for x in self.P[p]["docs"] if self.dtype[x]}):
                if sum(v == t for v in self.dtype.values()) < 3:
                    continue
                n = self.name(p)
                q = self.tpl("hwt", ["{t} 문서들 중에서 {n} 이름 나오는 것만", "{t} 가운데 {n} 님 건 찾아줘"]).format(n=n, t=t)
                if self.add("hybrid_within_type", q, [x for x in self.P[p]["docs"] if self.dtype[x] == t],
                            memo=f"{p}: '{t}' 문서 안에서만", key=f"hwt:{p}"):
                    return True

    def hybrid_paraphrase(self):
        for p in self.fresh(self.people):
            xs = [x for x in sorted(self.P[p]["docs"]) if self.ttl[x]]
            if xs:
                x, n = self.rnd.choice(xs), self.name(p)
                q = f"{n} 님한테 '{self.ttl[x]}' 비슷한 일 있었던 기록"
                if self.add("hybrid_paraphrase", q, [y for y in self.P[p]["docs"] if self.ttl[y] == self.ttl[x]],
                            memo=f"{p}: 제목 '{self.ttl[x]}' — 따옴표 부분을 다른 말로 바꿔 쓰세요", key=f"hp:{p}"):
                    return True

    def semantic_no_pii(self):
        g = [t for t, xs in sorted(self.groups.items()) if len(xs) >= 2]
        self.rnd.shuffle(g)
        for t in g:
            q = self.tpl("sn", ["{t} 모아서 보여줘", "{t} 관련 문서 전부 찾아줘", "{t} 있었던 거 다 알려줘"]).format(t=t)
            if self.add("semantic_no_pii", q, self.groups[t], memo=f"제목이 '{t}'인 문서 {len(self.groups[t])}건", key="sn:" + t):
                return True

    # ── 값 ──
    def _vals(self):
        ks = sorted(self.vals)
        self.rnd.shuffle(ks)
        return ks

    def value_exact(self):
        for k in self._vals():
            f = sorted(self.vals[k]["forms"])[0]
            q = self.tpl("ve", ["{f} 이 번호로 들어온 건 찾아줘", "{f} 관련 문서 있어?", "{f} 로 등록된 고객 기록"]).format(f=f)
            if self.add("value_exact", q, self.vals[k]["docs"], memo=f"{self.vals[k]['label']} 그대로", key="v:" + k):
                return True

    def value_format_variant(self):
        for k in self._vals():
            v = self.vals[k]
            f = sorted(v["forms"])[0]
            if v["label"] == "email":
                continue
            if v["label"] == "phone_number" and f.startswith("0") and "-" in f:
                variant = self.rnd.choice(["+82 " + f[1:], f.replace("-", "")])
            elif "-" in f:
                variant = f.replace("-", "")
            else:
                continue
            q = self.tpl("vf", ["{f} 관련 문서 있어?", "{f} 이 번호로 문의한 건 찾아줘"]).format(f=variant)
            if self.add("value_format_variant", q, v["docs"], memo=f"{v['label']} 표기만 바꿈 (원래 {f})", key="v:" + k):
                return True

    # ── 주소 ──
    def _addrs(self):
        ks = sorted(self.addrs)
        self.rnd.shuffle(ks)
        return ks

    def address_exact(self):
        for k in self._addrs():
            f = sorted(self.addrs[k]["forms"])[0]
            q = self.tpl("ae", ["{f} 주소로 등록된 건 알려줘", "주소가 {f}인 고객 문서 찾아줘"]).format(f=f)
            if self.add("address_exact", q, self.addrs[k]["docs"], memo="주소 그대로", key="a:" + k):
                return True

    def address_format_variant(self):
        for k in self._addrs():
            f = sorted(self.addrs[k]["forms"])[0]
            m = re.match(SIDO + r"(\S*)", f)
            if not m:
                continue
            sido, rest = m.group(1), m.group(2)
            variant = (sido + f[m.end():]) if rest else (LONG_SIDO.get(sido, sido) + f[m.end():])
            if variant == f:
                continue
            q = self.tpl("af", ["{f} 주소 고객 기록", "{f} 사는 분 문서 찾아줘"]).format(f=variant)
            if self.add("address_format_variant", q, self.addrs[k]["docs"], memo=f"시도 이름 표기만 바꿈 (원래 {f})", key="a:" + k):
                return True

    def address_partial(self):
        regs = collections.defaultdict(set)
        for v in self.addrs.values():
            for f in v["forms"]:
                r = region(f)
                if r:
                    regs[r] |= v["docs"]
        rk = [r for r in sorted(regs) if len(regs[r]) >= 2]
        self.rnd.shuffle(rk)
        for r in rk:
            q = self.tpl("ap", ["{a} {b} 사는 고객 문서", "{a} {b} 주소인 고객 건 찾아줘", "{a} {b} 쪽 고객 기록 보여줘"]).format(a=r[0], b=r[1])
            if self.add("address_partial", q, regs[r], memo=f"주소가 {r[0]} {r[1]}인 문서 전부", key=f"ap:{r}"):
                return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=sorted(PRESETS), help="분담 프리셋 A~D (qid 접두어도 같이 정해짐)")
    ap.add_argument("--plan", help="유형:개수,… (예: person_all:3,role_agent:2). 없으면 16개 유형 1개씩")
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--name", default="me")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    plan = a.plan or (PRESETS[a.preset] if a.preset else ALL)
    prefix = a.prefix or a.preset or "A"

    b = Builder(a.seed)
    if not b.has_pid:
        print("주의: holdout 인물 정답 파일(HPxxx)을 못 찾아 이름 문자열로 사람을 구분합니다 (동명이인 구분 안 됨)")
    short = []
    for item in plan.split(","):
        t, n = item.split(":")
        fn = getattr(b, t.strip(), None)
        if not fn:
            raise SystemExit(f"모르는 유형: {t}")
        made = sum(bool(fn()) for _ in range(int(n)))
        if made < int(n):
            short.append(f"{t} {int(n) - made}개")

    os.makedirs(os.path.join(ROOT, "data", "queries_team"), exist_ok=True)
    path = os.path.join(ROOT, "data", "queries_team", f"{a.name}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, ["qid", "query", "type", "relevant_doc_ids", "confusable_doc_ids", "memo"])
        w.writeheader()
        for i, r in enumerate(b.out, 1):
            w.writerow({"qid": f"{prefix}{i:02d}", **r})
    print(f"{os.path.relpath(path, ROOT)}: {len(b.out)}개")
    for i, r in enumerate(b.out, 1):
        print(f"  {prefix}{i:02d} [{r['type']}] {r['query']}  → 정답 {len(r['relevant_doc_ids'].split(';'))}건")
    if short:
        print("조건에 맞는 문서가 모자라 못 만든 것 (직접 작성):", ", ".join(short))


if __name__ == "__main__":
    main()
