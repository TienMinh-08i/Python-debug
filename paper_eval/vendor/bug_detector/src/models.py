from dataclasses import dataclass, field
from typing import Optional

@dataclass
class FunctionInfo:
    """Một hàm được lấy từ PR, có cả pre và post commit version."""
    name: str
    repo: str                    # vd: "marshmallow-code/marshmallow"
    pr_number: int
    docstring: str
    
    pre_commit_code: str         # source code hàm TRƯỚC khi sửa (có bug)
    post_commit_code: str        # source code hàm SAU khi sửa (đúng)
    pre_commit_sha: str
    post_commit_sha: str
    
    file_path: str               # relative path trong repo
    lineno: int
    test_files: list[str] = field(default_factory=list)  # file test liên quan


@dataclass
class AssertionResult:
    """Kết quả đánh giá một assertion trên một hàm (Tầng 1)."""
    function_name: str
    assertion_code: str          # đoạn assert sinh ra
    
    pre_status: str              # "pass" | "fail" | "error"
    post_status: str             # "pass" | "fail" | "error"
    pre_error_msg: str = ""
    post_error_msg: str = ""
    
    @property
    def is_correct(self) -> bool:
        """Assertion CORRECT khi fail trên pre, pass trên post."""
        return self.pre_status == "fail" and self.post_status == "pass"


@dataclass
class MutationResult:
    """Kết quả mutation testing cho một hàm (Tầng 2)."""
    function_name: str
    total_mutants: int
    killed: int
    survived: int
    errors: int                  # mutant gây ra crash, không tính killed
    
    per_assertion_kills: dict = field(default_factory=dict)
    # {assertion_code: [mutant_id, ...]}  — assertion nào kill mutant nào
    
    @property
    def mutation_score(self) -> float:
        if self.total_mutants == 0:
            return 0.0
        return self.killed / self.total_mutants


@dataclass
class ExecResult:
    """Kết quả chạy một đoạn code."""
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    
    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out
    
    @property
    def status(self) -> str:
        if self.timed_out: return "timeout"
        if self.exit_code == 0: return "pass"
        output = f"{self.stdout}\n{self.stderr}"
        if "AssertionError" in output or " assert " in output: return "fail"
        return "error"
