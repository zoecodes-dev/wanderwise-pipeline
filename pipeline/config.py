"""Config 로딩 + 공용 유틸."""
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def load_config(config_path: str) -> dict:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_path"] = config_path
    return cfg


def env(key: str, required: bool = True) -> str | None:
    val = os.getenv(key)
    if required and not val:
        raise RuntimeError(f"환경변수 {key} 가 설정되지 않았습니다 (.env 확인)")
    return val


def stage_file(cfg: dict, stage: str) -> Path:
    """단계별 중간 산출물 경로. 멱등 실행의 기준이 됨."""
    return DATA_DIR / f"{cfg['city']}_{stage}.json"
