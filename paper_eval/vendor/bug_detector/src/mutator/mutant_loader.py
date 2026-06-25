"""Mutant loader — apply and restore mutants during testing."""

import subprocess
from pathlib import Path

from src.tracker.logger import log_info, log_warn, log_error, log_debug


def apply_mutant(mutant_id: int | str, workspace_dir: str, target_file: str) -> str:
    """
    Chạy: mutmut apply {mutant_id}
    Đọc file đã bị mutate.
    Trả về source code của mutant.
    """
    workspace_path = Path(workspace_dir)
    
    try:
        mutant_name = str(mutant_id)
        
        log_debug(f"Applying mutant {mutant_name}")
        
        # Run mutmut apply to apply the mutant
        cmd = ["mutmut", "apply", mutant_name]
        
        result = subprocess.run(
            cmd,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            log_warn(f"mutmut apply returned code {result.returncode}")
            log_debug(f"stderr: {result.stderr}")
            # Try to read the file anyway
        
        # Read the mutated file
        target_path = workspace_path / target_file
        if not target_path.exists():
            raise FileNotFoundError(f"Target file not found: {target_path}")
        
        with open(target_path, 'r', encoding='utf-8') as f:
            mutant_source = f.read()
        
        log_debug(f"Successfully applied mutant {mutant_name}")
        return mutant_source
    
    except subprocess.TimeoutExpired:
        log_error(f"mutmut apply timed out")
        raise
    except Exception as e:
        log_error(f"Failed to apply mutant {mutant_id}: {type(e).__name__}: {e}")
        raise


def restore_original(workspace_dir: str, target_file: str, original_source: str) -> None:
    """
    Ghi lại original_source vào file.
    Luôn gọi trong finally block.
    """
    workspace_path = Path(workspace_dir)
    target_path = workspace_path / target_file
    
    try:
        log_debug(f"Restoring original {target_file}")
        
        # Write original source back to file
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(original_source)
        
        log_debug(f"Successfully restored {target_file}")
    
    except Exception as e:
        log_error(f"Failed to restore original {target_file}: {type(e).__name__}: {e}")
        # Don't raise here - we're in cleanup/finally path
