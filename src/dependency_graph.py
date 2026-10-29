
"""Минимальный CLI-прототип визуализатора зависимостей пакетов."""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

allowed_extensions = {".png", ".svg", ".jpg", ".jpeg", ".pdf"}
package_pattern = r"[A-Za-z0-9][A-Za-z0-9._-]*"
url_schemes = {"http", "https"}


def package_name(value: str) -> str:
    """Проверяет имя анализируемого пакета."""
    if not re.fullmatch(package_pattern, value):
        raise argparse.ArgumentTypeError(
            "имя должно содержать латинские буквы, цифры, '.', '_' или '-'"
        )
    return value


def repository_value(value: str) -> str:
    """Проверяет URL или путь к репозиторию."""
    if not value.strip():
        raise argparse.ArgumentTypeError(
            "адрес репозитория не может быть пустым"
        )
    
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return value

    if "://" not in value:
        return value

    parsed = urlparse(value)
    if parsed.scheme not in url_schemes or not parsed.netloc:
        raise argparse.ArgumentTypeError(
            "URL должен начинаться с http:// или https://"
        )

    return value


def output_file(value: str) -> str:
    """Проверяет имя и расширение выходного файла."""
    path = Path(value)
    if not value.strip() or path.name in {"", ".", ".."}:
        raise argparse.ArgumentTypeError("укажите имя выходного файла")
    if path.suffix.lower() not in allowed_extensions:
        raise argparse.ArgumentTypeError(
            "разрешены расширения: .png, .svg, .jpg, .jpeg, .pdf"
        )
    if path.exists() and path.is_dir():
        raise argparse.ArgumentTypeError(
            "выходной путь указывает на каталог"
        )
    return value


def filter_value(value: str) -> str:
    """Проверяет подстроку фильтрации."""
    if not value:
        raise argparse.ArgumentTypeError(
            "подстрока фильтрации не может быть пустой"
        )
    return value


def validate_test_file(parser: argparse.ArgumentParser, value: str) -> None:
    """Проверяет существование и доступность тестового файла."""
    path = Path(value)
    if not path.exists():
        parser.error(f"тестовый файл не найден: {value}")
    if not path.is_file():
        parser.error(f"ожидался файл, получен другой путь: {value}")
    try:
        with path.open("rb"):
            pass
    except OSError as error:
        parser.error(f"невозможно прочитать тестовый файл: {error}")


def build_parser() -> argparse.ArgumentParser:
    """Создаёт парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Прототип визуализатора зависимостей пакетов.")
    parser.add_argument(
        "--package",
        required=True,
        type=package_name,
        help="имя анализируемого пакета",)
    parser.add_argument(
        "--repository",
        required=True,
        type=repository_value,
        help="URL репозитория или путь к тестовому файлу",)
    parser.add_argument(
        "--test-repository",
        action="store_true",
        help="использовать локальный тестовый файл",)
    parser.add_argument(
        "--output",
        default="dependencies.png",
        type=output_file,
        help="имя выходного файла",)
    parser.add_argument(
        "--ascii-tree",
        action="store_true",
        help="включить режим ASCII-дерева",)
    parser.add_argument(
        "--filter",
        dest="package_filter",
        type=filter_value,
        help="подстрока для фильтрации пакетов",)
    return parser


def print_options(args: argparse.Namespace) -> None:
    """Печатает все пользовательские параметры."""
    values = {
        "package": args.package,
        "repository": args.repository,
        "test_repository": args.test_repository,
        "output": args.output,
        "ascii_tree": args.ascii_tree,
        "filter": args.package_filter or "",
    }
    for key, value in values.items():
        print(f"{key}={value}")


def main(argv: list[str] | None = None) -> int:
    """Проверяет параметры и выводит их значения."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.test_repository:
        validate_test_file(parser, args.repository)
    print_options(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
