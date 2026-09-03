# -*- coding: utf-8 -*-
"""
① base.py — 정량 데이터 수집 & 규칙 기반 장단점 생성  →  data/base.json

  1) K-apt 단지 목록      : 나주시 단지 중 주소에 '빛가람동'이 들어가는 단지만
  2) K-apt 기본정보/상세  : 세대수·동수·난방·주차·경비인원·편의시설 …
  3) K-apt 공용관리비     : 최근 월 기준 ㎡당 공용관리비
  4) 아파트 매매 실거래가 : 최근 12개월, 단지명 매칭
  5) 좌표                 : VWorld/OSM Nominatim 으로 개발 중 1회 조회 후 고정
                            (지도는 OpenStreetMap 이라 지도 API 키가 필요 없다)
  6) 파생값 + 동 내 사분위 판정으로 6개 항목 장단점 카드 생성 (LLM 미사용)

실행:  python scripts/base.py                 (전체)
       python scripts/base.py --limit 5       (앞 5개만, 빠른 점검용)
       python scripts/base.py --geocode-only  (빠진 좌표만 다시 채우기)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

import config
import kapt_api as api
from kapt_api import ApiUnavailable

KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------- 작은 유틸
def num(v, default=None):
    """'1,234' · '1234.5' · '' 를 float 로. 실패하면 default."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "null", "None"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def norm_name(s: str) -> str:
    """단지명 비교용 정규화 — 공백/괄호/'아파트' 제거."""
    s = re.sub(r"\(.*?\)", "", s or "")
    s = re.sub(r"[\s\-_·.]", "", s)
    return s.replace("아파트", "")


def quantile(values: list[float], q: float) -> float:
    """선형보간 분위수 (numpy 없이)."""
    xs = sorted(values)
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def recent_months(n: int, back: int = 1) -> list[str]:
    """오늘 기준 back 개월 전부터 과거로 n 개월치 YYYYMM."""
    out = []
    cur = datetime.now(KST).replace(day=1)
    for _ in range(back):
        cur = (cur - timedelta(days=1)).replace(day=1)
    for _ in range(n):
        out.append(cur.strftime("%Y%m"))
        cur = (cur - timedelta(days=1)).replace(day=1)
    return out


# ---------------------------------------------------------------- 1) 단지 목록
def fetch_complex_list() -> list[dict]:
    dong = config.REGION["dong"]
    items = []
    if config.REGION.get("bjd_code"):
        try:
            items = api.call("apt_list_bjd",
                             {"bjdCode": config.REGION["bjd_code"]}, quiet=True)
        except ApiUnavailable:
            items = []
    if not items:
        items = api.call("apt_list_sigungu",
                         {"sigunguCode": config.REGION["sigungu_code"]})

    out = []
    for it in items:
        # as4(리) 등은 JSON null 로 오는 경우가 많다. 키는 존재하되 값이 None 이라
        # it.get(k, "") 의 기본값이 적용되지 않고 str(None) == "None" 이 그대로
        # 주소에 섞여 들어가므로, None 은 명시적으로 빈 문자열로 바꾼다.
        addr_parts = " ".join(str(it.get(k) or "") for k in
                              ("as1", "as2", "as3", "as4", "kaptAddr"))
        name = it.get("kaptName") or it.get("kaptname") or ""
        if dong in addr_parts or dong in name:
            out.append({
                "kaptCode": it.get("kaptCode") or it.get("kaptcode"),
                "name": name,
                "listAddr": addr_parts.strip(),
                "bjdCode": it.get("bjdCode"),
            })
    seen, uniq = set(), []
    for c in out:
        if c["kaptCode"] and c["kaptCode"] not in seen:
            seen.add(c["kaptCode"])
            uniq.append(c)
    return uniq


# ---------------------------------------------------------------- 2) 기본/상세
def fetch_info(kapt_code: str) -> dict:
    info = {}
    for op in ("apt_basis", "apt_detail"):
        try:
            items = api.call(op, {"kaptCode": kapt_code}, quiet=True)
            if items:
                info.update(items[0])
        except ApiUnavailable:
            pass
    return info


# ---------------------------------------------------------------- 3) 공용관리비
COST_EXCLUDE_KEYS = {"kaptCode", "kaptName", "searchDate", "resultCode", "resultMsg"}


def fetch_common_fee(kapt_code: str, months: list[str]):
    """
    '공용관리비 총액'을 한 번에 주는 오퍼레이션은 없다. 인건비·청소비·경비비·
    승강기유지비·수선비·위탁관리수수료 등 config.COST_CATEGORIES 의 17개 항목이
    전부 별도 오퍼레이션이라, 달마다 17번 호출해 각 응답의 숫자 필드를 모두 더한다.
    (그래서 항목당 개별 필드명을 하드코딩하지 않고, 응답에 실제로 들어있는 숫자
     필드를 그대로 합산하는 방식을 그대로 유지했다 — 항목 자체가 이미 좁게
     나뉘어 있어 이 방식이 안전하다.)

    값이 하나라도 잡히는 가장 최근 달을 쓰고, 그 달을 기준월로 함께 돌려준다.
    한 달 전체가 아직 공시되지 않은 경우를 대비해 여러 달을 시도하되, 항목이
    17개라 달마다 호출 비용이 크므로 무한정 거슬러 올라가지 않는다.
    """
    for ym in months:
        total = 0.0
        got_any = False
        for key, _stem, _label in config.COST_CATEGORIES:
            try:
                items = api.call("cost_" + key,
                                 {"kaptCode": kapt_code, "searchDate": ym}, quiet=True)
            except ApiUnavailable:
                continue                 # 이 항목 오퍼레이션만 안 되는 경우 — 나머지는 계속
            if not items:
                continue
            got_any = True
            for k, v in items[0].items():
                if k in COST_EXCLUDE_KEYS:
                    continue
                f = num(v)
                if f is not None and f >= 0:
                    total += f
        if got_any and total > 0:
            return total, ym
    return None, None


# ---------------------------------------------------------------- 4) 실거래가
def fetch_trades(months: list[str]) -> list[dict]:
    rows = []
    for ym in months:
        try:
            items = api.call("apt_trade",
                             {"LAWD_CD": config.REGION["sigungu_code"], "DEAL_YMD": ym},
                             quiet=True)
        except ApiUnavailable:
            print("[안내] 실거래가 API 를 쓸 수 없어 최근 실거래가는 비워 둡니다.")
            return []
        for it in items:
            name = it.get("aptNm") or it.get("아파트") or ""
            raw_amount = str(it.get("dealAmount") or it.get("거래금액") or "").replace(" ", "")
            amount = num(raw_amount)
            if not name or amount is None:
                continue
            rows.append({
                "name": name.strip(),
                "amount": amount,                       # 만원 단위
                "area": num(it.get("excluUseAr") or it.get("전용면적")),
                "floor": num(it.get("floor") or it.get("층")),
                "ym": ym,
                "day": num(it.get("dealDay") or it.get("일")) or 0,
            })
    return rows


def match_trade(name: str, trades: list[dict]):
    key = norm_name(name)
    if not key:
        return None
    hit = [t for t in trades
           if key in norm_name(t["name"]) or norm_name(t["name"]) in key]
    if not hit:
        return None
    hit.sort(key=lambda t: (t["ym"], t["day"]), reverse=True)
    latest = hit[0]
    amounts = sorted(t["amount"] for t in hit)
    return {
        "amount": latest["amount"], "area": latest["area"], "floor": latest["floor"],
        "ym": latest["ym"], "count": len(hit),
        "medianAmount": amounts[len(amounts) // 2],
    }


# ---------------------------------------------------------------- 5) 좌표
# 지도는 OpenStreetMap(Leaflet)을 쓰므로 좌표만 있으면 되고, 지도 API 키는 필요 없다.
# 우선순위: data/coords.json 수동값 → VWorld 지오코더(키 있을 때) → OSM Nominatim
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
VWORLD_URL = "https://api.vworld.kr/req/address"
UA = "bitgaram-apt-map/1.0 (batch geocoder; {0})".format(
    config.NOMINATIM_EMAIL or "no-contact-provided")

# 좌표가 대상 지역에서 크게 벗어나면 오매칭으로 보고 버린다 (약 ±20km)
_C = config.REGION["center"]
BBOX = (_C["lat"] - 0.2, _C["lat"] + 0.2, _C["lng"] - 0.25, _C["lng"] + 0.25)


def load_manual_coords() -> dict:
    """data/coords.json 이 있으면 {kaptCode: [lat, lng]} 로 읽는다."""
    if not config.COORDS_JSON.exists():
        return {}
    try:
        raw = json.loads(config.COORDS_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        print("[안내] coords.json 을 읽지 못했습니다: {0}".format(exc))
        return {}
    out = {}
    for code, v in raw.items():
        if isinstance(v, dict):
            lat, lng = num(v.get("lat")), num(v.get("lng"))
        elif isinstance(v, (list, tuple)) and len(v) >= 2:
            lat, lng = num(v[0]), num(v[1])
        else:
            continue
        if lat and lng:
            out[code] = (lat, lng)
    return out


def in_region(lat, lng) -> bool:
    return bool(lat and lng and BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lng <= BBOX[3])


def _vworld(query: str, kind: str):
    """VWorld 지오코더 — 국토부 제공, 무료. 한국 주소 정확도가 가장 좋다."""
    try:
        res = requests.get(VWORLD_URL, timeout=10, params={
            "service": "address", "request": "getcoord", "version": "2.0",
            "crs": "epsg:4326", "type": kind, "address": query,
            "format": "json", "key": config.VWORLD_API_KEY,
        })
        body = res.json().get("response", {})
        if body.get("status") != "OK":
            return None, None
        point = body["result"]["point"]
        return num(point.get("y")), num(point.get("x"))
    except Exception:
        return None, None


def _nominatim(query: str):
    """OSM Nominatim — 키 불필요. 이용 정책상 초당 1회로 제한한다."""
    try:
        res = requests.get(NOMINATIM_URL, timeout=15,
                           headers={"User-Agent": UA, "Accept-Language": "ko"},
                           params={"q": query, "format": "jsonv2", "limit": 5,
                                   "countrycodes": "kr"})
        time.sleep(1.1)                      # Nominatim 이용 정책: 1 req/s
        for d in res.json():
            lat, lng = num(d.get("lat")), num(d.get("lon"))
            if in_region(lat, lng):
                return lat, lng
    except Exception:
        time.sleep(1.1)
    return None, None


def geocode(name: str, addr: str, road_addr: str = ""):
    region = "{0} {1} {2}".format(
        config.REGION["sido"], config.REGION["sigungu"], config.REGION["dong"])

    if config.VWORLD_API_KEY:
        for query, kind in ((road_addr, "road"), (addr, "parcel"), (addr, "road")):
            if not (query or "").strip():
                continue
            lat, lng = _vworld(query, kind)
            if in_region(lat, lng):
                return lat, lng

    for query in (road_addr, addr, "{0} {1}".format(region, name)):
        if not (query or "").strip():
            continue
        lat, lng = _nominatim(query)
        if lat:
            return lat, lng
    return None, None


# ---------------------------------------------------------------- 6) 파생값
def count_facilities(*fields: str) -> int:
    tokens = set()
    for f in fields:
        for t in re.split(r"[,/·\n\t]|\s{2,}", str(f or "")):
            t = t.strip(" .-()")
            if len(t) >= 2:
                tokens.add(t)
    return len(tokens)


def build_metrics(info: dict) -> dict:
    house = num(info.get("kaptdaCnt"))                        # 세대수
    park = (num(info.get("kaptdPcnt"), 0) or 0) + (num(info.get("kaptdPcntu"), 0) or 0)
    guards = num(info.get("kaptdScnt"))                       # 경비 인원
    marea = num(info.get("kaptMarea")) or num(info.get("kaptTarea"))
    used = str(info.get("kaptUsedate") or "")
    year = int(used[:4]) if len(used) >= 4 and used[:4].isdigit() else None
    amenity = count_facilities(info.get("convenientFacility"),
                               info.get("educationFacility"))
    if str(info.get("subwayStation") or "").strip():
        amenity += 1
    return {
        "houseCnt": house,
        "dongCnt": num(info.get("kaptDongCnt")),
        "parkingTotal": park or None,
        "guards": guards,
        "manageArea": marea,
        "builtYear": year,
        "amenityCount": amenity,
        "heatType": info.get("codeHeatNm") or info.get("codeHeatName"),
        "manageType": info.get("codeMgrNm") or info.get("codeMgr"),
        "securityType": info.get("codeSecNm") or info.get("codeSec"),
        "convenient": info.get("convenientFacility"),
        "education": info.get("educationFacility"),
        "bus": info.get("kaptdWtimebus"),
        "subway": info.get("subwayStation"),
        "cctv": num(info.get("kaptdCccnt")),
    }


def aspect_values(m: dict, fee_total):
    """6개 항목의 판정용 수치. 값이 없으면 None."""
    house = m.get("houseCnt")
    return {
        "parking": (m["parkingTotal"] / house) if (m.get("parkingTotal") and house) else None,
        "fee": (fee_total / m["manageArea"]) if (fee_total and m.get("manageArea")) else None,
        "security": (house / m["guards"]) if (house and m.get("guards")) else None,
        "scale": house,
        "age": float(m["builtYear"]) if m.get("builtYear") else None,
        "amenity": float(m["amenityCount"]) if m.get("amenityCount") is not None else None,
    }


def fmt(v, key: str) -> str:
    if v is None:
        return "-"
    if key == "parking":
        return "%.2f대" % v
    if key == "fee":
        return "{:,.0f}원".format(v)
    if key in ("security", "scale"):
        return "{:,.0f}세대".format(v)
    if key == "age":
        return "%.0f년" % v
    return "%.0f개" % v


def quant_text(key: str, v: float, avg: float) -> str:
    """기획서 표기 예: '㎡당 공용관리비 1,150원으로 동 평균 1,340원보다 낮음'"""
    higher = "높음" if v > avg else ("낮음" if v < avg else "같음")
    more = "많음" if v > avg else ("적음" if v < avg else "같음")
    table = {
        "parking": "세대당 주차 {0}로 동 평균 {1}보다 {2}".format(
            fmt(v, "parking"), fmt(avg, "parking"), more),
        "fee": "㎡당 공용관리비 {0}으로 동 평균 {1}보다 {2}".format(
            fmt(v, "fee"), fmt(avg, "fee"), higher),
        "security": "경비원 1명당 {0}로 동 평균 {1}보다 {2}".format(
            fmt(v, "security"), fmt(avg, "security"), more),
        "scale": "{0} 규모로 동 평균 {1}보다 {2}".format(
            fmt(v, "scale"), fmt(avg, "scale"), more),
        "age": "{0} 준공으로 동 평균 {1}보다 {2}".format(
            fmt(v, "age"), fmt(avg, "age"), higher),
        "amenity": "기본정보상 편의·교육시설 {0} 항목으로 동 평균 {1}보다 {2}".format(
            fmt(v, "amenity"), fmt(avg, "amenity"), more),
    }
    return table[key]


# ---------------------------------------------------------------- 메인
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞 N개 단지만 처리 (점검용)")
    ap.add_argument("--skip-geocode", action="store_true")
    ap.add_argument("--geocode-only", action="store_true",
                    help="기존 base.json 의 빠진 좌표만 다시 채운다 (API 재수집 없음)")
    ap.add_argument("--fee-months", type=int, default=3,
                    help="공용관리비를 거슬러 찾을 개월 수 (항목이 17개라 달마다 17회 호출됨)")
    ap.add_argument("--trade-months", type=int, default=12)
    args = ap.parse_args()

    manual = load_manual_coords()
    if manual:
        print("■ coords.json 수동 좌표 {0}개 적용".format(len(manual)))

    if args.geocode_only:
        regeocode(manual)
        return

    if not config.KAPT_SERVICE_KEY:
        print("[중단] .env 의 KAPT_SERVICE_KEY 가 비어 있습니다. "
              "공공데이터포털 '일반 인증키(Decoding)'를 넣어주세요.")
        sys.exit(1)

    print("■ 대상: {0} {1}".format(config.REGION["sigungu"], config.REGION["dong"]))
    complexes = fetch_complex_list()
    if args.limit:
        complexes = complexes[: args.limit]
    print("  단지 {0}개 확인".format(len(complexes)))
    if not complexes:
        print("[중단] 단지 목록이 비었습니다. sigungu_code 와 동 이름을 확인하세요.")
        sys.exit(1)

    fee_months = recent_months(args.fee_months)
    trade_months = recent_months(args.trade_months)
    trades = fetch_trades(trade_months)
    print("  실거래 {0}건 수집".format(len(trades)))

    rows, fee_base_months = [], []
    for i, c in enumerate(complexes, 1):
        info = fetch_info(c["kaptCode"])
        m = build_metrics(info)
        fee_total, fee_ym = fetch_common_fee(c["kaptCode"], fee_months)
        if fee_ym:
            fee_base_months.append(fee_ym)
        addr = info.get("kaptAddr") or c["listAddr"]
        name = info.get("kaptName") or c["name"]
        road = info.get("doroJuso") or ""
        lat = lng = None
        if c["kaptCode"] in manual:
            lat, lng = manual[c["kaptCode"]]
        elif not args.skip_geocode:
            lat, lng = geocode(name, addr, road)
        rows.append({
            "kaptCode": c["kaptCode"],
            "name": name,
            "addr": addr,
            "roadAddr": info.get("doroJuso"),
            "lat": lat,
            "lng": lng,
            "info": {
                "builtYear": m["builtYear"], "useApproveYmd": info.get("kaptUsedate"),
                "houseCnt": m["houseCnt"], "dongCnt": m["dongCnt"],
                "heatType": m["heatType"], "manageType": m["manageType"],
                "securityType": m["securityType"], "builder": info.get("kaptBcompany"),
                "manageArea": m["manageArea"], "parkingTotal": m["parkingTotal"],
                "guards": m["guards"], "cctv": m["cctv"],
                "convenient": m["convenient"], "education": m["education"],
                "bus": m["bus"], "subway": m["subway"],
                "monthlyCommonFee": fee_total, "feeBaseMonth": fee_ym,
                "recentTrade": match_trade(name, trades),
            },
            "values": aspect_values(m, fee_total),
        })
        print("  [{0}/{1}] {2}  ({3} / {4})".format(
            i, len(complexes), name,
            "좌표O" if lat else "좌표X", "관리비O" if fee_total else "관리비X"))

    # ---- 동 내 분포로 사분위 판정 ------------------------------------
    stats = {}
    for a in config.ASPECTS:
        key = a["key"]
        vals = [r["values"][key] for r in rows if r["values"].get(key) is not None]
        if len(vals) < config.MIN_SAMPLE:
            stats[key] = {"n": len(vals), "usable": False}
            continue
        stats[key] = {
            "n": len(vals), "usable": True,
            "min": min(vals), "max": max(vals),
            "avg": sum(vals) / len(vals),
            "q1": quantile(vals, 0.25),
            "median": quantile(vals, 0.5),
            "q3": quantile(vals, 0.75),
        }

    # 항목별 순위표를 미리 만들어 둔다 (좋은 쪽이 1위)
    ordered = {}
    for a in config.ASPECTS:
        key = a["key"]
        ordered[key] = sorted(
            [r["values"][key] for r in rows if r["values"].get(key) is not None],
            reverse=a["higher_is_better"])

    for r in rows:
        r["aspects"] = {}
        for a in config.ASPECTS:
            key, hib = a["key"], a["higher_is_better"]
            v, st = r["values"].get(key), stats[key]
            cell = {"label": a["label"], "unit": a["unit"], "value": v,
                    "verdict": "none", "quantText": None, "qual": None,
                    "n": st["n"], "rank": None}
            if v is not None and st.get("usable"):
                good = v > st["q3"] if hib else v < st["q1"]
                bad = v < st["q1"] if hib else v > st["q3"]
                cell["verdict"] = "pro" if good else ("con" if bad else "none")
                cell["dongAvg"] = st["avg"]
                cell["q1"] = st["q1"]
                cell["q3"] = st["q3"]
                cell["median"] = st["median"]
                cell["quantText"] = quant_text(key, v, st["avg"])
                cell["rank"] = ordered[key].index(v) + 1
            elif v is None:
                cell["quantText"] = "공개 데이터에 해당 수치가 없어 판정하지 않았습니다"
            else:
                cell["quantText"] = "표본이 적어 사분위 판정을 하지 않았습니다"
            r["aspects"][key] = cell

    for r in rows:
        r.pop("values", None)

    out = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "region": config.REGION,
        "aspects": config.ASPECTS,
        "minEvidence": config.MIN_EVIDENCE,
        "disclaimer": config.DISCLAIMER,
        "stats": stats,
        "sources": {
            "quant": "국토교통부 공동주택관리정보시스템(K-apt) 단지목록·기본정보·공용관리비, "
                     "국토교통부 아파트 매매 실거래가",
            "feeBaseMonth": max(fee_base_months) if fee_base_months else None,
            "tradeWindow": trade_months[-1] + "~" + trade_months[0],
            "endpoints": api.resolved_endpoints(),
            "rule": "동 내 단지 분포에서 상위 25%(Q3) 밖이면 장점, 하위 25%(Q1) 밖이면 단점. "
                    "중간 50%는 카드를 만들지 않음.",
        },
        "complexes": rows,
    }
    config.BASE_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    made = sum(1 for r in rows for c in r["aspects"].values() if c["verdict"] != "none")
    print("\n✔ {0} 생성 — 단지 {1}개 / 장단점 카드 {2}개".format(
        config.BASE_JSON, len(rows), made))
    report_missing(rows)


def report_missing(rows: list[dict]) -> None:
    """좌표를 못 찾은 단지를 알려주고, 손으로 채울 수 있는 템플릿을 남긴다."""
    missing = [r for r in rows if not r.get("lat")]
    if not missing:
        print("  좌표 {0}개 모두 확보".format(len(rows)))
        return
    print("\n  좌표 미확보 {0}개 — 지도에 표시되지 않습니다:".format(len(missing)))
    for r in missing:
        print("    · {0}  ({1})".format(r["name"], r.get("roadAddr") or r.get("addr")))
    template = config.COORDS_JSON.with_name("coords.missing.json")
    existing = load_manual_coords()
    body = {r["kaptCode"]: {"name": r["name"],
                            "addr": r.get("roadAddr") or r.get("addr"),
                            "lat": None, "lng": None}
            for r in missing if r["kaptCode"] not in existing}
    template.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n  → {0} 에 템플릿을 만들었습니다.".format(template.name))
    print("     지도에서 좌표를 찾아 lat/lng 를 채운 뒤 data/coords.json 으로 옮기고")
    print("     python scripts/base.py --geocode-only 를 실행하면 반영됩니다.")


def regeocode(manual: dict) -> None:
    """--geocode-only : 이미 만든 base.json 의 빈 좌표만 다시 채운다."""
    if not config.BASE_JSON.exists():
        print("[중단] data/base.json 이 없습니다. 먼저 전체 실행을 한 번 하세요.")
        sys.exit(1)
    data = json.loads(config.BASE_JSON.read_text(encoding="utf-8"))
    rows = data["complexes"]
    filled = 0
    for r in rows:
        if r["kaptCode"] in manual:
            r["lat"], r["lng"] = manual[r["kaptCode"]]
            filled += 1
            continue
        if r.get("lat"):
            continue
        lat, lng = geocode(r["name"], r.get("addr") or "", r.get("roadAddr") or "")
        if lat:
            r["lat"], r["lng"] = lat, lng
            filled += 1
            print("  + {0} → {1:.5f}, {2:.5f}".format(r["name"], lat, lng))
    data["generatedAt"] = datetime.now(KST).isoformat(timespec="seconds")
    config.BASE_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    print("\n✔ 좌표 {0}개 갱신 — {1}".format(filled, config.BASE_JSON))
    report_missing(rows)
    print("  ③ summarize.py 를 다시 돌리면 data.json 에도 반영됩니다.")


if __name__ == "__main__":
    main()
