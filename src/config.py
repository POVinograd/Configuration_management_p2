"""Command-line configuration and argument validation."""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

SUPPORTED_SCHEMES = {"http", "https", "file"}
SUPPORTED_OUTPUT_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg", ".pdf"}
TRUE_VALUES = {"true", "1", "yes", "да"}
FALSE_VALUES = {"false", "0", "no", "нет"}


def parse_bool(value: str) -> bool:
    """Parse a boolean command-line value or raise an argparse error."""
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    message = f"ожидалось true/false, получено: {value!r}"
    raise argparse.ArgumentTypeError(message)


def validate_package(value: str) -> str:
    """Validate and normalize a package name."""
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError(
            "имя пакета не должно быть пустым"
        )
    if any(character.isspace() for character in value):
        raise argparse.ArgumentTypeError(
            "имя пакета не должно содержать "
            "пробелы"
        )
    return value


def validate_repo(value: str) -> str:
    """Validate a repository URL or an existing local directory."""
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError(
            "репозиторий не должен быть пустым"
        )

    parsed = urlparse(value)
    if parsed.scheme:
        return _validate_repo_url(value, parsed)

    path = Path(value).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(
            "путь к тестовому репозиторию "
            "не найден: "
            f"{path}"
        )
    if not path.is_dir():
        raise argparse.ArgumentTypeError(
            "путь к репозиторию должен "
            "быть каталогом: "
            f"{path}"
        )
    return str(path.resolve())


def _validate_repo_url(value: str, parsed) -> str:
    """Validate supported URL schemes and file URL targets."""
    if parsed.scheme not in SUPPORTED_SCHEMES:
        schemes = ", ".join(sorted(SUPPORTED_SCHEMES))
        raise argparse.ArgumentTypeError(
            "URL должен использовать схему "
            "из списка: "
            f"{schemes}"
        )
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise argparse.ArgumentTypeError(
            "у URL отсутствует имя хоста"
        )
    if parsed.scheme == "file":
        path = Path(parsed.path)
        if not path.exists():
            raise argparse.ArgumentTypeError(
                f"файл/каталог не найден: {path}"
            )
    return value


def validate_output(value: str) -> str:
    """Validate the output image path and supported file extension."""
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError(
            "имя выходного файла не должно быть "
            "пустым"
        )

    path = Path(value).expanduser()
    if path.exists() and path.is_dir():
        raise argparse.ArgumentTypeError(
            "путь выходного файла указывает "
            "на каталог"
        )
    if path.suffix.lower() not in SUPPORTED_OUTPUT_SUFFIXES:
        suffixes = ", ".join(sorted(SUPPORTED_OUTPUT_SUFFIXES))
        raise argparse.ArgumentTypeError(
            "поддерживаемые расширения файла: "
            f"{suffixes}"
        )
    if not path.parent.exists():
        raise argparse.ArgumentTypeError(
            "каталог для выходного файла "
            "не найден: "
            f"{path.parent}"
        )
    if not path.parent.is_dir():
        raise argparse.ArgumentTypeError(
            "родительский путь не является "
            "каталогом: "
            f"{path.parent}"
        )
    return str(path)


def validate_filter(value: str) -> str:
    """Validate a non-empty package-name filter substring."""
    if not value:
        raise argparse.ArgumentTypeError(
            "подстрока фильтра не должна быть "
            "пустой"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser and register all user options."""
    parser = argparse.ArgumentParser(
        prog="dependency-visualizer",
        description=(
            "Минимальный прототип "
            "визуализатора "
            "зависимостей (этап 1)."
        ),
    )
    parser.add_argument(
        "--package", required=True, type=validate_package,
        help="имя анализируемого пакета",
    )
    parser.add_argument(
        "--repo", required=True, type=validate_repo,
        help=("URL репозитория или путь "
              "к тестовому каталогу"),
    )
    parser.add_argument(
        "--test-mode", type=parse_bool, default=False, metavar="BOOL",
        help=("режим тестового репозитория: "
              "true/false (по умолчанию false)"),
    )
    parser.add_argument(
        "--output", type=validate_output, default="graph.png",
        help=("имя файла изображения "
              "(по умолчанию graph.png)"),
    )
    parser.add_argument(
        "--ascii", action="store_true",
        help=("включить вывод зависимостей "
              "в виде ASCII-дерева"),
    )
    parser.add_argument(
        "--filter", type=validate_filter, default=None,
        help=("оставить пакеты, имя которых "
              "содержит подстроку"),
    )
    return parser
