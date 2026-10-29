
"""Проверяет базовые соглашения оформления Python-кода."""

import ast
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source_dir = root / "src"
max_line_length = 80
max_function_lines = 40
max_arguments = 7


def check_file(path: Path) -> list[str]:
    """Возвращает список замечаний по одному файлу."""
    source = path.read_text(encoding="utf-8")
    issues = []
    for number, line in enumerate(source.splitlines(), start=1):
        if len(line) > max_line_length:
            issues.append(f"{path}:{number}: строка длиннее 80 символов")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        if end - node.lineno + 1 > max_function_lines:
            issues.append(f"{path}:{node.lineno}: функция слишком длинная")
        args = node.args
        count = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
        count += int(args.vararg is not None)
        count += int(args.kwarg is not None)
        if count > max_arguments:
            issues.append(f"{path}:{node.lineno}: слишком много аргументов")
        if ast.get_docstring(node) is None:
            issues.append(f"{path}:{node.lineno}: отсутствует docstring")
    return issues


def main() -> int:
    """Проверяет Python-файлы проекта."""
    files = sorted(source_dir.rglob("*.py"))
    issues = [item for path in files for item in check_file(path)]
    if issues:
        print("\n".join(issues))
        return 1
    print(f"OK: проверено файлов: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
