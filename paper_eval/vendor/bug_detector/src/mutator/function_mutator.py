"""Function-level Python mutants for assertion evaluation.

These mutants are intentionally generated from a single function source. This
matches the experiment goal: assertions generated from a function docstring are
evaluated against bugs injected into that same function, not unrelated code in
the containing file.
"""

from __future__ import annotations

import ast
import copy
import difflib
import textwrap
from dataclasses import dataclass


@dataclass(frozen=True)
class FunctionMutant:
    id: str
    function_source: str
    description: str
    diff: str


def generate_function_mutants(
    function_source: str,
    max_mutants: int,
) -> list[FunctionMutant]:
    """Generate up to max_mutants single-site AST mutants for one function."""
    original = textwrap.dedent(function_source).strip("\n") + "\n"
    tree = ast.parse(original)
    _mark_docstring_constants(tree)

    candidates = _collect_candidates(tree)
    mutants: list[FunctionMutant] = []
    seen_sources = {original.strip()}

    for candidate_index, variant_index, description in candidates:
        if len(mutants) >= max_mutants:
            break

        mutated_tree = copy.deepcopy(tree)
        transformer = _SingleMutationTransformer(candidate_index, variant_index)
        mutated_tree = transformer.visit(mutated_tree)
        ast.fix_missing_locations(mutated_tree)

        mutated_source = ast.unparse(mutated_tree).strip("\n") + "\n"
        if mutated_source.strip() in seen_sources:
            continue

        try:
            ast.parse(mutated_source)
        except SyntaxError:
            continue

        seen_sources.add(mutated_source.strip())
        mutant_id = f"func-{len(mutants) + 1}"
        mutants.append(
            FunctionMutant(
                id=mutant_id,
                function_source=mutated_source,
                description=description,
                diff=_make_diff(original, mutated_source),
            )
        )

    return mutants


def _mark_docstring_constants(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            setattr(first.value, "_skip_mutation", True)


def _collect_candidates(tree: ast.AST) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str]] = []
    candidate_index = 0
    for node in _iter_nodes_preorder(tree):
        variants = _variants_for_node(node)
        for variant_index, description, _ in variants:
            candidates.append((candidate_index, variant_index, description))
        if variants:
            candidate_index += 1
    return candidates


def _iter_nodes_preorder(node: ast.AST):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _iter_nodes_preorder(child)


class _SingleMutationTransformer(ast.NodeTransformer):
    def __init__(self, target_index: int, target_variant: int):
        self.target_index = target_index
        self.target_variant = target_variant
        self.current_index = 0
        self.done = False

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if self.done:
            return super().generic_visit(node)

        variants = _variants_for_node(node)
        if variants:
            if self.current_index == self.target_index:
                _, _, mutated = variants[self.target_variant]
                self.done = True
                return ast.copy_location(mutated, node)
            self.current_index += 1

        return super().generic_visit(node)


def _variants_for_node(node: ast.AST) -> list[tuple[int, str, ast.AST]]:
    if getattr(node, "_skip_mutation", False):
        return []

    variants: list[tuple[str, ast.AST]] = []

    if isinstance(node, ast.Compare):
        op_map: dict[type[ast.cmpop], ast.cmpop] = {
            ast.Eq: ast.NotEq(),
            ast.NotEq: ast.Eq(),
            ast.Lt: ast.GtE(),
            ast.LtE: ast.Gt(),
            ast.Gt: ast.LtE(),
            ast.GtE: ast.Lt(),
            ast.Is: ast.IsNot(),
            ast.IsNot: ast.Is(),
            ast.In: ast.NotIn(),
            ast.NotIn: ast.In(),
        }
        for i, op in enumerate(node.ops):
            replacement = op_map.get(type(op))
            if replacement is None:
                continue
            mutated = copy.deepcopy(node)
            mutated.ops[i] = replacement
            variants.append((f"compare op {type(op).__name__} -> {type(replacement).__name__}", mutated))

    elif isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            mutated = copy.deepcopy(node)
            mutated.op = ast.Or()
            variants.append(("boolean and -> or", mutated))
        elif isinstance(node.op, ast.Or):
            mutated = copy.deepcopy(node)
            mutated.op = ast.And()
            variants.append(("boolean or -> and", mutated))

    elif isinstance(node, ast.BinOp):
        op_map: dict[type[ast.operator], ast.operator] = {
            ast.Add: ast.Sub(),
            ast.Sub: ast.Add(),
            ast.Mult: ast.Div(),
            ast.Div: ast.Mult(),
            ast.FloorDiv: ast.Mult(),
            ast.Mod: ast.Mult(),
        }
        replacement = op_map.get(type(node.op))
        if replacement is not None:
            mutated = copy.deepcopy(node)
            mutated.op = replacement
            variants.append((f"binary op {type(node.op).__name__} -> {type(replacement).__name__}", mutated))

    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        variants.append(("remove not", copy.deepcopy(node.operand)))

    elif isinstance(node, ast.If):
        mutated = copy.deepcopy(node)
        mutated.test = ast.UnaryOp(op=ast.Not(), operand=mutated.test)
        variants.append(("negate if condition", mutated))

    elif isinstance(node, ast.IfExp):
        mutated = copy.deepcopy(node)
        mutated.test = ast.UnaryOp(op=ast.Not(), operand=mutated.test)
        variants.append(("negate conditional expression", mutated))

    elif isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            variants.append((f"boolean {value} -> {not value}", ast.Constant(value=not value)))
        elif value is None:
            variants.append(("None -> False", ast.Constant(value=False)))
        elif isinstance(value, int) and not isinstance(value, bool):
            variants.append((f"integer {value} -> {value + 1}", ast.Constant(value=value + 1)))
            variants.append((f"integer {value} -> {value - 1}", ast.Constant(value=value - 1)))
        elif isinstance(value, float):
            variants.append((f"float {value} -> {value + 1.0}", ast.Constant(value=value + 1.0)))
        elif isinstance(value, str):
            variants.append(("string -> empty string", ast.Constant(value="")))

    return [(i, description, mutated) for i, (description, mutated) in enumerate(variants)]


def _make_diff(original: str, mutated: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            mutated.splitlines(keepends=True),
            fromfile="original_function",
            tofile="mutated_function",
        )
    )
