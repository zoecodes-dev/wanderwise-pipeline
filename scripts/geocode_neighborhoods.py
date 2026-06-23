#!/usr/bin/env python3
"""새 상권 후보를 카카오로 자동 지오코딩 → YAML 블록 출력 + 수율 드라이런.

좌표를 손으로 찍으면 틀리므로, 각 상권의 대표 별칭을 카카오 키워드 검색해서
상위 결과들의 좌표 중앙값을 center로 잡는다. (서울로 한정)

사용:
  PYTHONPATH=. python scripts/geocode_neighborhoods.py            # 지오코딩 + YAML 출력
  PYTHONPATH=. python scripts/geocode_neighborhoods.py --yield    # + 동네당 수율 드라이런
"""
import sys
import time
import statistics as st
import requests

from pipeline.config import env

KAKAO = "https://dapi.kakao.com/v2/local/search/keyword.json"

# (name, query_alias[], moods[]) — 좌표는 자동. 기존 16개와 겹치지 않는 새 상권.
NEW = [
    ("한남동", ["한남동", "한남오거리", "꼼데가르송길"], ["romantic", "artistic", "foodie"]),
    ("이태원", ["이태원", "경리단길", "우사단로"], ["energetic", "adventurous", "artistic"]),
    ("북촌", ["북촌한옥마을", "가회동", "삼청동"], ["nostalgic", "contemplative", "romantic"]),
    ("서촌", ["서촌", "통인시장", "체부동"], ["nostalgic", "artistic", "contemplative"]),
    ("익선동", ["익선동", "익선동 한옥거리"], ["romantic", "nostalgic", "hidden"]),
    ("을지로", ["을지로3가", "힙지로", "을지로 노가리골목"], ["nostalgic", "adventurous", "artistic"]),
    ("합정동", ["합정", "합정역 골목"], ["foodie", "energetic", "hidden"]),
    ("상수동", ["상수동", "당인리", "상수역 골목"], ["artistic", "hidden", "romantic"]),
    ("서교동", ["서교동", "홍대 골목"], ["energetic", "artistic", "foodie"]),
    ("공덕동", ["공덕시장", "마포 먹자골목", "공덕"], ["foodie", "nostalgic", "energetic"]),
    ("청파동", ["청파동", "숙대입구"], ["hidden", "nostalgic", "contemplative"]),
    ("보광동", ["보광동", "우사단로 10길"], ["hidden", "adventurous", "foodie"]),
    ("옥수동", ["옥수동", "옥수역"], ["contemplative", "nature", "romantic"]),
    ("왕십리", ["왕십리 곱창골목", "왕십리"], ["foodie", "energetic", "nostalgic"]),
    ("답십리", ["답십리 고미술상가", "답십리"], ["nostalgic", "hidden", "artistic"]),
    ("행당동", ["행당동", "행당시장"], ["hidden", "nostalgic", "foodie"]),
    ("대학로", ["대학로", "혜화 마로니에공원", "이화동 벽화마을"], ["artistic", "energetic", "romantic"]),
    ("정릉", ["정릉시장", "정릉", "북한산 정릉"], ["nature", "contemplative", "hidden"]),
    ("평창동", ["평창동 미술관거리", "평창동"], ["artistic", "contemplative", "nature"]),
    ("구기동", ["구기동", "이북5도청", "구기터널"], ["nature", "contemplative", "hidden"]),
    ("응암동", ["응암동 대림시장", "응암동"], ["hidden", "foodie", "nostalgic"]),
    ("은평한옥마을", ["은평한옥마을", "진관동"], ["contemplative", "nature", "romantic"]),
    ("수유", ["수유시장", "빨래골", "수유"], ["nostalgic", "hidden", "nature"]),
    ("쌍문동", ["쌍문동", "둘리뮤지엄"], ["nostalgic", "hidden", "energetic"]),
    ("방학동", ["방학동 도깨비시장", "방학동"], ["nostalgic", "hidden", "nature"]),
    ("회기동", ["회기 경희대 골목", "회기시장", "회기"], ["foodie", "energetic", "nostalgic"]),
    ("청량리", ["청량리 청과물시장", "경동시장", "청량리"], ["energetic", "nostalgic", "foodie"]),
    ("제기동", ["제기동 약령시", "제기동"], ["nostalgic", "energetic", "hidden"]),
    ("신림", ["신림동 순대타운", "신림"], ["foodie", "energetic", "hidden"]),
    ("사당", ["남현동 예술인마을", "사당"], ["hidden", "artistic", "contemplative"]),
    ("방배", ["방배 사이길", "방배 카페거리"], ["romantic", "foodie", "hidden"]),
    ("서래마을", ["서래마을", "몽마르뜨공원"], ["romantic", "contemplative", "foodie"]),
    ("양재천", ["양재천 카페거리", "도곡 양재천"], ["nature", "romantic", "contemplative"]),
    ("가로수길", ["가로수길", "세로수길", "신사동 골목"], ["romantic", "energetic", "artistic"]),
    ("잠실새내", ["잠실새내", "잠실 본동"], ["foodie", "energetic", "nostalgic"]),
    ("건대", ["건대입구 커먼그라운드", "건대 양꼬치거리"], ["energetic", "foodie", "adventurous"]),
    ("성신여대", ["성신여대 돈암시장", "성신여대"], ["energetic", "foodie", "hidden"]),
    ("충무로", ["충무로 인쇄골목", "필동", "충무로"], ["nostalgic", "artistic", "hidden"]),
    ("동대문", ["신당창작아케이드", "동대문 신평화", "동대문 골목"], ["adventurous", "energetic", "hidden"]),
    ("영등포", ["영등포 시장", "영등포 뒷골목"], ["energetic", "nostalgic", "hidden"]),
    ("노량진", ["노량진 수산시장", "노량진 컵밥거리"], ["energetic", "foodie", "adventurous"]),
    ("신촌", ["신촌 골목", "이화여대 앞", "신촌"], ["energetic", "foodie", "nostalgic"]),
    ("아현", ["아현동", "북아현", "굴레방다리"], ["hidden", "nostalgic", "foodie"]),
    ("효창", ["효창공원앞", "효창동"], ["hidden", "contemplative", "nostalgic"]),
    ("장충동", ["장충동 족발골목", "장충단공원"], ["foodie", "nostalgic", "nature"]),
]


def geocode(alias: str, key: str):
    r = requests.get(KAKAO, headers={"Authorization": f"KakaoAK {key}"},
                     params={"query": f"{alias} 서울", "size": 10, "sort": "accuracy"}, timeout=10)
    r.raise_for_status()
    docs = r.json().get("documents", [])
    pts = [(float(d["y"]), float(d["x"])) for d in docs
           if d.get("address_name", "").startswith("서울")]
    if not pts:
        pts = [(float(d["y"]), float(d["x"])) for d in docs]
    if not pts:
        return None
    return st.median([p[0] for p in pts]), st.median([p[1] for p in pts])


def main():
    key = env("KAKAO_API_KEY")
    do_yield = "--yield" in sys.argv
    cats = ["카페", "독립서점", "갤러리", "전망", "시장", "공원", "바", "공방", "베이커리", "노포"]
    blocks = []
    for name, aliases, moods in NEW:
        c = geocode(aliases[0], key)
        time.sleep(0.15)
        if not c:
            print(f"# !! {name}: 지오코딩 실패", file=sys.stderr)
            continue
        lat, lng = c
        blocks.append((name, aliases, moods, lat, lng))
        msg = f"  - name: {name}\n    query_alias: [{', '.join(aliases)}]\n    moods: [{', '.join(moods)}]\n    center: {{lat: {lat:.4f}, lng: {lng:.4f}}}"
        print(msg)

    if do_yield:
        print("\n# === 수율 드라이런 (동네 × 카테고리 raw 카운트, 필터 전) ===", file=sys.stderr)
        for name, aliases, moods, lat, lng in blocks:
            ids = set()
            for alias in aliases:
                for cat in cats:
                    r = requests.get(KAKAO, headers={"Authorization": f"KakaoAK {key}"},
                                     params={"query": f"{alias} {cat}", "x": lng, "y": lat,
                                             "radius": 700, "size": 15, "sort": "accuracy"}, timeout=10)
                    if r.ok:
                        for d in r.json().get("documents", []):
                            ids.add(d["id"])
                    time.sleep(0.05)
            print(f"# {name:8s} raw {len(ids):4d}곳", file=sys.stderr)


if __name__ == "__main__":
    main()
