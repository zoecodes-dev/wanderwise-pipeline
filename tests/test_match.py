"""스모크 테스트 — 임베딩 + match_places가 실제로 후보를 뽑는지 확인.

무드 "비 오는 날 혼자 사색하기 좋은" + 성수동 좌표로
후보가 몇 개 나오는지, 유사도 순으로 맞게 나오는지 출력한다.
(Claude 동선 구성/ FastAPI 라우터는 아직 만들지 않음 — 여기까지만.)

실행: PYTHONPATH=. python tests/test_match.py   (레포 루트에서)
"""
from pipeline.config import load_config
from api.places import find_candidates

MOOD = "비 오는 날 혼자 사색하기 좋은"
LAT, LNG = 37.5446, 127.0559  # 성수동 중심 (config/seoul.yaml)


def main():
    cfg = load_config("config/seoul.yaml")
    print(f"무드 : {MOOD!r}")
    print(f"좌표 : ({LAT}, {LNG}) 성수동")
    print(f"임베딩: {cfg['embedding']['provider']} / {cfg['embedding']['model']}")

    cands = find_candidates(cfg, MOOD, LAT, LNG)
    print(f"\n후보 {len(cands)}개\n")

    prev = None
    for i, c in enumerate(cands, 1):
        sim = c["similarity"]
        order_ok = "" if prev is None or sim <= prev + 1e-9 else "  <-- 유사도 역전!"
        prev = sim
        print(f"{i:2}. sim={sim:.3f}  dist={c['distance_m']:6.0f}m  "
              f"[{c['neighborhood']}/{c['category']}] {c['name']}{order_ok}")

    if not cands:
        print("후보 0개. match_places는 status='approved'만 반환한다 — "
              "적재된 99곳이 아직 pending이면 0이 정상.")


if __name__ == "__main__":
    main()
