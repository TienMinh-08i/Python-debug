"""Workspace management for mutmut mutation testing."""

import shutil
import tempfile
from pathlib import Path

from src.tracker.logger import log_info, log_warn, log_error, log_debug


class MutantWorkspace:
    """Quản lý thư mục làm việc sạch cho mutmut."""

    def __init__(self, post_commit_dir: str, cache_dir: str, repo: str, pr: int):
        """
        Args:
            post_commit_dir: path to post-commit source code
            cache_dir: base cache directory (e.g., ".cache")
            repo: repo name (e.g., "marshmallow-code/marshmallow")
            pr: PR number
        """
        self.post_commit_dir = Path(post_commit_dir)
        self.cache_dir = Path(cache_dir)
        self.repo = repo
        self.pr = pr
        
        # Compute workspace path: .cache/mutants/{repo}/{pr}/{random_id}
        self.workspace_base = self.cache_dir / "mutants" / repo / str(pr)
        self.workspace_path: Path | None = None
        self._temp_dir: tempfile.TemporaryDirectory | None = None

    def setup(self) -> str:
        """
        Copy post_commit_dir vào workspace tạm.
        Trả về workspace path.
        """
        # Ensure workspace base directory exists
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        
        # Create temporary directory
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix=f"mutmut_{self.pr}_",
            dir=str(self.workspace_base)
        )
        self.workspace_path = Path(self._temp_dir.name)
        
        # Copy post-commit code into workspace
        log_info(f"Setting up mutant workspace at {self.workspace_path}")
        
        try:
            # Copy all files from post_commit_dir to workspace
            for item in self.post_commit_dir.rglob("*"):
                if item.is_file():
                    # Preserve directory structure
                    rel_path = item.relative_to(self.post_commit_dir)
                    target = self.workspace_path / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
            
            log_debug(f"Workspace setup complete: {self.workspace_path}")
            return str(self.workspace_path)
        
        except Exception as e:
            log_error(f"Failed to set up workspace: {e}")
            self.cleanup()
            raise

    def cleanup(self) -> None:
        """Xóa workspace."""
        if self._temp_dir is not None:
            try:
                self._temp_dir.cleanup()
                log_debug(f"Workspace cleaned up: {self.workspace_path}")
            except Exception as e:
                log_warn(f"Failed to clean up workspace: {e}")
            finally:
                self._temp_dir = None
                self.workspace_path = None

    def __enter__(self) -> str:
        """Context manager entry."""
        return self.setup()

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.cleanup()

