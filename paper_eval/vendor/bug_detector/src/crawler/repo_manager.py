"""Repo manager — clone and checkout repos at pre/post commit SHAs."""

import os
import shutil
from pathlib import Path
from git import Repo, GitCommandError

from src.tracker.logger import log_info, log_warn, log_error, log_debug


class RepoManager:
    """Quản lý việc clone và checkout repo ở 2 trạng thái pre/post commit."""

    def __init__(self, repo_name: str, cache_dir: str):
        """
        Args:
            repo_name: e.g. "marshmallow-code/marshmallow"
            cache_dir: e.g. ".cache"
        """
        self.repo_name = repo_name
        self.cache_dir = Path(cache_dir)
        self.clone_base = self.cache_dir / "clones" / repo_name
        self.clone_url = f"https://github.com/{repo_name}.git"

    def _clone_or_open(self, target_dir: Path) -> Repo:
        """Clone repo nếu chưa có, mở nếu đã clone."""
        if target_dir.exists() and (target_dir / ".git").exists():
            log_debug(f"Repo already cloned at {target_dir}")
            repo = Repo(str(target_dir))
            # Fetch latest to ensure we have the SHA
            try:
                repo.remotes.origin.fetch()
                log_debug(f"Fetched latest for {target_dir}")
            except GitCommandError as e:
                log_warn(f"Failed to fetch: {e}")
            return repo

        # Clone fresh
        log_info(f"Cloning {self.repo_name} into {target_dir}...")
        target_dir.mkdir(parents=True, exist_ok=True)
        repo = Repo.clone_from(self.clone_url, str(target_dir))
        log_info(f"Clone complete: {target_dir}")
        return repo

    def _checkout(self, repo: Repo, sha: str) -> None:
        """Checkout a specific commit SHA, with reset/clean for safety."""
        try:
            repo.git.reset("--hard")
            # On Windows, git clean can fail due to permission issues
            # Try to clean, but don't fail if it doesn't work
            try:
                repo.git.clean("-f", "-d")
            except GitCommandError as e:
                log_debug(f"git clean failed (can happen on Windows): {e} — continuing anyway")
            repo.git.checkout(sha)
            log_debug(f"Checked out {sha}")
        except GitCommandError:
            # If checkout fails, try fetching first then retry
            log_warn(f"Checkout {sha} failed, fetching and retrying...")
            try:
                repo.remotes.origin.fetch()
                repo.git.reset("--hard")
                # Retry clean but don't fail
                try:
                    repo.git.clean("-f", "-d")
                except GitCommandError as e:
                    log_debug(f"git clean failed on retry: {e} — continuing anyway")
                repo.git.checkout(sha)
                log_debug(f"Checked out {sha} after fetch")
            except GitCommandError as e:
                log_error(f"Failed to checkout {sha}: {e}")
                raise

    def get_pre_commit_dir(self, sha: str) -> str:
        """Clone nếu chưa có, checkout sha, trả về đường dẫn thư mục."""
        target_dir = self.clone_base / "pre"
        repo = self._clone_or_open(target_dir)
        self._checkout(repo, sha)
        log_info(f"Pre-commit ready at {target_dir} (SHA: {sha[:8]})")
        return str(target_dir)

    def get_post_commit_dir(self, sha: str) -> str:
        """Tương tự cho post commit."""
        target_dir = self.clone_base / "post"
        repo = self._clone_or_open(target_dir)
        self._checkout(repo, sha)
        log_info(f"Post-commit ready at {target_dir} (SHA: {sha[:8]})")
        return str(target_dir)

    def cleanup(self) -> None:
        """Xóa các clone tạm nếu cần."""
        if self.clone_base.exists():
            log_info(f"Cleaning up clones at {self.clone_base}")
            shutil.rmtree(str(self.clone_base), ignore_errors=True)
            log_info("Cleanup complete")
