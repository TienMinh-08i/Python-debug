"""Cache module — read/write JSON cache files under .cache/results/."""

import json
from pathlib import Path
from typing import Optional


def _get_cache_dir() -> str:
    """Read cache_dir from configs/config.json, default to '.cache'."""
    config_path = Path("configs/config.json")
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return config.get("cache_dir", ".cache")
        except (json.JSONDecodeError, KeyError):
            pass
    return ".cache"


def _cache_path(repo: str, pr_number: int, key: str) -> Path:
    """Build the full path: {cache_dir}/results/{repo}/{pr_number}/{key}.json"""
    cache_dir = _get_cache_dir()
    return Path(cache_dir) / "results" / repo / str(pr_number) / f"{key}.json"


def cache_exists(repo: str, pr_number: int, key: str) -> bool:
    """Kiểm tra cache có tồn tại không."""
    return _cache_path(repo, pr_number, key).exists()


def load_cache(repo: str, pr_number: int, key: str) -> Optional[dict]:
    """Trả về None nếu chưa có cache."""
    path = _cache_path(repo, pr_number, key)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(repo: str, pr_number: int, key: str, data: dict) -> None:
    """Lưu data vào .cache/results/{repo}/{pr_number}/{key}.json"""
    path = _cache_path(repo, pr_number, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_functions_cache(repo: str, pr_number: int) -> Optional[list]:
    """Load .cache/results/{repo}/{pr_number}/functions.json"""
    path = _cache_path(repo, pr_number, "functions")
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_functions_cache(repo: str, pr_number: int, functions: list) -> None:
    """Save functions list to .cache/results/{repo}/{pr_number}/functions.json"""
    path = _cache_path(repo, pr_number, "functions")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(functions, f, indent=2, ensure_ascii=False)
