"""
Output manager - Save generated assertions and injected code to readable .py files
Similar to nl2postcond approach for human-readable output organization
"""

import os
from pathlib import Path
from typing import Optional
from dataclasses import asdict
from datetime import datetime


class OutputManager:
    """Manages output directories and saves .py files for assertions and injected code."""
    
    def __init__(self, base_output_dir: str = "data/outputs"):
        """Initialize output manager with base directory."""
        self.base_dir = Path(base_output_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
    def get_repo_output_dir(self, repo: str, pr_number: int) -> Path:
        """
        Get/create output directory for a specific PR.
        Format: data/outputs/{repo_name}/{pr_number}/
        """
        repo_name = repo.replace('/', '_')
        output_dir = self.base_dir / repo_name / str(pr_number)
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
    
    def save_generated_assertions(
        self,
        repo: str,
        pr_number: int,
        function_name: str,
        docstring: str,
        assertions: list[str],
        function_code: str
    ) -> Path:
        """
        Save generated assertions to a readable .py file.
        
        Format: {function_name}_assertions_generated.py
        Contains: docstring, original function, and each assertion
        """
        output_dir = self.get_repo_output_dir(repo, pr_number)
        assertions_dir = output_dir / "01_assertions_generated"
        assertions_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = assertions_dir / f"{function_name}_assertions.py"
        
        content = self._format_assertions_file(
            function_name=function_name,
            docstring=docstring,
            function_code=function_code,
            assertions=assertions
        )
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return file_path
    
    def save_injected_function_pre(
        self,
        repo: str,
        pr_number: int,
        function_name: str,
        pre_commit_code: str,
        assertions: list[str]
    ) -> Path:
        """
        Save pre-commit code wrapped with assertion (nl2postcond pattern).

        Dùng wrap_assertion() để tạo wrapper function giống hệt pattern thực tế pipeline:
            def func_original(args): <pre-commit buggy impl>
            def func(args):
                return_value = func_original(args)
                assert return_value ...   # assertion từ LLM
                return return_value

        Format: {function_name}_wrapped_pre_commit.py
        """
        from src.nl2postcond.interface import wrap_assertion

        output_dir = self.get_repo_output_dir(repo, pr_number)
        injected_dir = output_dir / "02_injected_code"
        injected_dir.mkdir(parents=True, exist_ok=True)

        file_path = injected_dir / f"{function_name}_wrapped_pre_commit.py"

        # Dùng assertion đầu tiên (mỗi hàm chỉ có 1 assertion)
        assertion = assertions[0] if assertions else "assert return_value is not None"

        try:
            wrapped_code = wrap_assertion(pre_commit_code, function_name, assertion)
        except Exception as e:
            wrapped_code = f"# wrap_assertion failed: {e}\n{pre_commit_code}"

        content = self._format_wrapped_code_file(
            function_name=function_name,
            wrapped_code=wrapped_code,
            assertion=assertion,
            commit_type="PRE-COMMIT (with bug)"
        )

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return file_path
    
    def save_injected_function_post(
        self,
        repo: str,
        pr_number: int,
        function_name: str,
        post_commit_code: str,
        assertions: list[str]
    ) -> Path:
        """
        Save post-commit code wrapped with assertion (nl2postcond pattern).

        Dùng wrap_assertion() để tạo wrapper function giống hệt pattern thực tế pipeline:
            def func_original(args): <post-commit fixed impl>
            def func(args):
                return_value = func_original(args)
                assert return_value ...   # assertion từ LLM
                return return_value

        Format: {function_name}_wrapped_post_commit.py
        """
        from src.nl2postcond.interface import wrap_assertion

        output_dir = self.get_repo_output_dir(repo, pr_number)
        injected_dir = output_dir / "02_injected_code"
        injected_dir.mkdir(parents=True, exist_ok=True)

        file_path = injected_dir / f"{function_name}_wrapped_post_commit.py"

        assertion = assertions[0] if assertions else "assert return_value is not None"

        try:
            wrapped_code = wrap_assertion(post_commit_code, function_name, assertion)
        except Exception as e:
            wrapped_code = f"# wrap_assertion failed: {e}\n{post_commit_code}"

        content = self._format_wrapped_code_file(
            function_name=function_name,
            wrapped_code=wrapped_code,
            assertion=assertion,
            commit_type="POST-COMMIT (fixed)"
        )

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return file_path
    
    def save_mutation_result(
        self,
        repo: str,
        pr_number: int,
        function_name: str,
        mutant_code: str,
        mutant_id: int,
        correct_assertions: list[str]
    ) -> Path:
        """
        Save mutant code with injected correct assertions.
        
        Format: {function_name}_mutant_{mutant_id}.py
        """
        output_dir = self.get_repo_output_dir(repo, pr_number)
        mutants_dir = output_dir / "03_mutants"
        mutants_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = mutants_dir / f"{function_name}_mutant_{mutant_id}.py"
        
        content = self._format_mutant_file(
            function_name=function_name,
            code=mutant_code,
            assertions=correct_assertions,
            mutant_id=mutant_id
        )
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return file_path
    
    def save_tier1_summary(
        self,
        repo: str,
        pr_number: int,
        function_name: str,
        tier1_results: list[dict]
    ) -> Path:
        """
        Save Tier 1 results summary as .py file.
        
        Format: {function_name}_tier1_summary.py
        Contains: assertion correctness evaluation results
        """
        output_dir = self.get_repo_output_dir(repo, pr_number)
        summary_dir = output_dir / "04_results_tier1"
        summary_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = summary_dir / f"{function_name}_tier1_summary.py"
        
        content = self._format_tier1_summary(
            function_name=function_name,
            results=tier1_results
        )
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return file_path
    
    def save_tier2_summary(
        self,
        repo: str,
        pr_number: int,
        function_name: str,
        mutation_result: dict
    ) -> Path:
        """
        Save Tier 2 mutation results summary as .py file.
        
        Format: {function_name}_tier2_summary.py
        Contains: mutation score and per-assertion kill data
        """
        output_dir = self.get_repo_output_dir(repo, pr_number)
        summary_dir = output_dir / "05_results_tier2"
        summary_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = summary_dir / f"{function_name}_tier2_summary.py"
        
        content = self._format_tier2_summary(
            function_name=function_name,
            result=mutation_result
        )
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return file_path
    
    # =========================================================================
    # Private formatting methods
    # =========================================================================
    
    def _format_assertions_file(
        self,
        function_name: str,
        docstring: str,
        function_code: str,
        assertions: list[str]
    ) -> str:
        """Format generated assertions file."""
        content = f'''"""
Generated Assertions for function: {function_name}
Generated at: {datetime.now().isoformat()}
"""

# Original Docstring
"""
{docstring}
"""

# Original Function (post-commit)
{function_code}


# ============================================================================
# GENERATED ASSERTIONS ({len(assertions)} total)
# ============================================================================

# Assertions to be injected before return statements:
ASSERTIONS = [
'''
        
        for i, assertion in enumerate(assertions, 1):
            content += f'    # [{i}] {assertion}\n'
            content += f'    "{assertion}",\n'
        
        content += ''']

# ============================================================================
# HOW TO USE THESE ASSERTIONS:
# ============================================================================
#
# These assertions will be injected before each return statement:
#
# Example:
#     def function(args):
#         ...
#         result = compute()
#         assert result is not None  # Injected assertion
#         return result
#
# The assertions should:
# - Define postconditions on the function output
# - Be executable without raising exceptions
# - Help detect bugs in the implementation
#
'''
        return content
    
    def _format_wrapped_code_file(
        self,
        function_name: str,
        wrapped_code: str,
        assertion: str,
        commit_type: str
    ) -> str:
        """Format wrapped code file — shows EXACT code run by the pipeline.

        Uses wrap_assertion() pattern (nl2postcond):
            def func_original(args): <original impl>
            def func(args):
                return_value = func_original(args)
                import re
                assert return_value ...
                return return_value
        """
        sep = "=" * 74
        lines = [
            '"""',
            "Function Wrapped with Assertion (nl2postcond pattern)",
            f"Function: {function_name}",
            f"Commit Type: {commit_type}",
            f"Generated at: {datetime.now().isoformat()}",
            "",
            "This is the EXACT code executed by the pipeline to evaluate the assertion.",
            "Pattern: func_original() holds the implementation, func() is the wrapper",
            "that captures return_value and checks the assertion.",
            '"""',
            "",
            "# Assertion being tested:",
            f"# {assertion}",
            "",
            f"# {sep}",
            "# WRAPPED CODE (exact code executed by pipeline)",
            f"# {sep}",
            "",
            wrapped_code,
            "",
            f"# {sep}",
            "# EVALUATION LOGIC",
            f"# {sep}",
            "#",
            f"# When the test calls {function_name}(args):",
            f"#   1. {function_name}_original(args) runs the actual implementation",
            "#   2. return_value = result is captured",
            f"#   3. `{assertion}` is checked",
            '#   4. AssertionError if assertion fails -> status = "fail"',
            '#   5. Returns return_value normally -> status = "pass"',
            "#",
            "# For Tier 1 Correctness:",
            "#   CORRECT = PRE-COMMIT fails + POST-COMMIT passes",
            "#             (assertion detects the bug)",
            "#",
        ]
        return "\n".join(lines) + "\n"


    def _format_injected_code_file(
        self,
        function_name: str,
        code: str,
        assertions: list[str],
        commit_type: str
    ) -> str:
        """Legacy format — kept for backward compatibility. Use _format_wrapped_code_file instead."""
        best_assertion = assertions[0] if assertions else "pass"
        content = f'# [LEGACY] Function: {function_name} | {commit_type}\n'
        content += f'# Assertion: {best_assertion}\n\n'
        content += code
        return content
    
    def _format_mutant_file(
        self,
        function_name: str,
        code: str,
        assertions: list[str],
        mutant_id: int
    ) -> str:
        """Format mutant code file."""
        content = f'''"""
Mutant Code for Tier 2 Evaluation
Function: {function_name}
Mutant ID: {mutant_id}
Generated at: {datetime.now().isoformat()}

This is a MUTATED version of the post-commit (fixed) code.
Assertions will be injected and tested to see if they KILL the mutant.
"""

# Mutated code (only CORRECT assertions from Tier 1 will be tested):

{code}


"""
============================================================================
INJECTED ASSERTIONS TO TEST
============================================================================

These are only the CORRECT assertions from Tier 1
(those that detected the bug: pre=fail, post=pass)
"""

CORRECT_ASSERTIONS = [
'''
        
        for i, assertion in enumerate(assertions, 1):
            content += f'    # [{i}]\n    "{assertion}",\n'
        
        content += ''']

# ============================================================================
# MUTATION TESTING RESULT INTERPRETATION:
# ============================================================================
#
# KILLED: If ANY assertion fails on this mutant, the mutant is KILLED
#         This means the assertion detects this particular mutation
#
# SURVIVED: If ALL assertions pass on this mutant, the mutant SURVIVED
#           This means the assertions don't detect this mutation
#
# Mutation Score = number of killed mutants / total mutants
#
'''
        return content
    
    def _format_tier1_summary(
        self,
        function_name: str,
        results: list[dict]
    ) -> str:
        """Format Tier 1 summary file."""
        content = f'''"""
TIER 1 RESULTS SUMMARY
Function: {function_name}
Correctness Evaluation
Generated at: {datetime.now().isoformat()}
"""

# ============================================================================
# RESULTS
# ============================================================================

TIER1_RESULTS = [
'''
        
        for i, result in enumerate(results, 1):
            correct = result.get('is_correct', False)
            correct_str = 'CORRECT' if correct else 'INCORRECT'
            content += f'''    {{
        "index": {i},
        "status": "{correct_str}",
        "assertion": "{result.get('assertion_code', '')}",
        "pre_status": "{result.get('pre_status', '')}",
        "post_status": "{result.get('post_status', '')}",
        "is_correct": {str(correct).lower()},
        "pre_error_msg": "{result.get('pre_error_msg', '')}",
        "post_error_msg": "{result.get('post_error_msg', '')}",
    }},
'''
        
        content += ''']

# ============================================================================
# SUMMARY STATISTICS
# ============================================================================

TOTAL_ASSERTIONS = ''' + str(len(results)) + '''
CORRECT_ASSERTIONS = ''' + str(sum(1 for r in results if r.get('is_correct'))) + '''
INCORRECT_ASSERTIONS = ''' + str(sum(1 for r in results if not r.get('is_correct'))) + '''
CORRECTNESS_RATE = {:.1f}%

# ============================================================================
# NEXT STEP: TIER 2
# ============================================================================
#
# Only CORRECT assertions will proceed to Tier 2 mutation testing.
# If CORRECT_ASSERTIONS > 0, run Tier 2 to evaluate mutation scores.
# If CORRECT_ASSERTIONS == 0, Tier 2 will be skipped for this function.
#
'''.format(100.0 * sum(1 for r in results if r.get('is_correct')) / len(results) if results else 0)
        
        return content
    
    def _format_tier2_summary(
        self,
        function_name: str,
        result: dict
    ) -> str:
        """Format Tier 2 summary file."""
        content = f'''"""
TIER 2 RESULTS SUMMARY
Function: {function_name}
Mutation Testing Evaluation
Generated at: {datetime.now().isoformat()}
"""

# ============================================================================
# MUTATION TESTING RESULTS
# ============================================================================

TIER2_RESULT = {{
    "function_name": "{result.get('function_name', '')}",
    "total_mutants": {result.get('total_mutants', 0)},
    "killed": {result.get('killed', 0)},
    "survived": {result.get('survived', 0)},
    "errors": {result.get('errors', 0)},
    "mutation_score": {result.get('mutation_score', 0):.3f},
}}

# ============================================================================
# MUTATION SCORE INTERPRETATION
# ============================================================================
#
# Mutation Score = killed / total_mutants
# Range: 0.0 to 1.0
#
# Score >= 0.8: EXCELLENT - Assertions catch most mutations
# Score 0.6-0.8: GOOD - Assertions catch majority of mutations
# Score 0.4-0.6: FAIR - Assertions catch many but not most mutations
# Score < 0.4: WEAK - Assertions miss many mutations
#
# Killed: Number of mutants detected (assertion failed on mutant)
# Survived: Number of mutants not detected (assertion passed on mutant)
# Errors: Mutants that caused errors (not counted as killed)
#
'''
        
        # Add per-assertion kills if available
        if result.get('per_assertion_kills'):
            content += '''
# ============================================================================
# PER-ASSERTION KILL BREAKDOWN
# ============================================================================

PER_ASSERTION_KILLS = {
'''
            for assertion, mutant_ids in result.get('per_assertion_kills', {}).items():
                content += f'''    "{assertion}": {{\n'''
                content += f'''        "kill_count": {len(mutant_ids)},\n'''
                content += f'''        "mutant_ids": {mutant_ids},\n'''
                content += f'''    }},\n'''
            
            content += '''}
'''
        
        return content
