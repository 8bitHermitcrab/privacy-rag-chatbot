"""Hugging Face 토큰 분류 모델 → 우리 라벨 구간 (공통 래퍼)

    det = HFDetector("atonlee/koelectra-ko-pii-ner", KOELECTRA_MAP)
    spans = det.predict(text)     # [{"label", "start", "end", "prob"}]  prob = 구간 글자들의 평균 확신도

BIO / BIOES 모두 처리하고, 이어진 주소 조각·성/이름 조각을 한 구간으로 합칩니다.
처음 보는 라벨은 det.unmapped 에 모입니다 (라벨 대응표 작성용).
"""
import collections
import re

from . import pii_pipeline as P

KEEP = set(P.LABELS7)            # 파이프라인이 다루는 라벨만 (organization 제외)
PREFIX = re.compile(r"^(?:[BIESLU])-")
MERGE_GAP = {"address": re.compile(r"^[ ,]*$"), "person": re.compile(r"^ ?$")}

KOELECTRA_ID = "atonlee/koelectra-ko-pii-ner"
KOELECTRA_MAP = {
    "NAME": "person", "PHONE": "phone_number", "EMAIL": "email", "ADDRESS": "address", "ZIPCODE": "address",
    "RRN": "resident_registration_number", "ALIEN_ID": "resident_registration_number",
    "CARD_NUMBER": "card_number", "ACCOUNT_NUMBER": "account_number", "ORGANIZATION": "organization",
    # 우리 8종에 없음
    "PASSPORT": None, "DRIVER_LICENSE": None, "IPIN": None, "CARD_EXPIRY": None, "CVC": None, "SECRET": None,
    "BIRTHDATE": None, "USER_ID": None, "GENERIC_ID": None, "VEHICLE_NUMBER": None, "IP_ADDRESS": None,
    "URL": None, "DEPARTMENT": None, "POSITION": None, "ATTRIBUTE": None, "PLACE": None, "DATE": None,
    "TIME": None, "QUANTITY": None}


class HFDetector:
    def __init__(self, model_id, mapping, threads=None):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer
        if threads:
            torch.set_num_threads(threads)
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForTokenClassification.from_pretrained(model_id).eval()
        self.id2label = self.model.config.id2label
        self.mapping = {k.upper(): v for k, v in mapping.items()}
        self.unmapped = collections.Counter()
        self.n_params = sum(p.numel() for p in self.model.parameters())

    def predict(self, text):
        raw = []
        with self.torch.no_grad():
            for off, piece in P._chunks(text):
                enc = self.tok(piece, return_offsets_mapping=True, truncation=True, max_length=512, return_tensors="pt")
                offsets = enc.pop("offset_mapping")[0].tolist()
                if "token_type_ids" in enc and self.model.config.type_vocab_size <= 1:
                    enc.pop("token_type_ids")
                probs = self.model(**enc).logits[0].softmax(-1)
                conf, ids = probs.max(-1)
                tags = [self.id2label[i] for i in ids.tolist()]
                raw += decode(piece, offsets, tags, off, conf.tolist())
        return self.to_ours(text, raw)

    def to_ours(self, text, raw):
        spans = []
        for sp in raw:
            t = sp["type"].upper()
            if t not in self.mapping:
                self.unmapped[t] += 1
                continue
            if self.mapping[t] in KEEP:
                spans.append({"label": self.mapping[t], "start": sp["start"], "end": sp["end"],
                              "prob": sp.get("prob", 1.0)})
        spans = merge_adjacent(text, spans)
        out = []
        for s in spans:
            t = P._trim(text, s)
            if t:
                out.append({**t, "prob": s["prob"]})
        return out


def decode(text, offsets, tags, off=0, conf=None):
    """BIO / BIOES 공통 디코딩: 같은 종류가 이어지면 한 구간, B-·S- 는 새 구간 시작. prob = 토큰 확신도 평균"""
    conf = conf or [1.0] * len(tags)
    out, cur = [], None
    for (s, e), tag, c in zip(offsets, tags, conf):
        if s == e:
            continue
        while s < e and text[s].isspace():   # sentencepiece 계열은 앞 공백이 오프셋에 들어오기도 함
            s += 1
        if tag == "O":
            cur = None
            continue
        pre, typ = (tag[0], PREFIX.sub("", tag)) if PREFIX.match(tag) else ("I", tag)
        if cur and cur["type"] == typ and pre not in ("B", "S", "U"):
            cur["end"] = off + e
            cur["_c"].append(c)
        else:
            cur = {"type": typ, "start": off + s, "end": off + e, "_c": [c]}
            out.append(cur)
        if pre in ("E", "S", "L", "U"):
            cur = None
    for o in out:
        o["prob"] = sum(o["_c"]) / len(o["_c"])
        del o["_c"]
    return out


def merge_adjacent(text, spans):
    spans = sorted(spans, key=lambda s: s["start"])
    out = []
    for s in spans:
        if out and out[-1]["label"] == s["label"] and s["label"] in MERGE_GAP \
                and MERGE_GAP[s["label"]].match(text[out[-1]["end"]:s["start"]]):
            out[-1] = {**out[-1], "end": max(out[-1]["end"], s["end"]),
                       "prob": min(out[-1].get("prob", 1.0), s.get("prob", 1.0))}
        else:
            out.append(dict(s))
    return out
