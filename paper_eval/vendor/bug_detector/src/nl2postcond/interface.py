"""
Wrapper interface cho nl2postcond — file MỚI DUY NHẤT được thêm vào trong src/nl2postcond/.

Expose hai hàm:
    generate_assertions(docstring, function_code, config) -> list[str]
    wrap_assertion(function_code, func_name, assertion_code) -> str

Toàn bộ logic bên trong (prompt template, gọi LLM, parse response, wrap code) đều
DỰA HOÀN TOÀN vào code gốc của nl2postcond — không tự viết lại.

Cụ thể:
- Prompt: dùng prompts.genOneNoRef["simple"] từ prompts.py gốc
  → has_reference_code=False: chỉ dùng docstring (function stub), KHÔNG cần implementation
- Gọi LLM: dùng hàm ask() và setup_api() từ llm_sample_generator.py gốc
- Parse response: dùng extract_code() + code_sanitize() từ response_preprocessing.py gốc
- Wrap code: dùng wrap_code_solution() từ response_preprocessing.py gốc
  → Pattern: rename func → func_original, tạo wrapper func() {
        return_value = func_original(args)
        <assertion using return_value>
        return return_value
     }
- Biến return value: `return_value` (đúng theo convention nl2postcond, KHÔNG phải `_result`)

nl2postcond sinh ĐÚNG 1 assertion mỗi lần gọi LLM.
"""

import os
import ast
import sys
import types
import traceback
import importlib
import textwrap
from pathlib import Path
from types import SimpleNamespace

# --------------------------------------------------------------------------
# Thêm thư mục nl2postcond vào sys.path để import được prompts.py, v.v.
# --------------------------------------------------------------------------
_NL2POSTCOND_DIR = str(Path(__file__).parent)
if _NL2POSTCOND_DIR not in sys.path:
    sys.path.insert(0, _NL2POSTCOND_DIR)

from src.tracker.logger import log_info, log_warn, log_error, log_debug


# --------------------------------------------------------------------------
# Import prompts.py gốc (không cần hydra/omegaconf)
# --------------------------------------------------------------------------
try:
    import prompts as _prompts
    _PROMPTS_OK = True
    _PROMPTS_ERR = None
except ImportError as e:
    _PROMPTS_OK = False
    _PROMPTS_ERR = str(e)
    _prompts = None


# --------------------------------------------------------------------------
# Import extract_code(), code_sanitize(), wrap_code_solution()
# từ response_preprocessing.py gốc bằng cách load source trực tiếp
# (importlib.util), bỏ qua top-level CLI imports hydra/evalplus/benchmarks.
# --------------------------------------------------------------------------
def _load_preprocessing_fns():
    """
    Load extract_code, code_sanitize, wrap_code_solution từ response_preprocessing.py gốc.

    response_preprocessing.py có top-level imports của hydra, evalplus, benchmarks
    — chỉ cần cho main() CLI, không cần cho các hàm helper.
    Ta tạm thời mock các module đó trong sys.modules để import thành công.
    """
    _preproc_path = Path(_NL2POSTCOND_DIR) / "response_preprocessing.py"
    if not _preproc_path.exists():
        return None, None, None, f"Không tìm thấy {_preproc_path}"

    _mock_specs = {
        "hydra":                     {"main": lambda *a, **kw: (lambda f: f), "core": types.ModuleType("hydra.core")},
        "hydra.types":               {"RunMode": None},
        "hydra.core":                {},
        "hydra.core.hydra_config":   {},
        "omegaconf":                 {"DictConfig": dict, "OmegaConf": types.ModuleType("OmegaConf")},
        "evalplus":                  {},
        "evalplus.data":             {"get_human_eval_plus": lambda: {}, "write_jsonl": lambda *a, **kw: None},
        "benchmarks":                {"load_benchmarks": lambda *a, **kw: {}},
        "log":                       {
            "OUTPUT_FOLDER": ".",
            "SUB_FOLDER": ".",
            "setup_output_dir": lambda *a, **kw: (print, print),
            "make_header": lambda s: s,
            "make_print_and_log_function": lambda *a, **kw: print,
        },
    }

    mocks_added = []
    for mod_name, attrs in _mock_specs.items():
        if mod_name not in sys.modules:
            mock = types.ModuleType(mod_name)
            for attr, val in attrs.items():
                setattr(mock, attr, val)
            sys.modules[mod_name] = mock
            mocks_added.append(mod_name)

    try:
        spec = importlib.util.spec_from_file_location(
            "response_preprocessing_nl2postcond",
            str(_preproc_path),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.extract_code, mod.code_sanitize, mod.wrap_code_solution, None
    except Exception as e:
        return None, None, None, str(e)
    finally:
        for mod_name in mocks_added:
            sys.modules.pop(mod_name, None)


def _load_llm_fns():
    """
    Load setup_api() và ask() từ llm_sample_generator.py gốc.

    llm_sample_generator.py có top-level imports của: omegaconf, hydra, evalplus,
    openai, decouple, tenacity, benchmarks, log.
    Chỉ setup_api() và ask() là cần thiết — chúng chỉ dùng google.genai và tenacity.
    Ta mock phần còn lại.
    """
    _llm_path = Path(_NL2POSTCOND_DIR) / "llm_sample_generator.py"
    if not _llm_path.exists():
        return None, None, f"Không tìm thấy {_llm_path}"

    _mock_specs = {
        "hydra":                     {"main": lambda *a, **kw: (lambda f: f), "core": types.ModuleType("hydra.core")},
        "hydra.types":               {"RunMode": None},
        "hydra.core":                {},
        "hydra.core.hydra_config":   {},
        "omegaconf":                 {"DictConfig": dict, "OmegaConf": types.ModuleType("OmegaConf")},
        "evalplus":                  {},
        "evalplus.data":             {"write_jsonl": lambda *a, **kw: None},
        "benchmarks":                {"load_benchmarks": lambda *a, **kw: {}},
        "log":                       {
            "OUTPUT_FOLDER": ".",
            "SUB_FOLDER": ".",
            "setup_output_dir": lambda *a, **kw: (print, print),
            "make_header": lambda s: s,
        },
        "openai":                    {},
        "decouple":                  {"config": lambda key: os.environ.get(key, "")},
    }

    mocks_added = []
    for mod_name, attrs in _mock_specs.items():
        if mod_name not in sys.modules:
            mock = types.ModuleType(mod_name)
            for attr, val in attrs.items():
                setattr(mock, attr, val)
            sys.modules[mod_name] = mock
            mocks_added.append(mod_name)

    try:
        spec = importlib.util.spec_from_file_location(
            "llm_sample_generator_nl2postcond",
            str(_llm_path),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.setup_api, mod.ask, None
    except Exception as e:
        return None, None, str(e)
    finally:
        for mod_name in mocks_added:
            sys.modules.pop(mod_name, None)


# Load tại module import time
_extract_code, _code_sanitize, _wrap_code_solution, _PREPROC_ERR = _load_preprocessing_fns()
_setup_api, _ask, _LLM_ERR = _load_llm_fns()

_NL2POSTCOND_AVAILABLE = (
    _PROMPTS_OK
    and _extract_code is not None
    and _code_sanitize is not None
    and _wrap_code_solution is not None
    and _setup_api is not None
    and _ask is not None
)


# --------------------------------------------------------------------------
# Helpers (không có business logic mới — chỉ glue code)
# --------------------------------------------------------------------------

def _extract_func_name(function_code: str) -> str:
    """Lấy tên hàm từ source code bằng AST."""
    function_code = textwrap.dedent(function_code)
    try:
        tree = ast.parse(function_code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
    except SyntaxError:
        pass
    import re
    match = re.search(r"def\s+(\w+)\s*\(", function_code)
    return match.group(1) if match else "unknown_function"


def _make_exper_cfg(model: str, temperature: float) -> SimpleNamespace:
    """
    Tạo experiment config object để truyền vào ask() gốc của nl2postcond.
    Các field bắt buộc theo llm_sample_generator.py: model, temperature,
    system_prompt, n_model_responses.
    """
    system_prompt = (
        _prompts.systemMessage if _PROMPTS_OK
        else "You are a programming assistant that generates executable python only."
    )
    return SimpleNamespace(
        model=model,
        temperature=temperature,
        system_prompt=system_prompt,
        n_model_responses=1,
    )


def _make_api_cfg() -> SimpleNamespace:
    """
    Tạo api config object tương đương config/api/gemini.yaml.
    'key' là tên env var — dùng bởi decouple.config() trong setup_api().
    """
    key_env = "GEMINI_API_KEY" if os.environ.get("GEMINI_API_KEY") else "GOOGLE_API_KEY"
    return SimpleNamespace(name="gemini", key=key_env)


def _build_prompt_no_ref(docstring_stub: str, func_name: str) -> str:
    """
    Dùng template genOneNoRef["simple"] từ prompts.py gốc của nl2postcond.

    Template này KHÔNG yêu cầu reference code (implementation).
    Chỉ cần function stub + docstring để sinh assertion.
    has_reference_code = False.

    Biến return value trong assertion là `return_value`.
    """
    if not _PROMPTS_OK:
        raise RuntimeError(f"Không load được prompts.py: {_PROMPTS_ERR}")
    template = _prompts.genOneNoRef["simple"]
    return template.substitute(
        codeStubAndDocstring=docstring_stub,
        toGenerateFull="symbolic postcondition",
        toGenerateShort="postcondition",
        toGenerateShortCaps="POSTCONDITION",
        entrypoint=func_name,
    )


def _make_docstring_stub(function_code: str, func_name: str) -> str:
    """
    Tạo function stub (chỉ giữ signature + docstring, bỏ phần implementation).

    nl2postcond genOneNoRef dùng stub (không cần implementation), ta parse
    bằng AST để lấy đúng signature + docstring.
    """
    function_code = textwrap.dedent(function_code)
    try:
        tree = ast.parse(function_code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Build stub: def func(args): """docstring"""
                stub_body = []
                # Keep docstring if present
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    stub_body.append(node.body[0])
                stub_body.append(ast.Pass())

                stub_func = type(node)(
                    name=node.name,
                    args=node.args,
                    body=stub_body,
                    decorator_list=[],
                    returns=node.returns,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
                ast.fix_missing_locations(stub_func)
                stub_module = ast.Module(body=[stub_func], type_ignores=[])
                ast.fix_missing_locations(stub_module)
                return ast.unparse(stub_module)
    except (SyntaxError, AttributeError):
        pass
    # Fallback: dùng toàn bộ function_code
    return function_code


def _parse_one_assertion(raw_response: str) -> str | None:
    """
    Dùng extract_code() + code_sanitize() gốc của nl2postcond để parse.

    nl2postcond sinh 1 assert mỗi lần → ta lấy dòng assert đầu tiên tìm được.
    Biến return value trong assertion là `return_value`.
    """
    code = _extract_code(raw_response)
    if not code or not code.strip():
        return None

    sanitized = _code_sanitize(code.strip())
    if sanitized is None:
        log_debug(f"code_sanitize returned None for: {code[:100]}")
        return None

    # Tìm dòng assert đầu tiên (bỏ qua comment và import)
    for line in sanitized.splitlines():
        stripped = line.strip()
        if stripped.startswith("assert "):
            try:
                ast.parse(stripped)
                return stripped
            except SyntaxError:
                log_debug(f"Assertion syntax error: {stripped}")

    return None


# --------------------------------------------------------------------------
# Public API (theo ARCHITECTURE.md và TASK-6)
# --------------------------------------------------------------------------

def generate_assertions(
    docstring: str,
    function_code: str,
    config: dict,
) -> list[str]:
    """
    Sinh ĐÚNG MỘT postcondition assertion từ docstring của hàm.

    Sử dụng hoàn toàn code gốc của nl2postcond:
    - prompts.py → genOneNoRef["simple"] template (has_reference_code=False)
      → chỉ dùng function stub + docstring, KHÔNG cần implementation
    - llm_sample_generator.py → ask() + setup_api() (Gemini API với retry)
    - response_preprocessing.py → extract_code() + code_sanitize() (parse response)

    Convention biến return value: `return_value` (theo nl2postcond gốc,
    KHÔNG phải `_result` hay `result`).

    Input:
        docstring: docstring của hàm (dùng để reference, nhưng chứa trong function stub)
        function_code: source code đầy đủ của hàm (dùng để extract stub + docstring)
        config: dict với:
            "llm": {"model": str, "temperature": float}

    Output:
        list[str] — đúng 1 phần tử nếu thành công, list rỗng nếu thất bại.
        Biến return value là `return_value` theo convention nl2postcond.
        Ví dụ:
            ['assert return_value is not None']
            ['assert isinstance(return_value, list)']

        Trả về list rỗng nếu LLM fail hoặc không parse được assertion.
        Không raise exception.
    """
    if not _NL2POSTCOND_AVAILABLE:
        errs = []
        if not _PROMPTS_OK:
            errs.append(f"prompts.py: {_PROMPTS_ERR}")
        if _PREPROC_ERR:
            errs.append(f"response_preprocessing.py: {_PREPROC_ERR}")
        if _LLM_ERR:
            errs.append(f"llm_sample_generator.py: {_LLM_ERR}")
        log_error("nl2postcond không khả dụng. " + "; ".join(errs))
        return []

    llm_cfg = config.get("llm", {})
    model = llm_cfg.get("model", "gemini-2.0-flash")
    temperature = llm_cfg.get("temperature", 0.7)

    func_name = _extract_func_name(function_code)
    log_info(f"Sinh 1 assertion cho hàm: {func_name}")

    # Kiểm tra API key
    api_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        log_error("GEMINI_API_KEY hoặc GOOGLE_API_KEY chưa được set trong environment")
        return []

    # Setup Gemini client (dùng setup_api() gốc của nl2postcond)
    api_cfg = _make_api_cfg()
    exper_cfg = _make_exper_cfg(model=model, temperature=temperature)
    try:
        _setup_api(api_cfg, log_debug)
    except Exception as e:
        log_error(f"nl2postcond setup_api() thất bại: {type(e).__name__}: {e}")
        return []

    # Tạo function stub (chỉ signature + docstring, bỏ implementation)
    # → has_reference_code = False: LLM chỉ thấy docstring, không thấy code
    stub = _make_docstring_stub(function_code, func_name)

    # Build prompt bằng template genOneNoRef["simple"] gốc nl2postcond
    try:
        prompt = _build_prompt_no_ref(stub, func_name)
    except Exception as e:
        log_error(f"Không build được prompt: {e}")
        return []

    log_debug(f"Prompt dài {len(prompt)} ký tự (has_reference_code=False)")
    log_debug(f"Prompt:\n{prompt}")

    # Gọi LLM MỘT LẦN DUY NHẤT (nl2postcond sinh 1 assertion mỗi lần)
    try:
        response = _ask(prompt, exper_cfg, log_debug)
    except Exception as e:
        log_warn(f"LLM call thất bại: {type(e).__name__}: {e}")
        return []

    if response is None:
        log_warn("LLM call trả về None")
        return []

    raw = response["choices"][0]["message"]["content"]
    log_debug(f"Raw response:\n{raw}")

    # Parse bằng extract_code() + code_sanitize() gốc nl2postcond
    assertion = _parse_one_assertion(raw)
    if assertion is None:
        log_warn(f"Không parse được assertion từ response cho {func_name}")
        return []

    log_info(f"Assertion sinh được cho {func_name}: {assertion}")
    return [assertion]


def wrap_assertion(
    function_code: str,
    func_name: str,
    assertion_code: str,
) -> str:
    """
    Wrap function code voi assertion theo pattern nl2postcond (AST-safe).

    Bug trong wrap_code_solution goc: dung string replace 'entry_point(' ->
    'entry_point_original(' nen bat ca method calls ben trong ham.
    Vi du: self._deserialize( -> self._deserialize_original( <- SAI

    Implementation nay dung AST:
    1. Chi doi ten FunctionDef node (khong dong den method calls ben trong)
    2. Giu nguyen **kwargs trong wrapper call
    3. Dung ast.unparse() de tao code chinh xac

    Pattern:
        def func_original(args, **kwargs):
            <original implementation NGUYEN VEN>

        def func(args, **kwargs):
            return_value = func_original(args, **kwargs)
            import re
            assert return_value ...
            return return_value
    """
    function_code = textwrap.dedent(function_code)
    try:
        tree = ast.parse(function_code)
    except SyntaxError as e:
        raise ValueError(f"Khong parse duoc function_code: {e}") from e

    # Tim FunctionDef node co ten == func_name
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            func_node = node
            break

    if func_node is None:
        raise ValueError(f"Khong tim thay ham {func_name!r} trong function_code")

    original_name = func_name + "_original"

    # Buoc 1: Doi ten FunctionDef bang AST — KHONG string replace
    # chi doi node.name, khong dong den method calls ben trong ham
    func_node.name = original_name
    ast.fix_missing_locations(tree)
    original_code = ast.unparse(tree)

    # Buoc 2: Build wrapper signature and the call into the renamed function.
    # ast.unparse(args) preserves defaults, kw-only markers, *args, and **kwargs.
    args = func_node.args

    normal_args = list(args.posonlyargs) + list(args.args)
    receiver = normal_args[0].arg if normal_args and normal_args[0].arg in {"self", "cls"} else None
    call_normal_args = normal_args[1:] if receiver else normal_args

    call_parts = [arg.arg for arg in call_normal_args]
    if args.vararg:
        call_parts.append("*" + args.vararg.arg)
    call_parts += [f"{arg.arg}={arg.arg}" for arg in args.kwonlyargs]
    if args.kwarg:
        call_parts.append("**" + args.kwarg.arg)

    sig_str = ast.unparse(args)
    call_str = ", ".join(call_parts)
    if receiver:
        callee = receiver + "." + original_name
    else:
        callee = original_name

    # Indent assertion cho wrapper body
    assertion_indented = "\n".join("    " + line for line in assertion_code.splitlines())

    decorator_lines = ""
    for decorator in func_node.decorator_list:
        decorator_lines += "@" + ast.unparse(decorator) + "\n"

    # Buoc 3: Build wrapper function
    wrapper = (
        "\n\n"
        + decorator_lines
        + "def " + func_name + "(" + sig_str + "):\n"
        "\n\n"
        "    return_value = " + callee + "(" + call_str + ")\n"
        "    \n"
        "    # Adding imports that might be useful for postconditions\n"
        "    import re\n"
        + assertion_indented + "\n"
        "\n"
        "    return return_value\n"
    )

    return original_code + wrapper

