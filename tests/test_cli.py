
"""Сценарные тесты CLI."""

import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = root / "src" / "dependency_graph.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Запускает CLI с указанными аргументами."""
    return subprocess.run(
        [sys.executable, str(app), *args],
        capture_output=True,
        text=True,
        cwd=root,
    )


def check(condition: bool, message: str) -> None:
    """Завершает тест с ошибкой при невыполнении условия."""
    if not condition:
        raise AssertionError(message)


def test_valid_run(directory: Path) -> None:
    """Проверяет вывод всех параметров."""
    repository = directory / "repo.json"
    repository.write_text('{"packages": []}', encoding="utf-8")
    output = directory / "graph.svg"
    result = run_cli(
        "--package", "demo_pkg",
        "--repository", str(repository),
        "--test-repository",
        "--output", str(output),
        "--ascii-tree",
        "--filter", "demo",
    )
    check(result.returncode == 0, result.stderr)
    expected = [
        "package=demo_pkg",
        f"repository={repository}",
        "test_repository=True",
        f"output={output}",
        "ascii_tree=True",
        "filter=demo",
    ]
    for line in expected:
        check(line in result.stdout, f"нет строки: {line}")


def test_missing_package() -> None:
    """Проверяет отсутствие обязательного имени пакета."""
    result = run_cli("--repository", "https://example.com")
    check(result.returncode != 0, "ожидалась ошибка --package")


def test_invalid_package() -> None:
    """Проверяет недопустимые символы в имени пакета."""
    result = run_cli(
        "--package", "bad name",
        "--repository", "https://example.com",
    )
    check(result.returncode != 0, "ожидалась ошибка имени пакета")


def test_invalid_url() -> None:
    """Проверяет недопустимую схему URL."""
    result = run_cli(
        "--package", "demo",
        "--repository", "ftp://example.com",
    )
    check(result.returncode != 0, "ожидалась ошибка URL")


def test_missing_file(directory: Path) -> None:
    """Проверяет отсутствие тестового файла."""
    missing = directory / "missing.json"
    result = run_cli(
        "--package", "demo",
        "--repository", str(missing),
        "--test-repository",
    )
    check(result.returncode != 0, "ожидалась ошибка файла")
    check("не найден" in result.stderr, "неверное сообщение об ошибке")


def test_invalid_output() -> None:
    """Проверяет недопустимое расширение выходного файла."""
    result = run_cli(
        "--package", "demo",
        "--repository", "https://example.com",
        "--output", "graph.exe",
    )
    check(result.returncode != 0, "ожидалась ошибка расширения")


def test_empty_filter() -> None:
    """Проверяет пустую подстроку фильтра."""
    result = run_cli(
        "--package", "demo",
        "--repository", "https://example.com",
        "--filter", "",
    )
    check(result.returncode != 0, "ожидалась ошибка фильтра")


def main() -> int:
    """Выполняет все сценарии тестирования."""
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        test_valid_run(directory)
        test_missing_file(directory)
    test_missing_package()
    test_invalid_package()
    test_invalid_url()
    test_invalid_output()
    test_empty_filter()
    print("OK: все сценарии пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
