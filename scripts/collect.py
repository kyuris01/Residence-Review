# -*- coding: utf-8 -*-
"""
② collect.py — 후기 원문 수집  →  data/reviews_raw.json

  · Tavily 검색 API : 단지당 일반 웹 검색 4회(검색어 변형). 아파트 리뷰 전문 플랫폼
                      (호갱노노 등)도 기본으로 검색 대상에 포함한다 — Tavily의 공개
                      색인을 통해 제목·발췌·링크만 받아 오는 것이라 직접 크롤링과는
                      다르다고 보되, --exclude-review-platforms 로 언제든 뺄 수 있다.
                      결과 본문에 단지명이 있으면 그 단지로, 동 이름만 있으면 '동 전체' 버킷.
  · 유튜브 Data API : search.list 로 지역 영상 탐색 → commentThreads.list 로 댓글 수집.
                      댓글 본문에서 단지명이 확인되면 그 단지로, 아니면 '동 전체' 버킷.

  이 스크립트는 검색 결과의 제목·발췌·링크만 저장한다. 페이지 전문을 복제하지 않는다.
  광고 분류·요약은 ③ summarize.py 가 한다.
  base.json 이 있어야 단지 목록을 알 수 있으므로 ① 을 먼저 실행할 것.

실행:  python scripts/collect.py
       python scripts/collect.py --no-youtube               (P1 인 유튜브 수집 생략)
       python scripts/collect.py --limit 5
       python scripts/collect.py --community-pass            (카페·블로그 한정 검색 추가)
       python scripts/collect.py --exclude-review-platforms  (리뷰 전문 플랫폼 제외)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests

import config

KST = timezone(timedelta(hours=9))

TAVILY_URL = "https://api.tavily.com/search"

# 카페·블로그를 먼저 훑기 위한 도메인 제한 목록.
# 기획서의 '지역 카페·입주민 카페 글이 블로그보다 광고 비율이 낮다'는 순서를 유지한다.
COMMUNITY_DOMAINS = [
    "cafe.naver.com", "m.cafe.naver.com",
    "cafe.daum.net",
    "blog.naver.com", "m.blog.naver.com",
    "tistory.com", "brunch.co.kr",
]

# URL 호스트로 출처를 분류한다 (화면 배지와 summarize 프롬프트에 쓰인다)
SOURCE_BY_HOST = [
    ("cafe.naver.com", "naver_cafe"),
    ("cafe.daum.net", "daum_cafe"),
    ("blog.naver.com", "naver_blog"),
    ("tistory.com", "blog"),
    ("brunch.co.kr", "blog"),
]

# 거주 관련 어휘. 단지명·동 이름만 스치는 페이지(숙박·예약·지도 스크랩 등)를
# LLM 에 넘기기 전에 걸러낸다. 실제 후기라면 이 중 하나는 거의 반드시 들어간다.
TOPIC_HINTS = [
    "후기", "거주", "살아", "살고", "살기", "입주", "이사", "실거주",
    "관리비", "주차", "층간소음", "소음", "단지", "세대", "경비",
    "학교", "학군", "통근", "출퇴근", "버스", "상가", "커뮤니티", "난방",
]

YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_COMMENTS = "https://www.googleapis.com/youtube/v3/commentThreads"

TAG_RE = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    """검색 결과에 섞여 오는 태그·HTML 엔티티 제거."""
    return html.unescape(TAG_RE.sub("", text or "")).strip()


def norm(s: str) -> str:
    return re.sub(r"[\s\-_·.()]", "", s or "")


def url_key(url: str) -> str:
    """
    중복 판정용 URL 정규화.
    같은 글이 blog.naver.com 과 m.blog.naver.com 로 두 번 잡히는 일이 잦아
    m./www. 접두사와 끝 슬래시를 떼고 비교한다.
    """
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    for prefix in ("m.", "www."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    key = host + p.path.rstrip("/")
    return key + ("?" + p.query if p.query else "")


def source_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for needle, label in SOURCE_BY_HOST:
        if host.endswith(needle) or host == needle:
            return label
    for needle in config.REVIEW_PLATFORM_DOMAINS:
        if host.endswith(needle) or host == needle:
            return "review_platform"
    return "web"


# ---------------------------------------------------------------- Tavily 검색
class Tavily:
    """
    Tavily Search API 래퍼.

    basic 검색 1회 = 1 크레딧. 단지당 5회(커뮤니티 한정 4 + 일반 웹 1)를 쓰므로
    단지 30개면 150 크레딧, 무료 월 1,000 크레딧 안에서 여러 번 돌릴 수 있다.
    응답의 content 는 페이지 전문이 아니라 질의와 관련된 발췌라, 원문을 통째로
    복제하지 않는다는 기획서 원칙과도 맞다. (raw_content 는 요청하지 않는다)
    """

    def __init__(self, key: str):
        self.key = key
        self.calls = 0
        self.credits = 0
        self.locale_ok = True     # country/language 파라미터를 서버가 받아주는지

    def search(self, query: str, include_domains=None, max_results: int = 15,
               exclude_domains=None) -> list[dict]:
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_raw_content": False,
            "include_answer": False,
            "include_usage": True,
        }
        if include_domains:
            payload["include_domains"] = list(include_domains)
        if exclude_domains:
            payload["exclude_domains"] = list(exclude_domains)
        if self.locale_ok:
            payload.update({"country": "south korea", "language": "ko"})

        try:
            res = requests.post(
                TAVILY_URL, timeout=30, json=payload,
                headers={"Authorization": "Bearer " + self.key,
                         "Content-Type": "application/json"})
        except Exception as exc:
            print("  [Tavily] 요청 실패: {0}".format(exc))
            return []
        finally:
            time.sleep(0.15)

        if res.status_code == 400 and self.locale_ok:
            # country/language 를 안 받는 플랜/버전이면 한 번만 빼고 재시도한다
            self.locale_ok = False
            print("  [Tavily] country/language 옵션 없이 재시도합니다.")
            return self.search(query, include_domains, max_results, exclude_domains)
        if res.status_code == 401:
            print("  [Tavily] 인증 실패 — .env 의 TAVILY_API_KEY 를 확인하세요.")
            return []
        if res.status_code == 429:
            print("  [Tavily] 크레딧/요청 한도 초과 — 남은 검색을 건너뜁니다.")
            raise RuntimeError("tavily_quota")
        if res.status_code != 200:
            print("  [Tavily] HTTP {0} — {1}".format(res.status_code, res.text[:160]))
            return []

        data = res.json()
        self.calls += 1
        self.credits += (data.get("usage") or {}).get("credits", 1)

        out = []
        for it in data.get("results", []):
            url = it.get("url") or ""
            if not url:
                continue
            out.append({
                "source": source_of(url),
                "title": clean(it.get("title")),
                "url": url,
                "text": clean(it.get("content")),
                "date": (it.get("published_date") or "")[:10],
                "author": (urlparse(url).hostname or ""),
                "score": it.get("score"),
                "query": query,
            })
        return out


def query_variants(name: str, dong: str) -> list[str]:
    """단지당 검색어 4개 변형 (기획서 3번 기능)."""
    return [
        "{0} {1} 아파트 거주 후기".format(dong, name),
        "{0} 실거주 장단점".format(name),
        "{0} 관리비".format(name),
        "{0} 주차".format(name),
    ]


# ---------------------------------------------------------------- 유튜브
def yt_search_videos(queries: list[str], per_query: int, key: str) -> list[dict]:
    videos, seen = [], set()
    for q in queries:
        try:
            res = requests.get(YT_SEARCH, timeout=15, params={
                "part": "snippet", "q": q, "type": "video",
                "maxResults": per_query, "regionCode": "KR",
                "relevanceLanguage": "ko", "key": key,
            })
            if res.status_code != 200:
                print("  [유튜브 search] HTTP {0} — {1}".format(res.status_code,
                                                               res.text[:160]))
                return videos
            for it in res.json().get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append({
                    "videoId": vid,
                    "title": clean((it.get("snippet") or {}).get("title")),
                    "publishedAt": (it.get("snippet") or {}).get("publishedAt", "")[:10],
                    "query": q,
                })
        except Exception as exc:
            print("  [유튜브 search] 실패: {0}".format(exc))
        time.sleep(0.1)
    return videos


def yt_comments(video: dict, key: str, max_comments: int = 100) -> list[dict]:
    try:
        res = requests.get(YT_COMMENTS, timeout=15, params={
            "part": "snippet", "videoId": video["videoId"],
            "maxResults": min(max_comments, 100), "order": "relevance",
            "textFormat": "plainText", "key": key,
        })
        if res.status_code != 200:
            return []                       # 댓글 사용중지 영상 등은 조용히 통과
        items = res.json().get("items", [])
    except Exception:
        return []
    finally:
        time.sleep(0.1)

    out = []
    for it in items:
        top = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        text = clean(top.get("textDisplay"))
        if len(text) < 15:                  # 너무 짧은 댓글은 근거가 되지 않는다
            continue
        out.append({
            "source": "youtube_comment",
            "title": video["title"],
            "url": "https://www.youtube.com/watch?v={0}&lc={1}".format(
                video["videoId"], it.get("id", "")),
            "text": text,
            "date": (top.get("publishedAt") or "")[:10],
            "author": clean(top.get("authorDisplayName")),
            "query": video["query"],
        })
    return out


def aliases_for(name: str) -> list[str]:
    """
    댓글에서 단지를 식별할 별칭.
    3자 이상 토큰만 쓴다(오탐 방지). 예: '빛가람 중흥S클래스' → ['빛가람중흥S클래스', '중흥S클래스']
    """
    base = norm(name)
    out = {base} if len(base) >= 3 else set()
    for tok in re.split(r"[\s\-_]", name or ""):
        tok = norm(tok)
        if len(tok) >= 3 and tok not in ("빛가람", "아파트", "혁신도시"):
            out.add(tok)
    return sorted(out, key=len, reverse=True)


# ---------------------------------------------------------------- 메인
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-youtube", action="store_true", help="유튜브 수집 생략 (P1)")
    ap.add_argument("--max-results", type=int, default=15,
                    help="Tavily 검색 1회당 결과 수 (1~20)")
    ap.add_argument("--community-pass", action="store_true",
                    help="카페·블로그 도메인 한정 검색을 단지당 2회 추가한다 (크레딧 +2)")
    ap.add_argument("--exclude-review-platforms", action="store_true",
                    help="아파트 리뷰 전문 플랫폼(호갱노노 등)을 검색에서 제외한다")
    ap.add_argument("--yt-queries", type=int, default=6, help="유튜브 search.list 호출 수")
    ap.add_argument("--yt-videos", type=int, default=12, help="댓글을 가져올 영상 수 상한")
    args = ap.parse_args()

    if not config.BASE_JSON.exists():
        print("[중단] data/base.json 이 없습니다. 먼저 scripts/base.py 를 실행하세요.")
        sys.exit(1)
    base = json.loads(config.BASE_JSON.read_text(encoding="utf-8"))
    complexes = base["complexes"][: args.limit] if args.limit else base["complexes"]
    dong = base["region"]["dong"]

    by_complex, dong_wide, seen_urls = {}, [], set()
    tv = None
    exclude = list(config.IRRELEVANT_DOMAINS)
    if args.exclude_review_platforms:
        exclude = config.REVIEW_PLATFORM_DOMAINS + exclude

    # ---------------- Tavily 검색 (P0) ----------------
    if not config.TAVILY_API_KEY:
        print("[안내] TAVILY_API_KEY 가 없어 웹 후기 수집을 건너뜁니다.")
    else:
        tv = Tavily(config.TAVILY_API_KEY)
        per = len(query_variants("x", dong)) + (2 if args.community_pass else 0)
        print("■ Tavily 검색 수집 (단지당 {0}회 · 예상 {1} 크레딧)".format(
            per, len(complexes) * per))
        if args.exclude_review_platforms:
            print("  리뷰 플랫폼 제외(--exclude-review-platforms): {0}".format(
                ", ".join(config.REVIEW_PLATFORM_DOMAINS)))
        else:
            print("  아파트 리뷰 전문 플랫폼(호갱노노 등)도 검색 대상에 포함합니다.")
        for i, c in enumerate(complexes, 1):
            aliases = aliases_for(c["name"])
            docs, spill, dropped = [], 0, 0
            try:
                hits = []
                # 주력: 일반 웹 검색 4회. (카페·블로그 도메인 한정 검색은 네이버가
                #  로그인 벽으로 색인을 막아 실측상 결과가 거의 잡히지 않는다)
                for q in query_variants(c["name"], dong):
                    hits += tv.search(q, max_results=args.max_results,
                                      exclude_domains=exclude)
                if args.community_pass:
                    for q in query_variants(c["name"], dong)[:2]:
                        hits += tv.search(q, include_domains=COMMUNITY_DOMAINS,
                                          max_results=args.max_results,
                                          exclude_domains=exclude)
            except RuntimeError:
                print("  크레딧 한도로 Tavily 수집을 중단합니다.")
                by_complex[c["kaptCode"]] = {"name": c["name"], "docs": docs}
                break

            for d in hits:
                key = url_key(d["url"])
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                d["id"] = "t{0}".format(len(seen_urls))
                body = norm(d["title"] + " " + d["text"])
                if not any(h in body for h in TOPIC_HINTS):
                    dropped += 1                        # 거주 얘기가 아예 없는 페이지
                    continue
                if any(a in body for a in aliases):
                    docs.append(d)                      # 단지명이 확인된 글
                elif dong in body:
                    dong_wide.append(d)                 # 동 이름만 있는 글 → '동 전체'
                    spill += 1
                else:
                    dropped += 1                        # 대상과 무관 → 버린다
            by_complex[c["kaptCode"]] = {"name": c["name"], "docs": docs}
            print("  [{0}/{1}] {2} — 단지 {3}건 / 동 전체 {4}건 / 제외 {5}건".format(
                i, len(complexes), c["name"], len(docs), spill, dropped))
        print("  Tavily 호출 {0}회 · {1} 크레딧 사용".format(tv.calls, tv.credits))

    # ---------------- 유튜브 (P1) ----------------
    if args.no_youtube:
        print("[안내] --no-youtube 로 유튜브 수집을 생략했습니다.")
    elif not config.YOUTUBE_API_KEY:
        print("[안내] 유튜브 키가 없어 댓글 수집을 건너뜁니다. (P1이라 없어도 무방)")
    else:
        print("■ 유튜브 Data API 수집")
        yt_queries = [
            "{0} 아파트".format(dong),
            "나주 혁신도시 아파트",
            "{0} 살기".format(dong),
            "나주 혁신도시 살기 어때",
            "{0} 아파트 후기".format(dong),
            "나주 혁신도시 이사",
        ][: args.yt_queries]
        videos = yt_search_videos(yt_queries, per_query=10, key=config.YOUTUBE_API_KEY)
        print("  영상 {0}개 탐색 (댓글 수집 상한 {1}개)".format(len(videos), args.yt_videos))

        alias_map = [(c["kaptCode"], aliases_for(c["name"])) for c in complexes]
        matched = 0
        for v in videos[: args.yt_videos]:
            for d in yt_comments(v, config.YOUTUBE_API_KEY):
                key = url_key(d["url"])
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                d["id"] = "y{0}".format(len(seen_urls))
                body = norm(d["text"])
                target = None
                for code, aliases in alias_map:
                    if any(a in body for a in aliases):
                        target = code
                        break
                if target:
                    bucket = by_complex.setdefault(target, {"name": "", "docs": []})
                    bucket["docs"].append(d)
                    matched += 1
                else:
                    dong_wide.append(d)     # 단지 특정 불가 → '동 전체' 버킷
        print("  댓글 {0}건 (단지 배분 {1} / 동 전체 {2})".format(
            matched + len(dong_wide), matched, len(dong_wide)))

    for c in complexes:
        by_complex.setdefault(c["kaptCode"], {"name": c["name"], "docs": []})
        by_complex[c["kaptCode"]]["name"] = c["name"]

    total = sum(len(v["docs"]) for v in by_complex.values()) + len(dong_wide)
    out = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "region": base["region"],
        "byComplex": by_complex,
        "dongWide": dong_wide,
        "stats": {
            "total": total,
            "dongWide": len(dong_wide),
            "perComplex": {k: len(v["docs"]) for k, v in by_complex.items()},
            "tavilyCalls": tv.calls if tv else 0,
            "tavilyCredits": tv.credits if tv else 0,
        },
        "policy": {"excludedDomains": exclude},
    }
    config.REVIEWS_RAW_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print("\n✔ {0} 생성 — 원문 {1}건".format(config.REVIEWS_RAW_JSON, total))


if __name__ == "__main__":
    main()
