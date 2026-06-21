"""Stage 3 — AI 생성 (Claude API).

장소마다: 시적 힌트 / 경험 묘사 / 도착 시 reveal 문장 / 무드 태그(enum 강제).
batch_size개씩 묶어 한 호출로 처리. JSON 파싱 실패 시 1회 재시도,
그래도 실패하면 'failed'로 분리해서 파이프라인은 계속 진행.

API 문서: https://docs.claude.com/en/api/overview
"""
import json

import anthropic

from .config import env, stage_file

SYSTEM = """당신은 여행 앱 WanderWise의 작가입니다. WanderWise는 목적지의 이름을 도착 전까지 숨기고, 방향과 시적인 힌트만으로 여행자를 안내하는 앱입니다.

장소 목록이 주어지면 각 장소에 대해 다음을 작성하세요:

- hint: 장소 이름을 절대 노출하지 않는 시적 힌트. 감각(냄새, 소리, 빛)과 골목의 결을 담을 것. 상호명, 간판 문구, 검색하면 바로 특정되는 고유 표현 금지. **길이를 장소마다 다르게 변주하세요 — 어떤 곳은 한 문장으로 툭, 어떤 곳은 세 문장으로 길게.** 모든 힌트가 비슷한 길이가 되지 않도록.
- display_name: 화면 표시용 짧은 이름. 정식 상호에서 군더더기를 덜어낸, 사람들이 실제로 부르는 이름. 예: "성수동대림창고갤러리" → "대림창고", "카페1953위드오드리" → "카페 1953", "메일룸" → "메일룸"(이미 짧으면 그대로).
- description: 그곳에서의 경험을 그리는 2~3문장. 무엇을 하게 되는지, 어떤 기분이 드는지.
- reveal_text: 도착 순간 화면에 뜨는 1문장. **display_name(짧은 이름)을** 자연스럽게 품으면서 작은 감탄이 있는 문장. 정식 상호 전체가 아니라 짧은 이름을 쓸 것. 예: "낡은 철문 너머, 당신이 찾던 곳은 ○○였습니다."
- moods: 허용된 무드 목록에서만 골라 1~3개.

반드시 JSON 배열만 반환하세요. 마크다운 코드펜스, 설명, 인사 금지.
각 원소: {"external_id": "...", "hint": "...", "display_name": "...", "description": "...", "reveal_text": "...", "moods": ["..."]}"""


def _build_prompt(batch: list[dict], moods: list[str]) -> str:
    items = [
        {
            "external_id": p["external_id"],
            "name": p["name"],
            "category": p["category"],
            "category_raw": p.get("category_raw", ""),
            "neighborhood": p["neighborhood"],
            "neighborhood_moods": p.get("neighborhood_moods", []),
            "address": p.get("address", ""),
        }
        for p in batch
    ]
    return (
        f"허용 무드: {json.dumps(moods, ensure_ascii=False)}\n\n"
        f"장소 목록:\n{json.dumps(items, ensure_ascii=False, indent=1)}"
    )


def _parse(text: str, batch: list[dict], moods: set[str]) -> dict[str, dict]:
    """검증: JSON 배열 + 필수 키 + 무드 enum + 힌트에 상호명 미노출."""
    clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    arr = json.loads(clean)
    by_id = {p["external_id"]: p for p in batch}
    ok = {}
    for item in arr:
        eid = item.get("external_id")
        if eid not in by_id:
            continue
        if not all(item.get(k) for k in ("hint", "description", "reveal_text", "moods")):
            continue
        item["moods"] = [m for m in item["moods"] if m in moods][:3]
        if not item["moods"]:
            continue
        # display_name 누락 시 원래 이름으로 폴백
        if not item.get("display_name"):
            item["display_name"] = by_id[eid]["name"]
        # 힌트에 가게 이름이 새어 나오면 탈락 → 재시도 대상
        place_name = by_id[eid]["name"].split()[0]
        if len(place_name) >= 2 and place_name in item["hint"]:
            continue
        ok[eid] = item
    return ok


def run(cfg: dict, places: list[dict]) -> list[dict]:
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    gen = cfg["generation"]
    moods = cfg["moods"]
    mood_set = set(moods)
    bs = gen["batch_size"]

    # 재개: 이미 생성된 external_id는 건너뜀 (중간에 끊겨도 처음부터 안 함)
    out_file = stage_file(cfg, "generated")
    done, done_ids = [], set()
    if out_file.exists():
        done = json.loads(out_file.read_text(encoding="utf-8"))
        done_ids = {p["external_id"] for p in done}
        print(f"[generate] 기존 {len(done)}곳 이어받기 — 나머지만 생성")
    remaining = [p for p in places if p["external_id"] not in done_ids]

    failed = []
    for i in range(0, len(remaining), bs):
        batch = remaining[i:i + bs]
        results: dict[str, dict] = {}
        for attempt in (1, 2):
            pending = [p for p in batch if p["external_id"] not in results]
            if not pending:
                break
            try:
                resp = client.messages.create(
                    model=gen["model"],
                    max_tokens=4096,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": _build_prompt(pending, moods)}],
                )
                results.update(_parse(resp.content[0].text, pending, mood_set))
            except Exception as e:
                print(f"  [generate] batch {i // bs} attempt {attempt} 오류: {e}")

        for p in batch:
            r = results.get(p["external_id"])
            if r:
                p.update(hint=r["hint"], display_name=r["display_name"],
                         description=r["description"],
                         reveal_text=r["reveal_text"], moods=r["moods"])
                done.append(p)
            else:
                failed.append(p)
        # 매 배치마다 중간 저장 — 여기서 끊겨도 done까지는 안전
        out_file.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[generate] {len(done)}곳 누적 저장 ({min(i + bs, len(remaining))}/{len(remaining)} 처리)")

    if failed:
        stage_file(cfg, "generation_failed").write_text(
            json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[generate] 실패 {len(failed)}곳 (다음 실행 때 재시도됨)")
    print(f"[generate] 완료 — 총 {len(done)}곳")
    return done
