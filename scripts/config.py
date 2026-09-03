# -*- coding: utf-8 -*-
"""
공통 설정 · 환경변수 로딩 · 경로 정의

이 파일 하나만 고치면 다른 지역으로 확장할 수 있다.
(REGION 의 시군구코드 / 동 이름 / 지도 중심좌표만 교체)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

# ---------------------------------------------------------------- 경로
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WEB_PUBLIC = ROOT / "web" / "public"

BASE_JSON = DATA_DIR / "base.json"            # ① base.py 산출물
COORDS_JSON = DATA_DIR / "coords.json"        # 좌표 수동 보정 파일 (선택)
REVIEWS_RAW_JSON = DATA_DIR / "reviews_raw.json"  # ② collect.py 산출물
DATA_JSON = DATA_DIR / "data.json"            # ③ summarize.py 산출물 (프론트가 읽는 파일)

DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- .env 로딩
def load_env(path: Path | None = None) -> None:
    """프로젝트 루트의 .env 를 os.environ 에 적재한다. (외부 의존성 없음)"""
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_env()

# Windows 콘솔(cp949)에서 한글/㎡ 출력이 깨지거나 예외가 나지 않도록 UTF-8 로 고정
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def env(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(key, default)
    if required and not val:
        print(f"[설정 오류] 환경변수 {key} 가 없습니다. .env 파일을 확인하세요.", file=sys.stderr)
        sys.exit(1)
    return val


# ---------------------------------------------------------------- 대상 지역
# 2026년 전라남도·광주광역시가 '전남광주통합특별시'로 통합되면서 시도 코드가
# 46(전남)/29(광주) 에서 12 로 바뀌었다. K-apt 쪽 마스터 데이터는 이미 새 코드를
# 쓰므로, 옛 코드(46170)로는 이 동네 단지가 0건으로 나온다 — 실제로 겪은 문제였다.
# sido 표시 문구는 과도기라 기관마다 다르게 쓰길래(전남광주통합특별시/광주전남특별시 등)
# 실측으로 확인된 K-apt 표기를 그대로 따랐다.
REGION = {
    "sido": "전남광주통합특별시",
    "sigungu": "나주시",
    "dong": "빛가람동",
    # 법정동 시군구코드 5자리 (나주시, 통합 이후 신코드). 실거래가 API 의 LAWD_CD 도
    # 같은 개편을 따라갔을 가능성이 높지만 이 프로젝트에서는 아직 검증하지 못했다 —
    # apt_trade 가 이 코드로 0건만 나오면 옛 코드(46170)로도 시도해볼 것.
    "sigungu_code": "12170",
    # 법정동코드 10자리 — 실측으로 확인된 빛가람동 신코드를 기본값으로 넣어 뒀다.
    # 다른 동으로 바꿀 때는 .env 의 BJD_CODE 로 덮어쓰면 된다.
    "bjd_code": env("BJD_CODE", "1217013400"),
    # 지도 초기 중심 (빛가람동 중심부)
    "center": {"lat": 35.0208, "lng": 126.7900},
    # Leaflet(OpenStreetMap) 줌 레벨 — 숫자가 클수록 확대. 14면 동 전체가 들어온다.
    "zoom": 14,
}

# 정성 요약을 만들기 위한 최소 근거 건수 (기획서: 3건 미만이면 정량만)
MIN_EVIDENCE = int(env("MIN_EVIDENCE", "3"))

# 사분위 판정에 필요한 최소 단지 수 (표본이 너무 적으면 판정하지 않는다)
MIN_SAMPLE = 6

# 화면 하단 고정 면책 문구
DISCLAIMER = (
    "AI가 공개된 후기를 요약한 것으로 사실과 다를 수 있으며, "
    "거주·투자 판단의 단독 근거로 쓰지 마세요."
)

# ---------------------------------------------------------------- 공공데이터 엔드포인트
# 공공데이터포털 '활용신청 상세' 화면의 End Point 를 그대로 붙여 넣으면 된다.
# 오퍼레이션 이름(getSigunguAptList4 등)은 kapt_api.py 가 버전 접미사를 붙여가며
# 자동으로 찾으므로 여기에는 서비스 주소까지만 적는다.
ENDPOINTS = {
    "apt_list":  "https://apis.data.go.kr/1613000/AptListService4",
    "apt_basis": "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5",
    "apt_cost":  "https://apis.data.go.kr/1613000/AptCmnuseManageCostServiceV3",
    "apt_trade": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade",
}

# ---------------------------------------------------------------- API 키


def _service_key(raw: str | None) -> str | None:
    """
    공공데이터포털 인증키 정규화.

    포털은 같은 키를 Encoding(퍼센트 인코딩) / Decoding 두 형태로 보여준다.
    requests 가 파라미터를 다시 인코딩하기 때문에 Encoding 값을 그대로 쓰면
    '%2F' 가 '%252F' 가 되어 인증에 실패한다. 인코딩된 형태면 여기서 되돌린다.
    (Decoding 값은 base64 문자만 쓰므로 '%' 가 들어갈 일이 없어 오탐이 없다.)
    """
    if not raw:
        return raw
    raw = raw.strip()
    if re.search(r"%[0-9A-Fa-f]{2}", raw):
        return unquote(raw)
    return raw


# 공용관리비는 '총액 한 번에 조회'하는 오퍼레이션이 없다. 실제 Swagger 명세를 직접
# 확인해 보니 인건비·청소비·경비비 등 17개 항목이 전부 별도 오퍼레이션으로 쪼개져
# 있고, 이걸 다 불러서 더해야 총액이 나온다. (key, 오퍼레이션 이름의 뼈대, 표시용 라벨)
COST_CATEGORIES = [
    ("labor", "getHsmpLaborCostInfo", "인건비"),
    ("taxdue", "getHsmpTaxdueInfo", "제세공과금"),
    ("vehicle", "getHsmpVhcleMntncCostInfo", "차량유지비"),
    ("etc", "getHsmpEtcCostInfo", "기타부대비용"),
    ("office", "getHsmpOfcrkCostInfo", "제사무비"),
    ("clothing", "getHsmpClothingCostInfo", "피복비"),
    ("training", "getHsmpEduTraingCostInfo", "교육훈련비"),
    ("cleaning", "getHsmpCleaningCostInfo", "청소비"),
    ("guard", "getHsmpGuardCostInfo", "경비비"),
    ("disinfection", "getHsmpDisinfectionCostInfo", "소독비"),
    ("elevator", "getHsmpElevatorMntncCostInfo", "승강기유지비"),
    ("homenetwork", "getHsmpHomeNetworkMntncCostInfo", "홈네트워크유지비"),
    ("repairs", "getHsmpRepairsCostInfo", "수선비"),
    ("facility", "getHsmpFacilityMntncCostInfo", "시설유지비"),
    ("safety", "getHsmpSafetyCheckUpCostInfo", "안전점검비"),
    ("disaster", "getHsmpDisasterPreventionCostInfo", "재해예방비"),
    ("consign", "getHsmpConsignManageFeeInfo", "위탁관리수수료"),
]

KAPT_SERVICE_KEY = _service_key(env("KAPT_SERVICE_KEY"))
# 좌표 조회 — 둘 다 무료. VWorld 키가 있으면 먼저 쓰고, 없으면 OSM Nominatim 을 쓴다.
VWORLD_API_KEY = env("VWORLD_API_KEY")
NOMINATIM_EMAIL = env("NOMINATIM_EMAIL", "")      # Nominatim 이용 정책상 연락처 권장
TAVILY_API_KEY = env("TAVILY_API_KEY")            # 후기 수집용 검색 API
YOUTUBE_API_KEY = env("YOUTUBE_API_KEY")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")

# ---------------------------------------------------------------- 후기 검색 정책
# 아파트 리뷰 전문 플랫폼(호갱노노 등)은 실거주 후기가 가장 잘 쌓여 있는 곳이다.
# 이 프로젝트는 직접 크롤링하지 않고 Tavily 검색 API의 공개 색인에서 제목·발췌·링크만
# 받아 오므로, 기본값은 이 목록을 검색 대상에서 빼지 않는다.
# 다만 '반복적이거나 체계적인' 수집은 개별 소재가 상당한 부분에 못 미쳐도
# 데이터베이스제작자 권리 침해로 볼 수 있다는 저작권법 93조 조항이 있어(관련
# 판례: 특허법원 2025.12), 더 보수적으로 가고 싶다면 collect.py 를
# --exclude-review-platforms 로 실행해 이 목록을 검색에서 뺄 수 있다.
# (도메인 차단은 최선의 노력이다. 같은 내용을 미러링하는 사이트나 유사 도메인까지
#  전부 막을 수는 없어, 실행 후 reviews_raw.json 의 도메인 분포를 한 번 훑어보고
#  새로 보이는 곳을 여기에 추가하는 방식으로 관리한다.)
REVIEW_PLATFORM_DOMAINS = [
    "hogangnono.com", "hogengnono.com",
    "zippoom.com", "zimssa.com",
    "zaritalk.com",
    "daangn.com",
    "aptrank.com", "apt2.me",
    "modu.kr",
    "asil.kr",
]

# 주소만 같을 뿐 거주 후기와 무관한 서비스 (여행·숙박·예약·지도 스크랩 등).
# 여기서 걸러야 ③ LLM 분류에 쓰는 토큰이 낭비되지 않는다.
IRRELEVANT_DOMAINS = [
    "airbnb.co.kr", "airbnb.com", "expedia.co.kr", "hotels.com", "trip.com",
    "agoda.com", "booking.com", "yanolja.com", "goodchoice.kr",
    "catchtable.co.kr", "tiktok.com", "instagram.com", "facebook.com",
    "chargekorea.com", "udanax.org",
]

# ---------------------------------------------------------------- 6개 판정 항목
# key: 내부 키 / label: 화면 표기 / higher_is_better: 값이 클수록 장점인가
ASPECTS = [
    {"key": "parking",   "label": "주차",        "unit": "대/세대", "higher_is_better": True},
    {"key": "fee",       "label": "관리비",      "unit": "원/㎡",   "higher_is_better": False},
    {"key": "security",  "label": "보안·관리",   "unit": "세대/경비원", "higher_is_better": False},
    {"key": "scale",     "label": "규모",        "unit": "세대",    "higher_is_better": True},
    {"key": "age",       "label": "노후도",      "unit": "년",      "higher_is_better": True},
    {"key": "amenity",   "label": "교통·생활편의", "unit": "개",     "higher_is_better": True},
]
ASPECT_KEYS = [a["key"] for a in ASPECTS]
ASPECT_LABEL = {a["key"]: a["label"] for a in ASPECTS}
