
"""Сценарные тесты CLI без pytest."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


root = Path(__file__).resolve().parents[1]
app = root / "src" / "dependency_graph.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Запускает CLI и возвращает результат выполнения."""
    return subprocess.run(
        [sys.executable, str(app), *map(str, args)],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )


def check(condition: bool, message: str) -> None:
    """Вызывает ошибку, если проверяемое условие ложно."""
    if not condition:
        raise AssertionError(message)


def write_graph(directory: Path, graph: dict) -> Path:
    """Сохраняет тестовый граф в формате JSON."""
    path = directory / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


def test_transitive_dependencies(directory: Path) -> None:
    """Проверяет поиск прямых и транзитивных зависимостей."""
    source = write_graph(
        directory,
        {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []},
    )
    result = run_cli(
        "--package", "A", "--repository", str(source),
        "--test-repository",
    )
    check(result.returncode == 0, result.stderr)
    for edge in ("A -> B, C", "B -> D", "C -> D"):
        check(edge in result.stdout, f"не найдено ребро: {edge}")
    check("Количество узлов: 4" in result.stdout, result.stdout)


def test_cycle(directory: Path) -> None:
    """Проверяет завершение обхода при циклических зависимостях."""
    source = write_graph(
        directory, {"A": ["B"], "B": ["C"], "C": ["A"]}
    )
    result = run_cli(
        "--package", "A", "--repository", str(source),
        "--test-repository",
    )
    check(result.returncode == 0, result.stderr)
    check("Количество узлов: 3" in result.stdout, result.stdout)
    check("C -> A" in result.stdout, "ребро цикла потеряно")


def test_filter(directory: Path) -> None:
    """Проверяет исключение пакетов по подстроке имени."""
    source = write_graph(
        directory,
        {
            "A": ["B", "LIB"],
            "B": ["C"],
            "C": [],
            "LIB": [],
        },)
    result = run_cli(
        "--package", "A",
        "--repository", str(source),
        "--test-repository",
        "--filter", "LIB",
    )
    check(result.returncode == 0, result.stderr)
    graph_output = result.stdout.split(
        "Граф зависимостей:", 1
    )[-1]
    check(
        "LIB" not in graph_output,
        "пакет LIB не исключён",
    )
    check(
        "B -> C" in graph_output,
        "остальные зависимости потеряны",
    )
    check(
        "Количество узлов: 3" in graph_output,
        graph_output,
    )


def test_root_filtered(directory: Path) -> None:
    """Проверяет ошибку, если фильтр исключает корневой пакет."""
    source = write_graph(directory, {"ABC": []})
    result = run_cli(
        "--package", "ABC", "--repository", str(source),
        "--test-repository", "--filter", "B",
    )
    check(result.returncode == 1, result.stdout)
    check("исключён фильтром" in result.stderr, result.stderr)


def test_package_not_found(directory: Path) -> None:
    """Проверяет ошибку для отсутствующего пакета."""
    source = write_graph(directory, {"A": []})
    result = run_cli(
        "--package", "Z", "--repository", str(source),
        "--test-repository",
    )
    check(result.returncode == 1, result.stdout)
    check("не найден" in result.stderr, result.stderr)


def test_invalid_graph(directory: Path) -> None:
    """Проверяет запрет имён пакетов в нижнем регистре в JSON."""
    source = write_graph(directory, {"lowercase": []})
    result = run_cli(
        "--package", "A", "--repository", str(source),
        "--test-repository",
    )
    check(result.returncode == 1, result.stdout)
    check("недопустимое имя" in result.stderr, result.stderr)


def test_missing_file(directory: Path) -> None:
    """Проверяет ошибку при отсутствии локального файла."""
    source = directory / "missing.json"
    result = run_cli(
        "--package", "A", "--repository", str(source),
        "--test-repository",
    )
    check(result.returncode == 1, result.stdout)
    check("не найден" in result.stderr, result.stderr)


def test_local_apt_index(directory: Path) -> None:
    """Проверяет чтение локального индекса Ubuntu Packages."""
    source = directory / "Packages"
    source.write_text(
        "Package: demo\n"
        "Depends: libc6 (>= 2.35), libssl3 | libssl1.1, zlib1g\n\n"
        "Package: libc6\n\n"
        "Package: libssl3\n\n"
        "Package: libssl1.1\n\n"
        "Package: zlib1g\n\n",
        encoding="utf-8",
    )
    result = run_cli(
        "--package", "demo", "--repository", str(source),
        "--test-repository",
    )
    check(result.returncode == 0, result.stderr)
    check("demo -> libc6, libssl3, libssl1.1, zlib1g" in result.stdout,
          result.stdout)
    check("Количество узлов: 5" in result.stdout, result.stdout)


def test_package_without_dependencies(directory: Path) -> None:
    """Проверяет пакет без зависимостей."""
    source = write_graph(directory, {"A": []})
    result = run_cli(
        "--package", "A", "--repository", str(source),
        "--test-repository",
    )
    check(result.returncode == 0, result.stderr)
    check("A -> (нет зависимостей)" in result.stdout, result.stdout)
    check("Количество узлов: 1" in result.stdout, result.stdout)


def test_invalid_package() -> None:
    """Проверяет отклонение имени пакета с пробелом."""
    result = run_cli(
        "--package", "bad name", "--repository", "https://example.com",
    )
    check(result.returncode != 0, "недопустимое имя было принято")


def test_invalid_url() -> None:
    """Проверяет отклонение URL с неподдерживаемой схемой."""
    result = run_cli(
        "--package", "demo", "--repository", "ftp://example.com",
    )
    check(result.returncode != 0, "URL FTP был принят")


def test_empty_filter() -> None:
    """Проверяет отклонение пустого фильтра."""
    result = run_cli(
        "--package", "demo", "--repository", "https://example.com",
        "--filter", "",
    )
    check(result.returncode != 0, "пустой фильтр был принят")


def main() -> int:
    """Выполняет все сценарии тестирования."""
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        test_transitive_dependencies(directory)
        test_cycle(directory)
        test_filter(directory)
        test_root_filtered(directory)
        test_package_not_found(directory)
        test_invalid_graph(directory)
        test_missing_file(directory)
        test_local_apt_index(directory)
        test_package_without_dependencies(directory)
    test_invalid_package()
    test_invalid_url()
    test_empty_filter()
    print("OK: все сценарии пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
