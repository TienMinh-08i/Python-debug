from __future__ import annotations

import argparse
from pathlib import Path

from .io_utils import read_jsonl, write_llm_outputs_py


def main() -> None:
    parser = argparse.ArgumentParser(description="Export paper_eval samples.jsonl LLM outputs to a Python file.")
    parser.add_argument("--run-dir", type=Path, required=True, help="Run directory containing samples.jsonl.")
    parser.add_argument("--output", default="llm_outputs.py", help="Python filename to create inside --run-dir.")
    args = parser.parse_args()

    samples_path = args.run_dir / "samples.jsonl"
    samples = read_jsonl(samples_path)
    if not samples:
        raise SystemExit(f"No samples found in {samples_path}")

    output_path = write_llm_outputs_py(args.run_dir, samples, filename=args.output)
    print(output_path.resolve())


if __name__ == "__main__":
    main()
