# -*- coding: utf-8 -*-
"""
make_mock.py — API 키 없이 화면을 확인하기 위한 데모 data.json 생성기.

배치 파이프라인(①②③)과 완전히 같은 구조·같은 판정 규칙으로 만든다.
실제 단지가 아니라 '예시 N단지' 이며, data.json 에 isMock=true 가 들어가
화면 상단에 데모 데이터 경고 배너가 뜬다.

실행:  python scripts/make_mock.py
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import datetime, timezone, timedelta

import config
from base import quantile, quant_text

KST = timezone(timedelta(hours=9))
random.seed(20260903)

N = 14
CENTER = config.REGION["center"]

QUAL_POOL = {
    "parking": [("con", "일부 후기에서 저녁 시간대 지상 주차가 어렵다는 언급이 있음"),
                ("pro", "일부 후기에서 지하 주차 자리가 여유롭다는 언급이 있음")],
    "fee": [("pro", "일부 후기에서 겨울 관리비가 생각보다 적게 나온다는 언급이 있음"),
            ("con", "일부 후기에서 여름철 공용 전기료 부담이 크다는 언급이 있음")],
    "security": [("pro", "일부 후기에서 경비실 응대가 빠르다는 언급이 있음")],
    "scale": [("pro", "일부 후기에서 단지 내 상가와 커뮤니티 이용이 편하다는 언급이 있음")],
    "age": [("con", "일부 후기에서 배수구 냄새 등 노후 설비 관련 언급이 있음")],
    "amenity": [("pro", "일부 후기에서 도보권에 마트와 학원가가 있다는 언급이 있음"),
                ("con", "일부 후기에서 광주 방면 출퇴근 버스가 부족하다는 언급이 있음")],
}
SRC_POOL = [
    ("naver_cafe", "빛가람동 입주민 카페 - 이사 오신 분들 참고하세요"),
    ("naver_blog", "나주 빛가람동 2년 거주 솔직 후기"),
    ("blog", "혁신도시 살이 3년차 후기 정리"),
    ("web", "나주 혁신도시 주거 환경 정리 글"),
    ("review_platform", "거주자 평점과 장단점 - 예시 단지"),
    ("youtube_comment", "[영상] 나주 혁신도시 살기 어때요?"),
]


def mock_sources(k: int) -> list[dict]:
    out = []
    for i in range(k):
        src, title = SRC_POOL[i % len(SRC_POOL)]
        out.append({"title": title, "url": "https://example.com/demo/{0}".format(i + 1),
                    "source": src, "date": "2025{0:02d}{1:02d}".format(3 + i, 10 + i)})
    return out


def main() -> None:
    rows = []
    for i in range(N):
        house = random.choice([328, 412, 486, 540, 612, 688, 754, 820, 1024])
        built = random.choice([2012, 2013, 2014, 2015, 2016, 2017, 2019, 2021, 2023])
        parking_per = round(random.uniform(0.78, 1.72), 2)
        fee_per = round(random.uniform(980, 1580), 0)
        guards = max(2, round(house / random.uniform(120, 420)))
        amenity = random.randint(3, 12)
        marea = round(house * random.uniform(72, 105), 1)
        rows.append({
            "kaptCode": "MOCK{0:04d}".format(i + 1),
            "name": "예시 {0}단지".format(i + 1),
            "addr": "전라남도 나주시 빛가람동 {0}".format(100 + i * 7),
            "roadAddr": "전라남도 나주시 빛가람로 {0}".format(20 + i * 11),
            "lat": round(CENTER["lat"] + random.uniform(-0.014, 0.014), 6),
            "lng": round(CENTER["lng"] + random.uniform(-0.017, 0.017), 6),
            "info": {
                "builtYear": built,
                "useApproveYmd": "{0}{1:02d}15".format(built, random.randint(1, 12)),
                "houseCnt": float(house),
                "dongCnt": float(random.randint(4, 14)),
                "heatType": random.choice(["지역난방", "개별난방", "지역난방"]),
                "manageType": random.choice(["위탁관리", "자치관리"]),
                "securityType": random.choice(["위탁관리", "자체관리"]),
                "builder": "예시건설",
                "manageArea": marea,
                "parkingTotal": round(parking_per * house),
                "guards": float(guards),
                "cctv": float(random.randint(40, 220)),
                "convenient": "슈퍼마켓, 어린이집, 경로당, 도서관"[:40],
                "education": "초등학교, 중학교, 학원가",
                "bus": "5분 이내",
                "subway": "",
                "monthlyCommonFee": round(fee_per * marea),
                "feeBaseMonth": "202606",
                "recentTrade": {"amount": float(random.randint(21000, 42000)),
                                "area": random.choice([59.94, 74.99, 84.97]),
                                "floor": float(random.randint(2, 20)),
                                "ym": "20260{0}".format(random.randint(4, 8)),
                                "count": random.randint(3, 24),
                                "medianAmount": float(random.randint(21000, 42000))},
            },
            "values": {
                "parking": parking_per, "fee": fee_per,
                "security": house / guards, "scale": float(house),
                "age": float(built), "amenity": float(amenity),
            },
        })

    # 좌표를 못 찾은 단지도 재현 (지도에 안 찍히고 범례에서 안내된다)
    rows[-1]["lat"] = None
    rows[-1]["lng"] = None

    # 결측 상황도 재현 — 두 단지는 관리비 수치가 없다
    for r in rows[:2]:
        r["values"]["fee"] = None
        r["info"]["monthlyCommonFee"] = None
        r["info"]["feeBaseMonth"] = None

    stats = {}
    for a in config.ASPECTS:
        key = a["key"]
        vals = [r["values"][key] for r in rows if r["values"].get(key) is not None]
        stats[key] = {"n": len(vals), "usable": len(vals) >= config.MIN_SAMPLE,
                      "min": min(vals), "max": max(vals),
                      "avg": sum(vals) / len(vals),
                      "q1": quantile(vals, 0.25), "median": quantile(vals, 0.5),
                      "q3": quantile(vals, 0.75)}

    ordered = {}
    for a in config.ASPECTS:
        key = a["key"]
        ordered[key] = sorted([r["values"][key] for r in rows
                               if r["values"][key] is not None],
                              reverse=a["higher_is_better"])

    for r in rows:
        r["aspects"] = {}
        for a in config.ASPECTS:
            key, hib = a["key"], a["higher_is_better"]
            v, st = r["values"][key], stats[key]
            cell = {"label": a["label"], "unit": a["unit"], "value": v,
                    "verdict": "none", "quantText": None, "qual": None,
                    "n": st["n"], "rank": None}
            if v is not None:
                good = v > st["q3"] if hib else v < st["q1"]
                bad = v < st["q1"] if hib else v > st["q3"]
                cell["verdict"] = "pro" if good else ("con" if bad else "none")
                cell["dongAvg"], cell["q1"], cell["q3"] = st["avg"], st["q1"], st["q3"]
                cell["median"] = st["median"]
                cell["quantText"] = quant_text(key, v, st["avg"])
                cell["rank"] = ordered[key].index(v) + 1
            else:
                cell["quantText"] = "공개 데이터에 해당 수치가 없어 판정하지 않았습니다"
            r["aspects"][key] = cell

    for r in rows:
        r.pop("values", None)

    # 정성 요약은 앞쪽 단지에만 붙인다 (후기 0건 단지의 화면도 확인하기 위해)
    totals = {"collected": 0, "ad": 0, "review": 0, "unrelated": 0, "evidence": 0}
    for idx, r in enumerate(rows):
        if idx >= 9:
            r["review"] = {"collected": 0, "ad": 0, "review": 0, "unrelated": 0, "evidence": 0}
            continue
        collected = random.randint(18, 64)
        ad = int(collected * random.uniform(0.45, 0.75))
        rev = collected - ad - random.randint(0, 4)
        r["review"] = {"collected": collected, "ad": ad, "review": max(rev, 0),
                       "unrelated": max(collected - ad - rev, 0), "evidence": 0}
        for key, pool in QUAL_POOL.items():
            if random.random() < 0.45:
                pol, text = random.choice(pool)
                cnt = random.randint(config.MIN_EVIDENCE, 7)
                r["aspects"][key]["qual"] = {
                    "text": text, "polarity": pol, "evidenceCount": cnt,
                    "sources": mock_sources(cnt)}
                r["review"]["evidence"] += cnt
        for k in totals:
            totals[k] += r["review"].get(k, 0)

    out = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "isMock": True,
        "region": config.REGION,
        "aspects": config.ASPECTS,
        "minEvidence": config.MIN_EVIDENCE,
        "disclaimer": config.DISCLAIMER,
        "stats": stats,
        "reviewStats": totals,
        "dongSummary": {
            "collected": 37,
            "items": [{"key": "amenity", "label": "교통·생활편의", "polarity": "mixed",
                       "text": "일부 후기에서 광주 방면 버스 배차가 아쉽다는 언급이 있음",
                       "evidenceCount": 5, "sources": mock_sources(5)}],
        },
        "sources": {
            "quant": "국토교통부 공동주택관리정보시스템(K-apt) 단지목록·기본정보·공용관리비, "
                     "국토교통부 아파트 매매 실거래가",
            "qual": "Tavily 검색 API(카페·블로그·웹), 유튜브 Data API(댓글)",
            "feeBaseMonth": "202606",
            "tradeWindow": "202509~202608",
            "llm": "claude-opus-5",
            "endpoints": {},
            "rule": "동 내 단지 분포에서 상위 25%(Q3) 밖이면 장점, 하위 25%(Q1) 밖이면 단점. "
                    "중간 50%는 카드를 만들지 않음.",
        },
        "complexes": rows,
    }
    config.DATA_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    config.WEB_PUBLIC.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.DATA_JSON, config.WEB_PUBLIC / "data.json")
    print("✔ 데모 data.json 생성 — 단지 {0}개".format(len(rows)))
    print("  data/data.json 및 web/public/data.json 에 기록했습니다.")
    print("  실제 데이터로 바꾸려면 base.py → collect.py → summarize.py 를 실행하세요.")


if __name__ == "__main__":
    main()
