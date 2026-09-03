# -*- coding: utf-8 -*-
"""
③ summarize.py — 광고 분류 → 항목별 정성 요약 → base.json 과 병합  →  data/data.json

  1단계(분류) : 수집한 글·댓글을 광고/분양홍보 · 실거주후기 · 무관 으로 나눈다.
                요약보다 분류를 앞에 두는 것이 이 파이프라인의 핵심이다.
  2단계(요약) : 실거주 후기로 분류된 것만 6개 항목으로 묶어, 근거가 MIN_EVIDENCE(기본 3)건
                이상인 항목에 대해서만 한 문장을 만든다.
                원문은 재현하지 않고 "일부 후기에서 ~라는 언급이 있음" 형태로만 쓴다.
  3단계(병합) : base.json 의 정량 카드 옆에 정성 문장과 근거 링크를 붙여 data.json 을 쓴다.

  reviews_raw.json 이 없거나 --no-llm 이면 base.json 을 그대로 data.json 으로 내보낸다.
  (②③이 실패해도 배포할 산출물이 남게 하는 안전장치)

실행:  python scripts/summarize.py
       python scripts/summarize.py --no-llm      (LLM 없이 정량만으로 data.json 생성)
       python scripts/summarize.py --limit 3     (앞 3개 단지만, 프롬프트 점검용)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone, timedelta

import config

KST = timezone(timedelta(hours=9))

MODEL = "claude-opus-5"
MAX_DOCS_PER_COMPLEX = 120     # 단지당 LLM 에 넣을 문서 상한
CLASSIFY_BATCH = 30            # 분류 1회 호출당 문서 수
SNIPPET_LEN = 500              # 문서당 본문 사용 길이

ASPECT_ENUM = config.ASPECT_KEYS
ASPECT_GUIDE = "\n".join(
    "  - {0}: {1}".format(a["key"], a["label"]) for a in config.ASPECTS)

CLASSIFY_SYSTEM = """너는 아파트 거주 후기 수집물을 분류하는 도구다.

각 문서를 다음 셋 중 하나로 분류한다.
  - "ad": 분양·전세·매매 광고, 중개업소/분양대행사 홍보, 이벤트·체험단·협찬 글,
          시세 브리핑, 단지 소개만 나열한 글. 실제 거주 경험이 없으면 광고로 본다.
  - "review": 그 단지에 살아봤거나 방문·거주 경험에 기반한 서술이 하나라도 있는 글.
  - "unrelated": 대상 단지/지역과 무관하거나 판단할 내용이 없는 글.

애매하면 "ad" 로 분류한다. 광고를 후기로 넣는 실수가 후기를 놓치는 실수보다 나쁘다.

"review" 인 문서에 대해서만, 아래 항목 중 실제로 언급된 것을 aspects 에 담는다.
언급이 없으면 빈 배열로 둔다. 추측해서 채우지 않는다.
{guide}
""".format(guide=ASPECT_GUIDE)

SUMMARY_SYSTEM = """너는 아파트 거주 후기에서 항목별 장단점을 뽑아내는 도구다.

규칙:
1. 주어진 문서(실거주 후기로 분류된 것)에 실제로 적힌 내용만 쓴다. 추론·일반화 금지.
2. 원문을 그대로 옮기지 않는다. 문장을 새로 쓰되 표현은 반드시 아래 형태로만 만든다.
   "일부 후기에서 ~라는 언급이 있음"
   예) "일부 후기에서 저녁 시간대 지상 주차가 어렵다는 언급이 있음"
3. 한 항목당 한 문장, 60자 이내. 단정적 표현("~이다", "~하다")을 쓰지 않는다.
4. 근거가 되는 문서의 id 를 evidenceIds 에 모두 담는다. 실제로 그 항목을 언급한 문서만.
5. 근거 문서가 {min_evidence}건 미만인 항목은 결과에 넣지 않는다.
6. polarity 는 후기들의 방향이다. 긍정 pro / 부정 con / 엇갈림 mixed.
7. 개인 신상, 특정인 비방, 확인되지 않은 사고·범죄 언급은 요약하지 않는다.

항목:
{guide}
""".format(min_evidence=config.MIN_EVIDENCE, guide=ASPECT_GUIDE)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "docs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string", "enum": ["ad", "review", "unrelated"]},
                    "aspects": {"type": "array",
                                "items": {"type": "string", "enum": ASPECT_ENUM}},
                },
                "required": ["id", "label", "aspects"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["docs"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "aspects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": ASPECT_ENUM},
                    "polarity": {"type": "string", "enum": ["pro", "con", "mixed"]},
                    "text": {"type": "string"},
                    "evidenceIds": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["key", "polarity", "text", "evidenceIds"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["aspects"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- Claude 호출
class Claude:
    def __init__(self):
        import anthropic                      # 지연 import (--no-llm 이면 필요 없음)
        self.client = anthropic.Anthropic()
        self.use_beta = True
        self.calls = 0
        self.usage = {"input": 0, "output": 0}

    def json_call(self, system: str, user: str, schema: dict, max_tokens: int = 8000):
        """구조화 출력(JSON Schema)으로 한 번 호출하고 dict 를 돌려준다."""
        kwargs = dict(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            if self.use_beta:
                # 정책상 거절되면 서버가 같은 호출 안에서 대체 모델로 재실행한다.
                res = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default", **kwargs)
            else:
                res = self.client.messages.create(**kwargs)
        except Exception as exc:
            if self.use_beta:
                print("  [안내] 서버측 폴백 옵션을 끄고 재시도합니다: {0}".format(
                    str(exc)[:140]))
                self.use_beta = False
                return self.json_call(system, user, schema, max_tokens)
            raise

        self.calls += 1
        usage = getattr(res, "usage", None)
        if usage:
            self.usage["input"] += getattr(usage, "input_tokens", 0) or 0
            self.usage["output"] += getattr(usage, "output_tokens", 0) or 0
        if getattr(res, "stop_reason", None) == "refusal":
            print("  [안내] 모델이 이 묶음의 응답을 거절했습니다. 건너뜁니다.")
            return None
        for block in res.content:
            if block.type == "text" and block.text.strip():
                try:
                    return json.loads(block.text)
                except json.JSONDecodeError:
                    continue
        return None


# ---------------------------------------------------------------- 문서 직렬화
def doc_block(d: dict) -> str:
    return "[{id}] ({src}) {title}\n{body}".format(
        id=d.get("id", "?"),
        src={"naver_cafe": "네이버카페", "daum_cafe": "다음카페",
             "naver_blog": "네이버블로그", "blog": "블로그", "web": "웹문서",
             "review_platform": "리뷰플랫폼",
             "youtube_comment": "유튜브댓글"}.get(d.get("source"), d.get("source")),
        title=(d.get("title") or "")[:120],
        body=(d.get("text") or "")[:SNIPPET_LEN])


def classify(cl: Claude, name: str, docs: list[dict]) -> dict:
    """doc id -> {'label':..., 'aspects':[...]}"""
    result = {}
    for i in range(0, len(docs), CLASSIFY_BATCH):
        chunk = docs[i:i + CLASSIFY_BATCH]
        user = ("대상 단지: {0} (전남 나주 빛가람동)\n\n"
                "아래 문서들을 분류해라. 모든 문서에 대해 정확히 한 줄씩 결과를 낸다.\n\n"
                "{1}").format(name, "\n\n".join(doc_block(d) for d in chunk))
        out = cl.json_call(CLASSIFY_SYSTEM, user, CLASSIFY_SCHEMA)
        if not out:
            continue
        for row in out.get("docs", []):
            result[row["id"]] = {"label": row["label"],
                                 "aspects": row.get("aspects", [])}
    return result


def summarize_aspects(cl: Claude, name: str, reviews: list[dict],
                      aspect_docs: dict) -> list[dict]:
    """근거가 MIN_EVIDENCE 건 이상인 항목만 골라 한 번에 요약한다."""
    targets = [k for k, ids in aspect_docs.items() if len(ids) >= config.MIN_EVIDENCE]
    if not targets:
        return []
    by_id = {d["id"]: d for d in reviews}
    used_ids = sorted({i for k in targets for i in aspect_docs[k]})
    user = ("대상 단지: {0} (전남 나주 빛가람동)\n"
            "요약할 항목: {1}\n\n"
            "아래는 실거주 후기로 분류된 문서들이다. 각 항목에 대해 규칙에 맞는 문장을 "
            "하나씩 만들어라.\n\n{2}").format(
        name, ", ".join(targets),
        "\n\n".join(doc_block(by_id[i]) for i in used_ids if i in by_id))
    out = cl.json_call(SUMMARY_SYSTEM, user, SUMMARY_SCHEMA, max_tokens=4000)
    return (out or {}).get("aspects", [])


def source_entry(d: dict) -> dict:
    """근거 목록에 노출할 항목 — 제목과 링크만. 본문·댓글 원문은 넣지 않는다."""
    return {"title": d.get("title") or "(제목 없음)",
            "url": d.get("url"),
            "source": d.get("source"),
            "date": d.get("date")}


# ---------------------------------------------------------------- 메인
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true",
                    help="LLM 없이 base.json 을 그대로 data.json 으로 내보낸다")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-dong-summary", action="store_true")
    args = ap.parse_args()

    if not config.BASE_JSON.exists():
        print("[중단] data/base.json 이 없습니다. 먼저 scripts/base.py 를 실행하세요.")
        sys.exit(1)
    data = json.loads(config.BASE_JSON.read_text(encoding="utf-8"))

    raw = None
    if config.REVIEWS_RAW_JSON.exists():
        raw = json.loads(config.REVIEWS_RAW_JSON.read_text(encoding="utf-8"))
    if raw is None:
        print("[안내] reviews_raw.json 이 없어 정량 데이터만으로 data.json 을 만듭니다.")
    if args.no_llm:
        print("[안내] --no-llm : 정성 요약을 건너뜁니다.")

    use_llm = bool(raw) and not args.no_llm
    if use_llm and not config.ANTHROPIC_API_KEY:
        print("[안내] ANTHROPIC_API_KEY 가 없어 정성 요약을 건너뜁니다.")
        use_llm = False

    cl = Claude() if use_llm else None
    totals = {"collected": 0, "ad": 0, "review": 0, "unrelated": 0, "evidence": 0}

    targets = data["complexes"][: args.limit] if args.limit else data["complexes"]
    for i, c in enumerate(targets, 1):
        bucket = (raw or {}).get("byComplex", {}).get(c["kaptCode"], {"docs": []})
        docs = bucket.get("docs", [])[:MAX_DOCS_PER_COMPLEX]
        c["review"] = {"collected": len(docs), "ad": 0, "review": 0,
                       "unrelated": 0, "evidence": 0}
        totals["collected"] += len(docs)
        if not (use_llm and docs):
            print("  [{0}/{1}] {2} — 수집 {3}건 / 요약 생략".format(
                i, len(targets), c["name"], len(docs)))
            continue

        labels = classify(cl, c["name"], docs)
        reviews, aspect_docs = [], {k: [] for k in config.ASPECT_KEYS}
        for d in docs:
            lab = labels.get(d["id"], {"label": "unrelated", "aspects": []})
            c["review"][lab["label"]] = c["review"].get(lab["label"], 0) + 1
            totals[lab["label"]] = totals.get(lab["label"], 0) + 1
            if lab["label"] != "review":
                continue
            reviews.append(d)
            for k in lab["aspects"]:
                if k in aspect_docs:
                    aspect_docs[k].append(d["id"])

        summaries = summarize_aspects(cl, c["name"], reviews, aspect_docs) if reviews else []
        by_id = {d["id"]: d for d in reviews}
        for s in summaries:
            key = s["key"]
            ids = [i2 for i2 in s.get("evidenceIds", [])
                   if i2 in by_id and i2 in aspect_docs.get(key, [])]
            if len(ids) < config.MIN_EVIDENCE:      # 규칙 재확인 (모델 판단을 그대로 믿지 않는다)
                continue
            cell = c["aspects"].get(key)
            if not cell:
                continue
            cell["qual"] = {
                "text": s["text"].strip(),
                "polarity": s["polarity"],
                "evidenceCount": len(ids),
                "sources": [source_entry(by_id[i2]) for i2 in ids],
            }
            c["review"]["evidence"] += len(ids)
            totals["evidence"] += len(ids)

        print("  [{0}/{1}] {2} — 수집 {3} / 광고 {4} / 후기 {5} / 요약항목 {6}".format(
            i, len(targets), c["name"], len(docs), c["review"]["ad"],
            c["review"]["review"], sum(1 for a in c["aspects"].values() if a.get("qual"))))

    # ---- '동 전체' 버킷 요약 (단지 매칭이 안 된 유튜브 댓글) --------------
    data["dongSummary"] = None
    dong_docs = (raw or {}).get("dongWide", [])[:MAX_DOCS_PER_COMPLEX]
    if use_llm and dong_docs and not args.no_dong_summary:
        print("■ '동 전체' 버킷 요약 — {0}건".format(len(dong_docs)))
        labels = classify(cl, data["region"]["dong"] + " 전체", dong_docs)
        reviews, aspect_docs = [], {k: [] for k in config.ASPECT_KEYS}
        for d in dong_docs:
            lab = labels.get(d["id"], {"label": "unrelated", "aspects": []})
            if lab["label"] != "review":
                continue
            reviews.append(d)
            for k in lab["aspects"]:
                if k in aspect_docs:
                    aspect_docs[k].append(d["id"])
        by_id = {d["id"]: d for d in reviews}
        items = []
        for s in summarize_aspects(cl, data["region"]["dong"] + " 전체", reviews, aspect_docs):
            ids = [i2 for i2 in s.get("evidenceIds", []) if i2 in by_id]
            if len(ids) < config.MIN_EVIDENCE:
                continue
            items.append({"key": s["key"], "label": config.ASPECT_LABEL.get(s["key"], s["key"]),
                          "text": s["text"].strip(), "polarity": s["polarity"],
                          "evidenceCount": len(ids),
                          "sources": [source_entry(by_id[i2]) for i2 in ids]})
        if items:
            data["dongSummary"] = {"collected": len(dong_docs), "items": items}

    # ---- 마무리 ---------------------------------------------------------
    data["generatedAt"] = datetime.now(KST).isoformat(timespec="seconds")
    data["reviewStats"] = totals
    data["reviewCollectedAt"] = (raw or {}).get("generatedAt")
    data["sources"]["qual"] = "Tavily 검색 API(카페·블로그·웹), 유튜브 Data API(댓글)"
    data["sources"]["llm"] = MODEL if use_llm else None

    config.DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    config.WEB_PUBLIC.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.DATA_JSON, config.WEB_PUBLIC / "data.json")

    print("\n✔ {0} 생성".format(config.DATA_JSON))
    print("  수집 {collected} / 광고 제외 {ad} / 후기 {review} / 요약 근거 {evidence}".format(**totals))
    print("  → web/public/data.json 으로 복사 완료")
    if cl:
        print("  LLM 호출 {0}회 (입력 {1:,} · 출력 {2:,} 토큰)".format(
            cl.calls, cl.usage["input"], cl.usage["output"]))


if __name__ == "__main__":
    main()
