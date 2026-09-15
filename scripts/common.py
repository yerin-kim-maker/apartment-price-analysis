"""여러 수집 스크립트가 공유하는 경로/설정 헬퍼."""
from __future__ import annotations

import math
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

load_dotenv(PROJECT_ROOT / ".env")


def get_api_key(name: str) -> str:
    key = os.environ.get(name, "").strip()
    if not key:
        raise RuntimeError(
            f"{name} 값이 비어 있습니다. 프로젝트 폴더의 .env 파일에 {name}=발급받은키 형태로 넣어주세요."
            f" (.env.example 참고)"
        )
    return key


def load_lawd_codes(region_filter: str | None = None) -> pd.DataFrame:
    """config/lawd_codes.csv 로드. region_filter가 있으면 sido/sigungu에 부분일치하는 행만 반환."""
    df = pd.read_csv(CONFIG_DIR / "lawd_codes.csv", dtype={"lawd_cd": str})
    if region_filter:
        mask = df["sido"].str.contains(region_filter) | df["sigungu"].str.contains(region_filter)
        df = df[mask]
    return df.reset_index(drop=True)


def month_range(start_ym: str, end_ym: str) -> list[str]:
    """"YYYYMM" 문자열 두 개 사이의 모든 연월을 "YYYYMM" 리스트로 반환 (양끝 포함)."""
    start = pd.Period(start_ym, freq="M")
    end = pd.Period(end_ym, freq="M")
    if start > end:
        raise ValueError(f"start_ym({start_ym})이 end_ym({end_ym})보다 이후입니다.")
    return [str(p).replace("-", "") for p in pd.period_range(start, end, freq="M")]


def default_start_ym(years_back: int = 5) -> str:
    today = date.today()
    year = today.year - years_back
    return f"{year}{today.month:02d}"


def default_end_ym() -> str:
    today = date.today()
    return f"{today.year}{today.month:02d}"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 사이의 직선거리(미터)."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def get_with_retry(url: str, params: dict, timeout: int = 30, max_retries: int = 8) -> requests.Response:
    """일시적 네트워크 오류(타임아웃/연결끊김/429 요청과다)에 대비해 재시도하는 GET 요청.
    429(Too Many Requests)는 일반 오류보다 더 오래 기다려야 풀리는 경우가 많아 별도로 처리한다."""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = min(20 * attempt, 180)
                print(f"  [429 재시도 {attempt}/{max_retries}] 요청이 많아 {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            wait = min(2 ** attempt, 60)
            print(f"  [재시도 {attempt}/{max_retries}] {exc.__class__.__name__} - {wait}초 후 재시도")
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise requests.exceptions.RequestException(f"429 재시도 {max_retries}회 초과: {url}")
