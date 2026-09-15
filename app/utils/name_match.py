"""
실거래가 API의 아파트명(aptNm)과 공동주택 단지 목록의 등록명(kaptName)을
매칭하기 위한 헬퍼. 두 데이터의 이름 표기가 꽤 다르다 (지번 병기,
동 번호 병기, 띄어쓰기, 미세한 표기 차이 등) — 예:

  "타워팰리스2"           vs "타워팰리스2차"
  "대치동우정에쉐르2(890-42)" vs (매칭 없음)
  "현대비젼21"            vs "현대비전21"

정확히 일치하지 않으면 정규화 후, 그래도 안 되면 같은 (시군구,법정동) 안에서만
유사 매칭을 시도한다. 후보가 여러 개로 애매하면 틀리게 붙이는 것보다
차라리 매칭하지 않는 쪽을 택한다 (세대수 정보없음으로 표시).
"""
from __future__ import annotations

import difflib
import re

_PAREN_RE = re.compile(r"\([^)]*\)")
_DONG_RANGE_RE = re.compile(r"\d+동(?:\s*[~,]\s*\d*동?)*")
_NON_ALNUM_KOR_RE = re.compile(r"[^0-9A-Za-z가-힣]")
_TRAILING_NUM_RE = re.compile(r"(\d+)$")

_SYNONYMS = {
    "비젼": "비전",
}


def normalize_name(name: str) -> str:
    s = str(name)
    s = _PAREN_RE.sub("", s)
    s = _DONG_RANGE_RE.sub("", s)
    s = _NON_ALNUM_KOR_RE.sub("", s)
    for old, new in _SYNONYMS.items():
        s = s.replace(old, new)
    return s.strip()


def _trailing_num(s: str) -> str | None:
    m = _TRAILING_NUM_RE.search(s)
    return m.group(1) if m else None


def _conflicts(a: str, b: str) -> bool:
    """끝자리 숫자가 둘 다 있는데 서로 다르면(예: ...에쉐르1 vs ...에쉐르2) 다른 단지로 간주."""
    na, nb = _trailing_num(a), _trailing_num(b)
    return na is not None and nb is not None and na != nb


def best_match(query_norm: str, candidates_norm: list[str], fuzzy_cutoff: float = 0.85) -> tuple[str | None, float, str]:
    """candidates_norm 중 query_norm과 가장 잘 맞는 것을 고른다.
    반환: (매칭된 정규화 이름 또는 None, 유사도 점수, 매칭 종류)
    후보가 여러 개로 애매하면 매칭하지 않는다(None)."""
    if query_norm in candidates_norm:
        return query_norm, 1.0, "exact"

    substr = [c for c in set(candidates_norm) if (query_norm in c or c in query_norm) and not _conflicts(query_norm, c)]
    if len(substr) == 1:
        score = difflib.SequenceMatcher(None, query_norm, substr[0]).ratio()
        return substr[0], score, "substring"
    if len(substr) > 1:
        return None, 0.0, "ambiguous"

    fuzzy = difflib.get_close_matches(query_norm, set(candidates_norm), n=1, cutoff=fuzzy_cutoff)
    if fuzzy and not _conflicts(query_norm, fuzzy[0]):
        score = difflib.SequenceMatcher(None, query_norm, fuzzy[0]).ratio()
        return fuzzy[0], score, "fuzzy"

    return None, 0.0, "none"
