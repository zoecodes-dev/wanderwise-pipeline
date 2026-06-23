"""스모크 테스트 — Claude 동선 구성 + join이 실제로 작동하는지 확인.

무드 "비 오는 날 혼자 사색하기 좋은" + 성수동 좌표로 하루 동선을 생성해 출력한다.
도착 전 노출 정보(hint/방향/시간)와 reveal 블록(이름 공개)을 나눠서 보여준다.
(FastAPI 라우터는 아직 만들지 않음 — 동선 생성+join까지만.)

실행: PYTHONPATH=. python tests/test_itinerary.py   (레포 루트에서)
"""
from pipeline.config import load_config
from api.itinerary import build_itinerary

MOOD = "비 오는 날 혼자 사색하기 좋은"
LAT, LNG = 37.5446, 127.0559  # 성수동 중심


def main():
    cfg = load_config("config/seoul.yaml")
    print(f"무드 : {MOOD!r}")
    print(f"좌표 : ({LAT}, {LNG}) 성수동\n")

    r = build_itinerary(cfg, MOOD, LAT, LNG, stops=4, start_time="10:00")

    if r.get("error") == "no_candidates":
        print("후보 0개 — 근처에 approved 장소가 없습니다.")
        return

    tag = " [폴백]" if r["fallback"] else ""
    print(f"후보 {r['candidate_count']}개 → 동선 {len(r['stops'])}곳{tag}")
    print(f"summary: {r['summary']}\n")

    for s in r["stops"]:
        print(f"[{s['order']}] {s['arrive_time']}~{s['depart_time']} "
              f"({s['stay_minutes']}분 체류)  교통: {s['transport']['from_prev']}")
        print(f"    동네/카테고리: {s['neighborhood']} / {s['category']}")
        print(f"    방향: {s['direction']}")
        print(f"    hint(이름 숨김): {s['hint']}")
        rv = s["reveal"]
        print(f"    └ reveal ▶ {rv['display_name']}  ({rv['lat']}, {rv['lng']})")
        print(f"             {rv['reveal_text']}\n")

    # 이름 숨김 검증: hint/direction에 display_name이 새어나오면 경고
    leaks = []
    for s in r["stops"]:
        dn = s["reveal"]["display_name"] or ""
        blob = f"{s['hint']} {s['direction']}"
        if len(dn) >= 2 and dn in blob:
            leaks.append((s["order"], dn))
    if leaks:
        print("⚠ 이름 누출 의심:", leaks)
    else:
        print("✓ hint/direction에 display_name 노출 없음")


if __name__ == "__main__":
    main()
