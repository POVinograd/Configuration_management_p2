
"""CLI-инструмент для получения зависимостей пакетов Ubuntu APT."""

import argparse
import gzip
import lzma
import platform
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


allowed_extensions = {".png", ".svg", ".jpg", ".jpeg", ".pdf"}
package_pattern = r"[A-Za-z0-9][A-Za-z0-9._-]*"
url_schemes = {"http", "https"}
dependency_pattern = re.compile(r"^\s*([a-z0-9][a-z0-9+.-]*)")


class RepositoryError(Exception):
    """Ошибка загрузки или разбора репозитория."""


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
        help="URL репозитория, индекс Packages или путь к тестовому файлу",)
    parser.add_argument(
        "--test-repository",
        action="store_true",
        help="использовать локальный тестовый файл",)
    parser.add_argument(
        "--output",
        default="dependencies.png",
        type=output_file,
        help="имя выходного файла", )
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


def parse_control_data(data: str) -> list[dict[str, str]]:
    """Разбирает записи формата Debian control, используемого APT."""
    packages = []
    fields: dict[str, str] = {}
    last_field: str | None = None
    def save_record() -> None:
        if fields:
            packages.append(dict(fields))
            fields.clear()
    for line in data.splitlines():
        if not line.strip():
            save_record()
            last_field = None
            continue
        if line[0].isspace():
            if last_field is not None:
                fields[last_field] += " " + line.strip()
            continue
        if ":" not in line:
            raise RepositoryError(
                f"некорректная строка в метаданных: {line!r}"
            )
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise RepositoryError("обнаружено поле без имени")
        fields[key] = value.strip()
        last_field = key
    save_record()
    return packages


def extract_dependencies(package: dict[str, str]) -> list[str]:
    """Извлекает имена прямых Depends и Pre-Depends."""
    dependencies = []
    seen = set()
    for field in ("Pre-Depends", "Depends"):
        value = package.get(field, "")
        if not value:
            continue
        for alternative_group in value.split(","):
            for alternative in alternative_group.split("|"):
                match = dependency_pattern.match(alternative)
                if not match:
                    continue
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    dependencies.append(name)
    return dependencies


def decode_index(raw: bytes, source: str) -> str:
    """Распаковывает индекс APT в зависимости от расширения."""
    try:
        if source.endswith(".gz"):
            raw = gzip.decompress(raw)
        elif source.endswith(".xz"):
            raw = lzma.decompress(raw)
        return raw.decode("utf-8")
    except (OSError, EOFError, lzma.LZMAError, UnicodeDecodeError) as error:
        raise RepositoryError(
            f"не удалось прочитать индекс {source}: {error}"
        ) from error


def read_local_repository(path: str) -> str:
    """Читает локальный индекс пакетов."""
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as error:
        raise RepositoryError(
            f"не удалось прочитать файл репозитория: {error}"
        ) from error

    return decode_index(raw, file_path.name)


def fetch_url(url: str) -> str:
    """Загружает индекс пакетов по HTTP или HTTPS."""
    request = Request(
        url,
        headers={"User-Agent": "dependency-graph/1.0"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise RepositoryError(
            f"не удалось загрузить {url}: {error}"
        ) from error

    return decode_index(raw, urlparse(url).path)


def architecture_name() -> str:
    """Преобразует архитектуру Python в название архитектуры APT."""
    machine = platform.machine().lower()
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "i386": "i386",
        "i686": "i386",
        "armv7l": "armhf",
    }.get(machine, machine)


def ubuntu_codename() -> str | None:
    """Определяет кодовое имя Ubuntu из /etc/os-release, если оно доступно."""
    try:
        for line in Path("/etc/os-release").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("VERSION_CODENAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def index_urls(repository: str) -> list[str]:
    """Формирует возможные URL индекса Packages."""
    parsed = urlparse(repository)
    base = repository.rstrip("/")
    path = parsed.path.rstrip("/")
    filename = path.rsplit("/", 1)[-1]
    if filename in {"Packages", "Packages.gz", "Packages.xz"}:
        return [repository]
    if re.fullmatch(r"binary-[A-Za-z0-9_-]+", filename):
        return [
            f"{base}/Packages.xz",
            f"{base}/Packages.gz",
            f"{base}/Packages",]
    if re.fullmatch(r"(main|universe|restricted|multiverse)", filename):
        architecture = architecture_name()
        return [
            f"{base}/binary-{architecture}/Packages.xz",
            f"{base}/binary-{architecture}/Packages.gz",
            f"{base}/binary-{architecture}/Packages",
            f"{base}/binary-all/Packages.xz",
            f"{base}/binary-all/Packages.gz",
            f"{base}/binary-all/Packages",]
    suites = []
    detected = ubuntu_codename()
    for suite in (detected, "noble", "jammy", "focal"):
        if suite and suite not in suites:
            suites.append(suite)
    urls = []
    architecture = architecture_name()
    for suite in suites:
        for component in ("main", "universe", "restricted", "multiverse"):
            for arch in (architecture, "all"):
                directory = (
                    f"{base}/dists/{suite}/{component}/binary-{arch}"
                )
                urls.extend([
                        f"{directory}/Packages.xz",
                        f"{directory}/Packages.gz",
                        f"{directory}/Packages",])
    return urls


def find_package(data: str, requested_name: str) -> dict[str, str] | None:
    """Ищет пакет по точному имени в индексе."""
    for package in parse_control_data(data):
        if package.get("Package") == requested_name:
            return package
    return None


def get_package_dependencies(
    repository: str,
    requested_name: str,
    test_repository: bool = False,
) -> list[str]:
    """Получает прямые зависимости пакета из локального или сетевого индекса."""
    if test_repository:
        data = read_local_repository(repository)
        package = find_package(data, requested_name)
        if package is None:
            raise RepositoryError(
                f"пакет {requested_name!r} не найден в тестовом репозитории"
            )
        return extract_dependencies(package)
    urls = index_urls(repository)
    errors = []
    for url in urls:
        try:
            data = fetch_url(url)
            package = find_package(data, requested_name)
            if package is not None:
                return extract_dependencies(package)
        except RepositoryError as error:
            errors.append(str(error))

    if errors and len(errors) == len(urls):
        raise RepositoryError(
            "не удалось загрузить ни один индекс пакетов. "
            "Передайте URL файла Packages или каталога индекса. "
            f"Последняя ошибка: {errors[-1]}"
        )
    raise RepositoryError(
        f"пакет {requested_name!r} не найден в доступных индексах. "
        "Проверьте URL, выпуск Ubuntu, компонент и архитектуру."
    )


def main(argv: list[str] | None = None) -> int:
    """Проверяет параметры и выводит прямые зависимости пакета."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.test_repository:
        validate_test_file(parser, args.repository)
    print_options(args)
    try:
        dependencies = get_package_dependencies(
            args.repository,
            args.package,
            args.test_repository,
        )
    except RepositoryError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    print(f"\nПрямые зависимости пакета {args.package}:")
    if dependencies:
        for dependency in dependencies:
            print(dependency)
    else:
        print("Прямые зависимости отсутствуют.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
