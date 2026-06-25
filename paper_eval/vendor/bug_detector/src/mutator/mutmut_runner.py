"""Mutmut runner — execute mutmut CLI and collect results."""

import re
import subprocess
from pathlib import Path

from src.tracker.logger import log_info, log_warn, log_error, log_debug


def run_mutmut(workspace_dir: str, target_file: str, config: dict) -> list[str]:
    """
    Chạy: mutmut run --paths-to-mutate {target_file}
    Parse output để lấy list mutant IDs.
    Trả về list[int] — các ID của mutant sinh ra.
    """
    workspace_path = Path(workspace_dir)
    
    # Construct the target file path relative to workspace
    # target_file might be like "src/field.py"
    target_relative = target_file
    
    log_info(f"Running mutmut on {target_relative} in {workspace_path}")
    
    try:
        # Run mutmut to generate mutants
        # Command: mutmut run --paths-to-mutate src/field.py --tests-dir tests
        
        # Find tests directory
        tests_dir = None
        if Path(workspace_path / "tests").exists():
            tests_dir = "tests"
        elif Path(workspace_path / "test").exists():
            tests_dir = "test"
        
        cmd = [
            "mutmut",
            "run",
            "--paths-to-mutate",
            target_relative,
        ]
        
        if tests_dir:
            cmd.extend(["--tests-dir", tests_dir])
            log_debug(f"Using tests directory: {tests_dir}")
        
        # Set working directory to workspace
        result = subprocess.run(
            cmd,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            timeout=300,
        )
        
        # mutmut exit codes: 0=all killed, 1=error, 2=survived, 4=timeout, 8=slow
        # We consider 0, 2, 4, 8 as successful runs (mutants were generated)
        if result.returncode not in [0, 1, 2, 4, 8]:
            log_warn(f"mutmut run returned code {result.returncode}")
            log_debug(f"stdout: {result.stdout}")
            log_debug(f"stderr: {result.stderr}")
        
        # Now get the list of mutant IDs
        mutant_ids = get_mutant_ids(workspace_dir)
        log_info(f"Generated {len(mutant_ids)} mutants: {mutant_ids[:10]}...")
        
        return mutant_ids
    
    except subprocess.TimeoutExpired:
        log_error(f"mutmut run timed out after 300s")
        return []
    except Exception as e:
        log_error(f"Failed to run mutmut: {type(e).__name__}: {e}")
        return []


def get_mutant_ids(workspace_dir: str) -> list[str]:
    """
    Chạy: mutmut results
    Parse để lấy list ID.
    Trả về list[int] — các ID của mutant.
    """
    workspace_path = Path(workspace_dir)
    
    try:
        # Run mutmut results to get all mutant results
        # Command: mutmut results
        cmd = ["mutmut", "results"]
        
        result = subprocess.run(
            cmd,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode != 0:
            log_warn(f"mutmut results returned code {result.returncode}")
            log_debug(f"stderr: {result.stderr}")
            return []
        
        # Parse output to extract mutant IDs
        # Output format from mutmut 2.x is like:
        #   src/field.py:10 mutant (line 10) - KILLED
        #   src/field.py:20 mutant (line 20) - SURVIVED
        # We need to extract line numbers as mutant identifiers
        
        mutant_ids = set()
        lines = result.stdout.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Parse mutant key - format is like "path/file.py:line mutant ..."
            # Extract the line number
            match = re.search(r'(?<!\d)(\d+)(?!\d)', line)
            if match:
                mutant_id = match.group(1)
                mutant_ids.add(mutant_id)

        if mutant_ids:
            return sorted(mutant_ids, key=lambda x: int(x) if x.isdigit() else x)

        # mutmut 2.x can produce sparse/empty `results` output. Probe ids.
        probed: list[str] = []
        for i in range(1, 5001):
            show = subprocess.run(
                ["mutmut", "show", str(i)],
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if show.returncode != 0:
                break
            probed.append(str(i))

        return probed
    
    except subprocess.TimeoutExpired:
        log_error(f"mutmut results timed out after 60s")
        return []
    except Exception as e:
        log_error(f"Failed to get mutant IDs: {type(e).__name__}: {e}")
        return []
