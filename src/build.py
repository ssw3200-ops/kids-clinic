"""밤에 아픈 우리 아이 — 지금 문 연 소아과 데이터 만들기

1) 국립중앙의료원 병·의원 찾기 (소아청소년과 D002)  → 소아과·응급실
2) 국립중앙의료원 약국 정보                          → 약국
3) 한국천문연구원 특일 정보                          → 공휴일
결과: docs/data/<시도>.enc.json (비밀번호 암호화), docs/meta.json, docs/status.json

사용: python src/build.py            (DATA_GO_KR_KEY, PAGE_PASSWORD 필요)
      python src/build.py --sample   (키 없이 샘플 데이터로 페이지 확인)
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from lock import encrypt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
KST = timezone(timedelta(hours=9))

HOSP_URL = "https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
PHARM_URL = "https://apis.data.go.kr/B552657/ErmctInsttInfoInqireService/getParmacyListInfoInqire"
HOLI_URL = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
ROWS = 1000

# 주소 첫 단어 → 시·도 짧은 이름 (화면 순서도 이 순서)
REGIONS = {
    "서울": ("서울",), "부산": ("부산",), "대구": ("대구",), "인천": ("인천",), "광주": ("광주",),
    "대전": ("대전",), "울산": ("울산",), "세종": ("세종",), "경기": ("경기",), "강원": ("강원",),
    "충북": ("충청북도", "충북"), "충남": ("충청남도", "충남"), "전북": ("전라북도", "전북", "전북특별자치도"),
    "전남": ("전라남도", "전남"), "경북": ("경상북도", "경북"), "경남": ("경상남도", "경남"), "제주": ("제주",),
}
CODES = {  # 파일 이름용 (한글 파일명 피함)
    "서울": "seoul", "부산": "busan", "대구": "daegu", "인천": "incheon", "광주": "gwangju", "대전": "daejeon",
    "울산": "ulsan", "세종": "sejong", "경기": "gyeonggi", "강원": "gangwon", "충북": "chungbuk", "충남": "chungnam",
    "전북": "jeonbuk", "전남": "jeonnam", "경북": "gyeongbuk", "경남": "gyeongnam", "제주": "jeju",
}

status: dict = {"sources": {}, "errors": []}


def region_of(addr: str) -> str:
    first = (addr or "").strip().split(" ")[0]
    for short, keys in REGIONS.items():
        if any(first.startswith(k) for k in keys):
            return short
    return ""


def city_of(addr: str) -> str:
    """시·군·구 (경기 수원시 장안구 → 수원시)"""
    parts = (addr or "").split()
    if len(parts) < 2:
        return ""
    c = parts[1]
    if region_of(addr) == "세종":
        return "세종시"
    return c if re.search(r"(시|군|구)$", c) else ""


# ---------------------------------------------------------------- API 호출
def call(url: str, params: dict, tries: int = 3) -> ET.Element:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "00"
            if code not in ("00", "0000"):
                msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or ""
                raise RuntimeError(f"resultCode={code} {msg}".strip())
            return root
        except ET.ParseError:
            last = RuntimeError(f"XML 아님: {r.text[:120]!r}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (i + 1))
    raise last  # type: ignore[misc]


def items(root: ET.Element) -> list[dict]:
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]


def fetch_all(name: str, url: str, extra: dict, key: str) -> list[dict]:
    out, page, total = [], 1, None
    while True:
        root = call(url, {"serviceKey": key, "pageNo": page, "numOfRows": ROWS, **extra})
        got = items(root)
        if total is None:
            total = int(root.findtext(".//totalCount") or 0)
        out += got
        if not got or len(out) >= total or page > 200:
            break
        page += 1
    status["sources"][name] = {"total": total, "got": len(out), "pages": page}
    if out:
        status["sources"][name]["fields"] = sorted(out[0].keys())
    return out


def fetch_holidays(key: str) -> list[str]:
    days = set()
    y = datetime.now(KST).year
    for year in (y, y + 1):
        for m in range(1, 13):
            try:
                root = call(HOLI_URL, {"serviceKey": key, "solYear": year, "solMonth": f"{m:02d}", "numOfRows": 50})
                for it in items(root):
                    if it.get("isHoliday", "Y") == "Y" and it.get("locdate"):
                        d = it["locdate"]
                        days.add(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
            except Exception as e:  # noqa: BLE001
                status["errors"].append(f"공휴일 {year}-{m:02d}: {e}")
    status["sources"]["holidays"] = {"got": len(days)}
    return sorted(days)


# ---------------------------------------------------------------- 가공
def hhmm(v: str) -> str:
    v = re.sub(r"\D", "", v or "")
    return v.zfill(4)[-4:] if v else ""


def times_of(it: dict) -> str:
    """월~일+공휴일 8칸, 칸마다 '시작끝' 8자리, 없으면 '--------'"""
    out = []
    for d in range(1, 9):
        s, c = hhmm(it.get(f"dutyTime{d}s", "")), hhmm(it.get(f"dutyTime{d}c", ""))
        out.append(s + c if s and c and s != c else "--------")
    return "".join(out)


def num(v: str) -> float | None:
    try:
        f = float(v)
        return round(f, 5) if f else None
    except (TypeError, ValueError):
        return None


def load_moon() -> dict[str, set[str]]:
    p = ROOT / "moonlight.yaml"
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {r: {re.sub(r"\s", "", n) for n in (names or [])} for r, names in data.items()}


def pack(it: dict, kind: str, moon: dict[str, set[str]]) -> tuple[str, list] | None:
    addr = it.get("dutyAddr", "")
    reg = region_of(addr)
    lat, lon = num(it.get("wgs84Lat")), num(it.get("wgs84Lon"))
    if not reg or lat is None or lon is None:
        return None
    name = it.get("dutyName", "").strip()
    flags = ""
    if kind == "h":
        if it.get("dutyEryn") == "1":
            flags += "E"
        if "소아" in name or "아동" in name or "어린이" in name:
            flags += "P"
        if re.sub(r"\s", "", name) in moon.get(reg, set()):
            flags += "M"
    div = it.get("dutyDivNam", "") if kind == "h" else ""
    rec = [name, addr, it.get("dutyTel1", ""), lat, lon, times_of(it), flags, div, city_of(addr)]
    return reg, rec


def build(hosp: list[dict], pharm: list[dict], holidays: list[str], password: str, sample: bool) -> None:
    moon = load_moon()
    data = {r: {"h": [], "p": []} for r in REGIONS}
    seen = set()
    skipped = 0
    for kind, rows in (("h", hosp), ("p", pharm)):
        for it in rows:
            hid = it.get("hpid") or (it.get("dutyName", "") + it.get("dutyAddr", ""))
            if (kind, hid) in seen:
                continue
            seen.add((kind, hid))
            res = pack(it, kind, moon)
            if not res:
                skipped += 1
                continue
            reg, rec = res
            data[reg][kind].append(rec)
    status["skipped_no_region_or_xy"] = skipped

    out_dir = DOCS / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_regions = {}
    for reg, d in data.items():
        allp = d["h"] + d["p"]
        if allp:
            lat = round(sum(r[3] for r in allp) / len(allp), 4)
            lon = round(sum(r[4] for r in allp) / len(allp), 4)
        else:
            lat = lon = None
        d["h"].sort(key=lambda r: r[0])
        d["p"].sort(key=lambda r: r[0])
        enc = encrypt({"region": reg, "h": d["h"], "p": d["p"]}, password)
        (out_dir / f"{CODES[reg]}.enc.json").write_text(json.dumps(enc), encoding="utf-8")
        meta_regions[reg] = {
            "code": CODES[reg], "lat": lat, "lon": lon,
            "h": len(d["h"]), "p": len(d["p"]), "er": sum("E" in r[6] for r in d["h"]),
        }

    now = datetime.now(KST)
    meta = {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "sample": sample,
        "holidays": holidays,
        "regions": meta_regions,
    }
    (DOCS / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    status["updated"] = meta["updated"]
    status["regions"] = {r: {k: v for k, v in m.items() if k in ("h", "p", "er")} for r, m in meta_regions.items()}
    (DOCS / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(status["regions"], ensure_ascii=False))


# ---------------------------------------------------------------- 샘플
def sample_rows() -> tuple[list[dict], list[dict], list[str]]:
    rnd = random.Random(7)
    places = [("세종특별자치시 한누리대로 2130", 36.4800, 127.2890), ("세종특별자치시 고운서길 10", 36.5130, 127.2350),
              ("대전광역시 서구 둔산로 100", 36.3510, 127.3850), ("대전광역시 유성구 대학로 99", 36.3620, 127.3450),
              ("서울특별시 강남구 테헤란로 152", 37.5000, 127.0360), ("경기도 수원시 팔달구 효원로 1", 37.2630, 127.0280)]
    patterns = [("0900", "1800", "0900", "1300", "", ""), ("0900", "2100", "0900", "1800", "1000", "1800"),
                ("0830", "2300", "0900", "2300", "0900", "2300"), ("0000", "2400", "0000", "2400", "0000", "2400")]
    hosp, pharm = [], []
    for i in range(60):
        addr, la, lo = places[i % len(places)]
        wk_s, wk_c, sa_s, sa_c, su_s, su_c = patterns[rnd.randrange(3)]
        it = {"hpid": f"H{i}", "dutyName": rnd.choice(["튼튼", "새싹", "아이사랑", "하늘", "푸른"]) + f"소아청소년과의원{i}",
              "dutyAddr": addr, "dutyTel1": f"044-000-{i:04d}", "wgs84Lat": str(la + rnd.uniform(-.03, .03)),
              "wgs84Lon": str(lo + rnd.uniform(-.03, .03)), "dutyDivNam": "의원"}
        for d in range(1, 6):
            it[f"dutyTime{d}s"], it[f"dutyTime{d}c"] = wk_s, wk_c
        it["dutyTime6s"], it["dutyTime6c"] = sa_s, sa_c
        it["dutyTime7s"], it["dutyTime7c"] = su_s, su_c
        it["dutyTime8s"], it["dutyTime8c"] = su_s, su_c
        if i % 15 == 0:
            it["dutyName"] = f"샘플대학교병원{i}"
            it["dutyDivNam"] = "종합병원"
            it["dutyEryn"] = "1"
            for d in range(1, 9):
                it[f"dutyTime{d}s"], it[f"dutyTime{d}c"] = "0000", "2400"
        hosp.append(it)
    for i in range(50):
        addr, la, lo = places[i % len(places)]
        wk_s, wk_c, sa_s, sa_c, su_s, su_c = patterns[rnd.randrange(4)]
        it = {"hpid": f"P{i}", "dutyName": f"온누리샘플약국{i}", "dutyAddr": addr, "dutyTel1": f"044-111-{i:04d}",
              "wgs84Lat": str(la + rnd.uniform(-.03, .03)), "wgs84Lon": str(lo + rnd.uniform(-.03, .03))}
        for d in range(1, 6):
            it[f"dutyTime{d}s"], it[f"dutyTime{d}c"] = wk_s, wk_c
        it["dutyTime6s"], it["dutyTime6c"] = sa_s, sa_c
        it["dutyTime7s"], it["dutyTime7c"] = su_s, su_c
        pharm.append(it)
    return hosp, pharm, ["2026-12-25", "2027-01-01"]


def main() -> None:
    sample = "--sample" in sys.argv
    password = os.environ.get("PAGE_PASSWORD", "").strip()
    if not password:
        if not sample:
            sys.exit("PAGE_PASSWORD 가 없어요 (GitHub Secrets 확인)")
        password = "test"
    if sample:
        hosp, pharm, holidays = sample_rows()
    else:
        key = os.environ.get("DATA_GO_KR_KEY", "").strip()
        if not key:
            sys.exit("DATA_GO_KR_KEY 가 없어요 (GitHub Secrets 확인)")
        hosp = pharm = []
        try:
            hosp = fetch_all("hospital", HOSP_URL, {"QD": "D002"}, key)
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"병·의원: {e}")
        try:
            pharm = fetch_all("pharmacy", PHARM_URL, {}, key)
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"약국: {e}")
        holidays = fetch_holidays(key)
        if not holidays and (DOCS / "meta.json").exists():  # 공휴일 API 실패 시 이전 값 유지
            holidays = json.loads((DOCS / "meta.json").read_text()).get("holidays", [])
        first_run = not (DOCS / "meta.json").exists() or json.loads((DOCS / "meta.json").read_text()).get("sample")
        if (not hosp and not pharm) or ((not hosp or not pharm) and not first_run):
            DOCS.mkdir(exist_ok=True)
            (DOCS / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(status, ensure_ascii=False, indent=1))
            print("수집 실패 — 활용신청 승인 여부를 확인하세요 (기존 데이터는 그대로 둠)")
            return
    build(hosp, pharm, holidays, password, sample)
    if status["errors"]:
        print("\n".join(status["errors"]))


if __name__ == "__main__":
    main()
