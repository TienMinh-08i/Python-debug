import ast
import json
import copy

INPUT_FILE = "tasks.json"
OUTPUT_FILE = "mutants.jsonl"


class Mutator(ast.NodeTransformer):
    def __init__(self):
        self.mutants = []

    def mutate_binop(self, node):
        ops = {
            ast.Add: ast.Sub,
            ast.Sub: ast.Add,
            ast.Mult: ast.Div,
            ast.Div: ast.Mult,
        }

        for old_op, new_op in ops.items():
            if isinstance(node.op, old_op):
                new_node = copy.deepcopy(node)
                new_node.op = new_op()
                self.mutants.append(new_node)

    def mutate_compare(self, node):
        cmp_ops = {
            ast.Gt: ast.GtE,
            ast.Lt: ast.LtE,
            ast.Eq: ast.NotEq,
            ast.NotEq: ast.Eq,
        }

        for i, op in enumerate(node.ops):
            for old_op, new_op in cmp_ops.items():
                if isinstance(op, old_op):
                    new_node = copy.deepcopy(node)
                    new_node.ops[i] = new_op()
                    self.mutants.append(new_node)

    def visit_BinOp(self, node):
        self.mutate_binop(node)
        return self.generic_visit(node)

    def visit_Compare(self, node):
        self.mutate_compare(node)
        return self.generic_visit(node)


def generate_mutants(code):
    tree = ast.parse(code)
    mutator = Mutator()
    mutator.visit(tree)

    mutants_code = []

    for mutated_node in mutator.mutants:
        new_tree = copy.deepcopy(tree)

        class ReplaceNode(ast.NodeTransformer):
            def visit(self, node):
                if type(node) == type(mutated_node):
                    return mutated_node
                return self.generic_visit(node)

        new_tree = ReplaceNode().visit(new_tree)
        ast.fix_missing_locations(new_tree)

        try:
            mutants_code.append(ast.unparse(new_tree))
        except:
            pass

    return mutants_code


def main():
    with open(INPUT_FILE) as f:
        tasks = json.load(f)

    out = open(OUTPUT_FILE, "w")

    for task_id, task in tasks.items():
        sol = task["canonical_solution"]
        full_code = task["prompt"] + sol

        try:
            mutants = generate_mutants(full_code)
        except SyntaxError as exc:
            print(f"Skipping {task_id}: failed to parse generated source ({exc})")
            continue

        for i, m in enumerate(mutants):
            record = {
                "task_id": task_id,
                "mutant_id": f"{task_id}_m{i}",
                "mutant": m
            }
            out.write(json.dumps(record) + "\n")

    out.close()
    print("✅ AST mutants generated")


if __name__ == "__main__":
    main()