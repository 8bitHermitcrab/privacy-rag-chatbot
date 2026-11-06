"""동명이인 구분 + 문서 간 인물 연결 (종합 단계)

문제: 실전 2 토큰은 HMAC(이름) 이라 '고객센터 김민준 대리'와 '김민준 고객'이 같은 토큰이 됨 (합쳐짐).
     반대로 '인사팀 시우 드림'(이름만)은 다른 문서의 '문시우'와 다른 토큰이 됨 (쪼개짐).

해결: 적재할 때 문서마다 그 이름 주변의 '단서'를 모아 같은 사람끼리 묶은 뒤(entity resolution),
     한 이름이 두 사람 이상으로 갈라질 때만 토큰에 구분자를 더함.

  단서 (문서 안에서 그 이름에 붙은 것)
    - 식별자 : 같은 줄(또는 이름 없는 바로 앞뒤 줄)의 전화·이메일·주민번호·계좌·카드·주소 → 정규화 값의 HMAC
    - 역할   : 직원(직급·팀명·심사역·상담사…) / 고객(고객·님 귀하·신청인·민원인…)
    - 소속   : 이름 앞 팀명('정보보호팀 이서연'), 표에서 이름 옆 칸('| 이서연 | 미르보안 |')
  묶는 규칙
    1) 같은 식별자를 공유하거나 같은 소속이면 같은 사람 (긍정 근거)
    2) 서로 충돌하는 근거가 없으면 같은 사람 (이름이 하나뿐인 대부분의 경우)
    3) 충돌: 역할이 다름(직원↔고객), 소속이 다름, 주민번호가 다름, 전화·이메일·주소가 모두 다름
       → 충돌하는 무리는 '다른 사람'으로 남기고, 근거 없는 등장은 역할이 맞는 무리에 붙임
  토큰
    - 이름이 한 사람뿐이면 지금과 똑같이 HMAC("person:" + 이름) → 기존 결과와 호환
    - 여러 사람이면 HMAC("person:" + 이름 + "#" + 그 사람의 대표 단서 HMAC) → 결정적 (적재 순서와 무관)

질의 쪽: 레지스트리(이름 토큰 → 후보 사람들의 역할·소속·식별자 토큰, 원문 없음)로
         '김민준 고객' → 고객 쪽 토큰, '고객센터 김민준' → 직원 쪽 토큰. 단서가 없으면 후보 전부 + 동명이인 안내.
레지스트리에는 원문 이름·번호가 없고 토큰과 팀명만 있으므로 검색 계정(rag)에서 읽어도 됨.
"""
import collections
import json
import re

from .detect_boost import COMMON2, LEFT_LABEL, ORG_WORD
from .rules_detector import COMPOUND, SURNAMES, TITLES

STAFF_TITLES = [t for t in TITLES if t not in ("고객", "님", "씨", "드림", "올림", "선생", "교수", "변호사", "대표")]
STAFF_RIGHT = re.compile(r"^[ \t]?(?:" + "|".join(sorted(STAFF_TITLES + ["심사역", "상담사", "직원"], key=len, reverse=True)) + r")")
# 역할어 바로 뒤 이름 ('상담사 민준', '심사역 안윤'). '상담사: 김민준 고객님' 처럼 콜론이 있으면 화자 표시라 근거로 쓰지 않음
STAFF_LEFT = re.compile(r"(?:직원|상담사|상담원|심사역|담당자|담당|소속|소명자|점검자|검토자|작성자|보고자|확인자)[ \t]+$"
                        r"|(?:상담|안내|처리|응대|도와)[가-힣]*[ \t]*주신[ \t]+$"     # '상담해 주신 동현 님' (응대한 직원)
                        r"|(?:대리|과장|차장|부장|팀장|사원|주임|선임|책임|수석|매니저|실장)\s*/\s*$")
CUST_RIGHT = re.compile(r"^[ \t]?(?:고객|님[ \t]*귀하|[ \t]*님[ \t]*귀하)")
CUST_LEFT = re.compile(r"(?:고객|고객명|신청인|민원인|예금주|위임인|송금인|수령인|명의자|대표자|가입자|카드 소지자)\s*[:：]?\s*$")
ORG_LEFT = re.compile(r"([가-힣A-Za-z0-9]+(?:팀|센터|본부|지점|부서|사업부|실))\s*(?:소속\s*)?(?:/\s*[가-힣]+\s*/\s*)?$")
CELL_RIGHT = re.compile(r"^\s*\|\s*([가-힣A-Za-z][가-힣A-Za-z0-9 ]{1,15}?)\s*\|")
DOC_AFFIL = re.compile(r"(?m)^.*?(?:부서|소속|소속팀|부서/직급)\s*[:：]\s*([가-힣A-Za-z0-9]+(?:팀|센터|본부|지점|부서|실))")
ID_LABELS = ("phone_number", "email", "resident_registration_number", "account_number", "card_number", "address")


def _lines(text):
    """[(start, end)] 줄 범위"""
    out, s = [], 0
    for line in text.split("\n"):
        out.append((s, s + len(line)))
        s += len(line) + 1
    return out


def _line_of(lines, pos):
    for i, (s, e) in enumerate(lines):
        if s <= pos <= e:
            return i
    return len(lines) - 1


class Node:
    """한 문서 안의 한 이름 (그 문서의 모든 등장을 한 덩어리로)"""

    def __init__(self, doc_id, name):
        self.doc_id, self.name = doc_id, name
        self.mentions = []                       # (start, end)
        self.ids = collections.defaultdict(set)  # label -> {id_token}
        self.staff = self.cust = 0
        self.affil = set()

    @property
    def role(self):
        if self.staff > self.cust:
            return "staff"
        if self.cust > self.staff:
            return "customer"
        return None

    def evidence(self):
        return bool(self.ids) or bool(self.affil) or self.role is not None


class PersonResolver:
    def __init__(self, token_fn):
        """token_fn(label, normalized) -> 토큰 (pii_pipeline._token)"""
        self.tok = token_fn
        self.assign = {}       # (doc_id, start, end) -> token
        self.registry = {}     # 이름 토큰 -> [{"token", "role", "affil", "ids"}]

    # ------------------------------------------------------------ 1. 문서별 단서 모으기
    def _nodes_for_doc(self, doc_id, text, records):
        lines = _lines(text)
        persons = [r for r in records if r["label"] == "person"]
        nodes = {}
        for r in persons:
            n = nodes.setdefault(r["normalized"], Node(doc_id, r["normalized"]))
            n.mentions.append((r["start"], r["end"]))
            left = text[lines[_line_of(lines, r["start"])][0]:r["start"]]
            right = text[r["end"]:lines[_line_of(lines, r["start"])][1]]
            if STAFF_RIGHT.match(right) or STAFF_LEFT.search(left) or ORG_WORD.search(left):
                n.staff += 1
            if CUST_RIGHT.match(right) or CUST_LEFT.search(left):
                n.cust += 1
            m = ORG_LEFT.search(left)
            if m:
                n.affil.add(m.group(1))
            m = CELL_RIGHT.match(right)
            if m and m.group(1) not in COMMON2 and not re.search(r"\d", m.group(1)):
                n.affil.add(m.group(1).strip())

        # 같은 문서의 이름만(두 글자) → 역할이 충돌하지 않는 전체 이름이 하나뿐이면 합침 ('상담사 민준' ≠ '김민준 고객')
        # 한 글자 이름만('린 님') → 같은 문서의 외자 전체 이름('전린')과 이름 부분이 같으면 합침
        for short in [n for n in nodes.values() if len(n.name) == 1]:
            fulls = [f for f in nodes.values() if len(f.name) >= 2 and f.name.endswith(short.name)
                     and len(f.name) - (2 if f.name[:2] in COMPOUND else 1) == 1]
            if len(fulls) == 1:
                f = fulls[0]
                f.mentions += short.mentions
                f.staff, f.cust, f.affil = f.staff + short.staff, f.cust + short.cust, f.affil | short.affil
                del nodes[short.name]
        for short in [n for n in nodes.values() if len(n.name) == 2]:
            fulls = [f for f in nodes.values() if len(f.name) >= 3 and f.name.endswith(short.name)
                     and not (f.role and short.role and f.role != short.role)
                     and not (f.affil and short.affil and not (f.affil & short.affil))]
            if len(fulls) == 1:
                f = fulls[0]
                f.mentions += short.mentions
                f.staff, f.cust, f.affil = f.staff + short.staff, f.cust + short.cust, f.affil | short.affil
                del nodes[short.name]

        # 문서 양식의 소속 칸 '부서/직급: 여신심사팀 / 대리' → 바로 위 3줄 안의 이름, 없으면 문서의 유일한 이름
        for m in DOC_AFFIL.finditer(text):
            li = _line_of(lines, m.start(1))
            near = [n for n in nodes.values() if any(0 <= li - _line_of(lines, s_) <= 3 for s_, _ in n.mentions)]
            if len(near) == 1 or len(nodes) == 1:
                (near or list(nodes.values()))[0].affil.add(m.group(1))

        # 식별자 → 같은 줄의 가장 가까운 앞 이름, 없으면 이름 없는 앞뒤 줄의 이름, 그래도 없으면 문서의 유일한 고객
        person_lines = collections.defaultdict(list)
        for n in nodes.values():
            for s, e in n.mentions:
                person_lines[_line_of(lines, s)].append((s, n))
        customers = [n for n in nodes.values() if n.role == "customer"]
        for r in records:
            if r["label"] not in ID_LABELS:
                continue
            li = _line_of(lines, r["start"])
            owner = None
            if person_lines.get(li):
                before = [x for x in person_lines[li] if x[0] < r["start"]]
                owner = (max(before, key=lambda x: x[0]) if before else min(person_lines[li], key=lambda x: x[0]))[1]
            else:
                for nb in (li + 1, li - 1):
                    cand = {id(x[1]): x[1] for x in person_lines.get(nb, [])}
                    if len(cand) == 1:
                        owner = next(iter(cand.values()))
                        break
            if owner is None and len(customers) == 1:
                owner = customers[0]
            if owner is not None:
                owner.ids[r["label"]].add(r["token"])
        return list(nodes.values())

    # ------------------------------------------------------------ 2. 같은 이름끼리 묶기
    @staticmethod
    def _conflict(a, b):
        ra, rb = a["role"], b["role"]
        if ra and rb and ra != rb:
            return True
        if a["affil"] and b["affil"] and not (a["affil"] & b["affil"]):
            return True
        shared = any(a["ids"][k] & b["ids"][k] for k in ID_LABELS)
        if shared:
            return False
        for k in ("resident_registration_number",):
            if a["ids"][k] and b["ids"][k]:
                return True
        for k in ("phone_number", "email", "address"):
            if a["ids"][k] and b["ids"][k]:
                return True
        return False

    @staticmethod
    def _summary(nodes):
        ids = collections.defaultdict(set)
        st = cu = 0
        affil = set()
        for n in nodes:
            for k, v in n.ids.items():
                ids[k] |= v
            st, cu = st + n.staff, cu + n.cust
            affil |= n.affil
        role = "staff" if st > cu else "customer" if cu > st else None
        return {"ids": ids, "role": role, "affil": affil, "nodes": list(nodes)}

    def _cluster(self, nodes):
        # (1) 긍정 근거로 묶기: 식별자 공유 · 같은 소속
        parent = list(range(len(nodes)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                if any(a.ids[k] & b.ids[k] for k in ID_LABELS) or (a.affil & b.affil):
                    if not (a.role and b.role and a.role != b.role):
                        parent[find(i)] = find(j)
        groups = collections.defaultdict(list)
        for i, n in enumerate(nodes):
            groups[find(i)].append(n)
        clusters = [self._summary(g) for g in groups.values()]

        # (2) 충돌 없는 무리끼리 합치기 — 근거가 많은 무리부터 기준(anchor)으로
        clusters.sort(key=lambda c: (-(len(c["ids"]) + len(c["affil"]) + (c["role"] is not None)), -len(c["nodes"])))
        merged = []
        for c in clusters:
            fits = [m for m in merged if not self._conflict(m, c)]
            if not fits:
                merged.append(c)
                continue
            if len(fits) > 1 and c["role"]:            # 여러 곳에 맞으면 역할이 같은 쪽
                same = [m for m in fits if m["role"] == c["role"]]
                fits = same or fits
            target = max(fits, key=lambda m: len(m["nodes"]))
            idx = merged.index(target)
            merged[idx] = self._summary(target["nodes"] + c["nodes"])
            merged[idx]["ambiguous"] = target.get("ambiguous", False) or len(fits) > 1
        return merged

    # ------------------------------------------------------------ 3. 적재: fit
    def fit(self, docs):
        """docs: [{"id", "text", "records"}]  (records = pii_pipeline.pseudonymize 결과, LINK_GIVEN_NAMES 끈 상태 권장)"""
        by_name = collections.defaultdict(list)
        for d in docs:
            for n in self._nodes_for_doc(d["id"], d["text"], d["records"]):
                by_name[n.name].append(n)

        # (1) 세 글자 이상 이름부터 사람 단위로 묶기
        clusters = {nm: self._cluster(ns) for nm, ns in by_name.items() if len(nm) >= 3}

        # (2) 두 글자(외자 또는 이름만) 등장 → 뒤가 같은 전체 이름의 '사람' 중 근거가 맞는 쪽이 하나뿐이면 그 사람에 붙임
        for nm in [nm for nm in by_name if len(nm) == 2]:
            cands = [(f, c) for f, cs in clusters.items() if f.endswith(nm) for c in cs]
            left = []
            for node in by_name[nm]:
                one = self._summary([node])
                fits = [(f, c) for f, c in cands if not self._conflict(c, one)]
                if node.affil:
                    fits = [x for x in fits if x[1]["affil"] & node.affil] or fits
                if node.role and len(fits) > 1:            # 역할이 같은 사람을 우선
                    fits = [x for x in fits if x[1]["role"] == node.role] or fits
                has_ev = bool(node.affil) or node.role is not None
                if len(fits) == 1 and (has_ev or nm[0] not in SURNAMES):
                    fits[0][1]["nodes"].append(node)
                else:
                    left.append(node)
            if left:
                clusters[nm] = self._cluster(left)

        # (3) 토큰: 한 사람뿐이면 이름 토큰 그대로, 여러 사람이면 대표 단서로 구분
        for name, cs in clusters.items():
            base = self.tok("person", name)
            cs = [self._summary(c["nodes"]) | {"ambiguous": c.get("ambiguous", False)} for c in cs]
            entries = []
            for c in cs:
                if len(cs) == 1:
                    token = base
                else:
                    anchors = sorted(t for v in c["ids"].values() for t in v) or sorted(c["affil"]) or [c["role"] or "?"]
                    token = self.tok("person", f"{name}#{anchors[0]}")
                for n in c["nodes"]:
                    for s, e in n.mentions:
                        self.assign[(n.doc_id, s, e)] = token
                entries.append({"token": token, "role": c["role"], "affil": sorted(c["affil"]),
                                "ids": sorted(t for v in c["ids"].values() for t in v),
                                "docs": sorted({n.doc_id for n in c["nodes"]}), "ambiguous": c["ambiguous"]})
            self.registry[base] = entries
        return self

    def overrides(self, doc_id):
        """pseudonymize(..., override=) 에 넘길 {(start, end): token}"""
        return {(s, e): t for (d, s, e), t in self.assign.items() if d == doc_id}

    # ------------------------------------------------------------ 4. 질의
    def query_candidates(self, query, record, query_records):
        """질의 속 이름 하나 → (토큰 목록, 동명이인 여부). 단서가 있으면 하나로 좁힘."""
        base = self.tok("person", record["normalized"])
        entries = self.registry.get(base)
        if not entries or len(entries) == 1:
            return [entries[0]["token"] if entries else base], False
        q_ids = {r["token"] for r in query_records if r["label"] in ID_LABELS}
        left, right = query[:record["start"]], query[record["end"]:]
        role = None
        if STAFF_RIGHT.match(right) or STAFF_LEFT.search(left) or ORG_WORD.search(left):
            role = "staff"
        elif CUST_RIGHT.match(right) or CUST_LEFT.search(left):
            role = "customer"

        def score(en):
            s = 3 * len(q_ids & set(en["ids"]))
            s += 2 * sum(1 for a in en["affil"] if a in query)
            s += 1 if role and en["role"] == role else 0
            s -= 1 if role and en["role"] and en["role"] != role else 0
            return s

        ranked = sorted(entries, key=score, reverse=True)
        best = score(ranked[0])
        top = [e for e in ranked if score(e) == best]
        if best > 0 and len(top) == 1:
            return [top[0]["token"]], False
        return [e["token"] for e in (top if best > 0 else ranked)], True

    # ------------------------------------------------------------ 저장
    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.registry, f, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path, token_fn):
        r = cls(token_fn)
        with open(path, encoding="utf-8") as f:
            r.registry = json.load(f)
        return r
