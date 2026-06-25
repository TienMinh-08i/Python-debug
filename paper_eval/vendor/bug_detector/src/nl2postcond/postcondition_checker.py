"""
Custom implementation of postcondition checking for evalplus
This module provides untrusted_postcondition_check which is not in the standard evalplus package
"""

import multiprocessing
from multiprocessing import Value, Array
from typing import Any, List, Tuple
import time
import numpy as np

from evalplus.eval import (
    create_tempdir,
    reliability_guard,
    swallow_io,
    time_limit,
    SUCCESS,
    FAILED,
    TIMEOUT,
)

# Status codes (same as evalplus)
_UNKNOWN = 0
_SUCCESS = 1
_FAILED = 2
_TIMEOUT = 3

_mapping = {
    _UNKNOWN: TIMEOUT,
    _SUCCESS: SUCCESS,
    _FAILED: FAILED,
    _TIMEOUT: TIMEOUT,
}


def unsafe_postcondition_execute(
    entry_point: str,
    code: str,
    inputs: List[Any],
    time_limits: List[float],
    stat: Value,
    details: Array,
    progress: Value,
):
    """
    Execute code with buggy inputs and check if postcondition catches them.
    
    For postcondition checking:
    - If postcondition raises an exception (AssertionError, etc.), it "killed" the buggy input -> details[i] = True
    - If postcondition passes (no exception), it failed to detect the bug -> details[i] = False
    """
    with create_tempdir():
        # These system calls are needed when cleaning up tempdir.
        import os
        import shutil

        rmtree = shutil.rmtree
        rmdir = os.rmdir
        chdir = os.chdir
        
        # Disable functionalities that can make destructive changes to the test.
        # allow only 4GB memory usage
        maximum_memory_bytes = 4 * 1024 * 1024 * 1024
        reliability_guard(maximum_memory_bytes=maximum_memory_bytes)
        
        exec_globals = {}
        try:
            with swallow_io():
                exec(code, exec_globals)
                fn = exec_globals[entry_point]
                
                for i, inp in enumerate(inputs):
                    try:
                        with time_limit(time_limits[i]):
                            # Run the function with buggy input
                            out = fn(*inp)
                        
                        # If we reach here, postcondition did NOT raise an exception
                        # This means it failed to detect the bug
                        details[i] = False
                        progress.value += 1
                        
                    except (AssertionError, Exception):
                        # Postcondition raised an exception - it detected the bug!
                        # This is GOOD - it "killed" the buggy input
                        details[i] = True
                        progress.value += 1
                        continue
                
            # If at least one bug was killed, consider it success
            if any(details[:progress.value]):
                stat.value = _SUCCESS
            else:
                stat.value = _FAILED
                
        except BaseException:
            stat.value = _FAILED
            
        # Needed for cleaning up.
        shutil.rmtree = rmtree
        os.rmdir = rmdir
        os.chdir = chdir


def untrusted_postcondition_check(
    code: str,
    inputs: List[Any],
    entry_point: str,
    ref_time: List[float],
    min_time_limit: float = 0.1,
    gt_time_limit_factor: float = 2.0,
) -> Tuple[str, List[int]]:
    """
    Check if a postcondition can catch buggy inputs.
    
    Args:
        code: The wrapped code with postcondition
        inputs: The buggy inputs to test
        entry_point: The function entry point
        ref_time: Reference execution times
        min_time_limit: Minimum time limit
        gt_time_limit_factor: Time limit factor
        
    Returns:
        A tuple of (status_string, details_list)
        - status_string: "killed at least one mutant" or "failed to kill any mutant"
        - details_list: List of 1s (killed) and 0s (not killed) for each input
    """
    # Calculate time limits
    time_limits = [max(min_time_limit, gt_time_limit_factor * t) for t in ref_time]
    timeout = sum(time_limits) + 2
    
    # Shared memory objects
    progress = Value("i", 0)
    stat = Value("i", _UNKNOWN)
    details = Array("b", [False for _ in range(len(inputs))])
    
    p = multiprocessing.Process(
        target=unsafe_postcondition_execute,
        args=(
            entry_point,
            code,
            inputs,
            time_limits,
            stat,
            details,
            progress,
        ),
    )
    p.start()
    p.join(timeout=timeout + 1)
    
    if p.is_alive():
        p.terminate()
        time.sleep(0.1)
    if p.is_alive():
        p.kill()
        time.sleep(0.1)
    
    stat_value = _mapping[stat.value]
    details_list = [int(details[i]) for i in range(progress.value)]
    
    if not stat_value:
        stat_value = TIMEOUT
    
    # Determine status string
    if stat_value == SUCCESS or any(details_list):
        status_string = "killed at least one mutant"
    else:
        status_string = "failed to kill any mutant"
    
    return (status_string, details_list)
