# -*- coding: utf-8 -*-
"""
공공데이터포털(K-apt / 실거래가) 호출 유틸.

config.ENDPOINTS 에는 '서비스 주소'까지만 적는다.

    "apt_list": "https://apis.data.go.kr/1613000/AptListService4"

오퍼레이션 이름은 서비스 주소 끝의 버전 표기를 그대로 따라가는 규칙이라
(AptListService4 → getSigunguAptList4, AptBasisInfoServiceV5 → getAphusBassInfoV5),
여기서 후보를 만들어 처음 성공하는 것을 골라 캐시한다.
버전이 또 올라가도 config.ENDPOINTS 한 줄만 바꾸면 된다.
"""
from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Iterable

import requests

import config

TIMEOUT = 20
RETRY = 3
SLEEP = 0.12  # 공공데이터포털 초당 호출 제한 완화용

_resolved: dict[str, str] = {}   # 오퍼레이션 키 -> 실제로 동작한 URL


class ApiUnavailable(Exception):
    """후보 엔드포인트가 모두 실패했을 때."""


# ---------------------------------------------------------------- 응답 캐시
# 단지 하나당 관리비 17항목 + 기본/상세 2회를 부르기 때문에, 캐시가 없으면
# 재실행할 때마다 수백 번을 다시 호출하게 된다. 공공데이터는 월 단위로만 바뀌므로
# (op, 파라미터) 조합을 그대로 캐시해도 안전하다.
_cache: dict[str, list] = {}
_cache_lock = threading.Lock()
_cache_dirty = False
_cache_loaded = False
_use_cache = True


def load_cache() -> None:
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if config.CACHE_JSON.exists():
        try:
            _cache = json.loads(config.CACHE_JSON.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}


def save_cache() -> None:
    if not _cache_dirty:
        return
    with _cache_lock:
        try:
            config.CACHE_JSON.write_text(
                json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            print("[안내] 캐시 저장 실패(무시하고 진행): {0}".format(exc))


def disable_cache() -> None:
    """--refresh 로 실행할 때 캐시를 무시하고 전부 새로 받는다."""
    global _use_cache
    _use_cache = False


def cache_get(key: str):
    """API 응답 외의 값(지오코딩 결과 등)도 같은 캐시에 얹기 위한 공개 헬퍼."""
    load_cache()
    return _cache.get(key)


def cache_put(key: str, value) -> None:
    global _cache_dirty
    with _cache_lock:
        _cache[key] = value
        _cache_dirty = True


def _cache_key(op_key: str, params: dict) -> str:
    parts = sorted((k, str(v)) for k, v in params.items() if k != "serviceKey")
    return op_key + "|" + "&".join(k + "=" + v for k, v in parts)


# ---------------------------------------------------------------- 응답 파싱
def _items_from_json(payload: dict) -> tuple[list[dict], dict]:
    body = (payload.get("response") or {}).get("body") or {}
    items = body.get("items")
    if items in (None, "", []):
        # 단건 조회 오퍼레이션(예: kaptCode 로 찾는 기본정보·관리비 항목별 API)은
        # items 래퍼 없이 body.item 에 객체 하나를 바로 준다. 여기를 안 보면
        # 정상 응답인데도 빈 리스트로 오인해 해당 항목이 통째로 null 이 된다.
        single = body.get("item")
        if isinstance(single, dict):
            return [single], body
        return [], body
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return list(items), body


def _items_from_xml(text: str) -> tuple[list[dict], dict]:
    root = ET.fromstring(text)
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    if code and code.strip() not in ("00", "0"):
        msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or ""
        raise ApiUnavailable("resultCode={0} {1}".format(code, msg))
    items = []
    for node in root.iter("item"):
        items.append({child.tag: (child.text or "").strip() for child in node})
    if not items:
        # items 래퍼 없이 body 바로 아래 단건으로 오는 오퍼레이션 대응
        body = root.find(".//body")
        if body is not None:
            leaf = {c.tag: (c.text or "").strip() for c in body if len(c) == 0}
            for k in ("numOfRows", "pageNo", "totalCount"):
                leaf.pop(k, None)
            if leaf:
                items = [leaf]
    meta = {
        "totalCount": root.findtext(".//totalCount"),
        "numOfRows": root.findtext(".//numOfRows"),
        "pageNo": root.findtext(".//pageNo"),
    }
    return items, meta


def _request(url: str, params: dict) -> tuple[list[dict], dict]:
    last = None
    for attempt in range(RETRY):
        try:
            res = requests.get(url, params=params, timeout=TIMEOUT)
            time.sleep(SLEEP)
            text = res.text.strip()
            if res.status_code == 404:
                raise ApiUnavailable("HTTP 404 (오퍼레이션 이름 불일치)")
            if res.status_code != 200:
                raise ApiUnavailable("HTTP {0} {1}".format(res.status_code, text[:120]))
            if text.startswith("{"):
                return _items_from_json(res.json())
            if text.startswith("<"):
                if "OpenAPI_ServiceResponse" in text or "SERVICE_KEY" in text.upper():
                    raise ApiUnavailable("인증/서비스 오류: {0}".format(text[:200]))
                return _items_from_xml(text)
            raise ApiUnavailable("알 수 없는 응답: {0}".format(text[:120]))
        except ApiUnavailable:
            raise
        except Exception as exc:              # 네트워크/파싱 일시 오류만 재시도
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise ApiUnavailable(str(last))


# ---------------------------------------------------------------- 후보 URL 생성
def candidate_urls(base: str, stem: str) -> list[str]:
    """
    'https://…/AptListService4' + 'getSigunguAptList'
      → ['…/getSigunguAptList4', '…/getSigunguAptList', '…/getSigunguAptListV4']
    """
    base = (base or "").rstrip("/")
    tail = base.rsplit("/", 1)[-1]
    m = re.search(r"(V?\d+)$", tail)
    names = []
    if m:
        suffix = m.group(1)
        digits = suffix.lstrip("Vv")
        names += [stem + suffix, stem + digits, stem + "V" + digits]
    names.append(stem)
    return [base + "/" + n for n in dict.fromkeys(names)]


# 예전 버전 주소 — 위 후보가 모두 실패했을 때만 시도한다.
LEGACY: dict[str, list[str]] = {
    "apt_list_sigungu": [
        "https://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3",
        "http://apis.data.go.kr/1611000/AptListService/getSigunguAptList",
    ],
    "apt_list_bjd": [
        "https://apis.data.go.kr/1613000/AptListService3/getLegaldongAptList3",
        "http://apis.data.go.kr/1611000/AptListService/getLegaldongAptList",
    ],
    "apt_basis": [
        "https://apis.data.go.kr/1613000/AptBasisInfoServiceV3/getAphusBassInfoV3",
        "http://apis.data.go.kr/1611000/AptBasisInfoService/getAphusBassInfo",
    ],
    "apt_detail": [
        "https://apis.data.go.kr/1613000/AptBasisInfoServiceV3/getAphusDtlInfoV3",
        "http://apis.data.go.kr/1611000/AptBasisInfoService/getAphusDtlInfo",
    ],
    "apt_trade": [
        "http://openapi.molit.go.kr/OpenAPI_ToolInstallPackage/service/rest/RTMSOBJSvc"
        "/getRTMSDataSvcAptTradeDev",
    ],
}

# 오퍼레이션 키 -> (config.ENDPOINTS 키, 오퍼레이션 이름의 뼈대)
OPERATIONS = {
    "apt_list_sigungu": ("apt_list", "getSigunguAptList"),
    "apt_list_bjd": ("apt_list", "getLegaldongAptList"),
    "apt_basis": ("apt_basis", "getAphusBassInfo"),
    "apt_detail": ("apt_basis", "getAphusDtlInfo"),
    "apt_trade": ("apt_trade", "getRTMSDataSvcAptTradeDev"),
}
# 공용관리비 17개 항목 오퍼레이션을 cost_<key> 로 등록한다 (config.COST_CATEGORIES 참고).
for _key, _stem, _label in config.COST_CATEGORIES:
    OPERATIONS["cost_" + _key] = ("apt_cost", _stem)


def urls_for(op_key: str) -> list[str]:
    service_key, stem = OPERATIONS[op_key]
    base = config.ENDPOINTS.get(service_key, "")
    return candidate_urls(base, stem) + LEGACY.get(op_key, [])


# ---------------------------------------------------------------- 공개 호출부
def call(op_key: str, params: dict, quiet: bool = False,
         extra_candidates: Iterable[str] = ()) -> list[dict]:
    """
    op_key 에 대응하는 후보 URL 중 처음 성공한 것으로 호출하고 item 리스트를 돌려준다.
    한 번 성공한 URL 은 프로세스 내에서 재사용한다.
    """
    global _cache_dirty

    base_params = {
        "serviceKey": config.KAPT_SERVICE_KEY,
        "_type": "json",
        "numOfRows": 999,
        "pageNo": 1,
    }
    base_params.update(params)

    ckey = _cache_key(op_key, base_params)
    if _use_cache:
        load_cache()
        hit = _cache.get(ckey)
        if hit is not None:
            return hit

    urls = [_resolved[op_key]] if op_key in _resolved else \
        list(extra_candidates) + urls_for(op_key)

    errors = []
    for url in urls:
        try:
            items, _meta = _request(url, base_params)
            if op_key not in _resolved:
                print("    · {0} → {1}".format(op_key, url))
            _resolved[op_key] = url
            with _cache_lock:
                _cache[ckey] = items
                _cache_dirty = True
            return items
        except Exception as exc:
            errors.append("  - {0}\n      {1}".format(url, exc))
    if not quiet:
        print("[경고] '{0}' 호출 실패. 시도한 주소:\n{1}".format(op_key, "\n".join(errors)))
    raise ApiUnavailable(op_key)


def resolved_endpoints() -> dict[str, str]:
    return dict(_resolved)
