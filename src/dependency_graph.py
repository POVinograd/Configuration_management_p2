"""CLI-инструмент для построения графа зависимостей Ubuntu APT."""
import argparse
import gzip
import json
import lzma
import platform
import re
import sys
from collections import deque
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


package_pattern = r"[A-Za-z0-9][A-Za-z0-9._-]*"
graph_package_pattern = re.compile(r"^[A-Z]+$")
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
    if re.match(r"^[A-Za-z]:[\\/]", value) or "://" not in value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in url_schemes or not parsed.netloc:
        raise argparse.ArgumentTypeError(
            "URL должен начинаться с http:// или https://"
        )
    return value


def filter_value(value: str) -> str:
    """Проверяет подстроку для фильтрации пакетов."""
    if not value:
        raise argparse.ArgumentTypeError(
            "подстрока фильтрации не может быть пустой"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    """Создаёт парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Построение графа зависимостей пакетов."
    )
    parser.add_argument(
        "--package", required=True, type=package_name,
        help="имя анализируемого пакета",
    )
    parser.add_argument(
        "--repository", required=True, type=repository_value,
        help="URL репозитория или путь к локальному файлу",
    )
    parser.add_argument(
        "--test-repository", action="store_true",
        help="использовать локальный тестовый файл",
    )
    parser.add_argument(
        "--filter", dest="package_filter", type=filter_value,
        help="исключить пакеты, содержащие указанную подстроку",
    )
    return parser


def print_options(args: argparse.Namespace) -> None:
    """Печатает параметры запуска программы."""
    print(f"package={args.package}")
    print(f"repository={args.repository}")
    print(f"test_repository={args.test_repository}")
    print(f"filter={args.package_filter or ''}")


def parse_control_data(data: str) -> list[dict[str, str]]:
    """Разбирает записи Debian control из индекса APT."""
    packages = []
    fields: dict[str, str] = {}
    last_field = None

    def save_record() -> None:
        """Сохраняет текущую запись и очищает поля."""
        if fields:
            packages.append(dict(fields))
            fields.clear()

    for line in data.splitlines():
        if not line.strip():
            save_record()
            last_field = None
        elif line[0].isspace():
            if last_field:
                fields[last_field] += " " + line.strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            last_field = key.strip()
            fields[last_field] = value.strip()
        else:
            raise RepositoryError(
                f"некорректная строка в метаданных: {line!r}"
            )
    save_record()
    return packages


def extract_dependencies(package: dict[str, str]) -> list[str]:
    """Извлекает имена прямых зависимостей Debian-пакета."""
    dependencies = []
    for field in ("Pre-Depends", "Depends"):
        for group in package.get(field, "").split(","):
            for alternative in group.split("|"):
                match = dependency_pattern.match(alternative)
                if match and match.group(1) not in dependencies:
                    dependencies.append(match.group(1))
    return dependencies


def decode_index(raw: bytes, source: str) -> str:
    """Распаковывает индекс APT с учётом расширения файла."""
    try:
        if source.endswith(".gz"):
            raw = gzip.decompress(raw)
        elif source.endswith(".xz"):
            raw = lzma.decompress(raw)
        return raw.decode("utf-8")
    except (OSError, EOFError, lzma.LZMAError, UnicodeDecodeError) as exc:
        raise RepositoryError(
            f"не удалось прочитать индекс {source}: {exc}"
        ) from exc


def read_local_repository(path: str) -> str:
    """Читает локальный индекс пакетов APT."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RepositoryError(
            f"не удалось прочитать файл репозитория: {exc}"
        ) from exc
    return decode_index(raw, Path(path).name)


def fetch_url(url: str) -> str:
    """Загружает индекс пакетов по HTTP или HTTPS."""
    request = Request(url, headers={"User-Agent": "dependency-graph/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RepositoryError(f"не удалось загрузить {url}: {exc}") from exc
    return decode_index(raw, urlparse(url).path)


def architecture_name() -> str:
    """Возвращает архитектуру системы в формате APT."""
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
    """Определяет кодовое имя Ubuntu из системных метаданных."""
    try:
        lines = Path("/etc/os-release").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def index_urls(repository: str) -> list[str]:
    """Формирует возможные URL индекса Packages."""
    parsed = urlparse(repository)
    base = repository.rstrip("/")
    filename = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if filename in {"Packages", "Packages.gz", "Packages.xz"}:
        return [repository]
    if re.fullmatch(r"binary-[A-Za-z0-9_-]+", filename):
        return [
            f"{base}/Packages.xz",
            f"{base}/Packages.gz",
            f"{base}/Packages",
        ]
    suites = []
    for suite in (ubuntu_codename(), "noble", "jammy", "focal"):
        if suite and suite not in suites:
            suites.append(suite)
    urls = []
    architecture = architecture_name()
    for suite in suites:
        for component in ("main", "universe", "restricted", "multiverse"):
            for arch in (architecture, "all"):
                folder = (
                    f"{base}/dists/{suite}/{component}/binary-{arch}"
                )
                urls.extend([
                    f"{folder}/Packages.xz",
                    f"{folder}/Packages.gz",
                    f"{folder}/Packages",
                ])
    return urls


def find_package(
    data: str,
    requested_name: str,
) -> dict[str, str] | None:
    """Находит пакет по имени или по виртуальной зависимости Provides."""
    records = parse_control_data(data)

    for package in records:
        if package.get("Package") == requested_name:
            return package

    for package in records:
        provided = package.get("Provides", "")
        for item in provided.split(","):
            match = dependency_pattern.match(item)
            if match and match.group(1) == requested_name:
                return package

    return None


def get_package_dependencies(
    repository: str,
    requested_name: str,
    test_repository: bool = False,
) -> list[str]:
    """Получает прямые зависимости пакета из APT."""
    if test_repository:
        data = read_local_repository(repository)
        package = find_package(data, requested_name)
        if package is None:
            raise RepositoryError(
                f"пакет {requested_name!r} не найден в тестовом репозитории"
            )
        return extract_dependencies(package)
    errors = []
    urls = index_urls(repository)
    for url in urls:
        try:
            package = find_package(fetch_url(url), requested_name)
            if package is not None:
                return extract_dependencies(package)
        except RepositoryError as exc:
            errors.append(str(exc))
    if errors and len(errors) == len(urls):
        raise RepositoryError(
            "не удалось загрузить индексы Packages. "
            f"Последняя ошибка: {errors[-1]}"
        )
    raise RepositoryError(
        f"пакет {requested_name!r} не найден в доступных индексах"
    )


def parse_graph_file(path: str) -> dict[str, list[str]]:
    """Читает JSON-граф с именами пакетов из больших латинских букв."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryError(f"не удалось прочитать JSON-граф: {exc}") from exc
    if not isinstance(data, dict):
        raise RepositoryError("граф должен быть JSON-объектом")
    graph = {}
    for name, dependencies in data.items():
        if (
            not isinstance(name, str)
            or not graph_package_pattern.fullmatch(name)
        ):
            raise RepositoryError(
                f"недопустимое имя пакета: {name!r}"
            )
            raise RepositoryError(f"недопустимое имя пакета: {name!r}")
        if not isinstance(dependencies, list):
            raise RepositoryError(f"зависимости {name} должны быть списком")
        for dependency in dependencies:
            if (
                not isinstance(dependency, str)
                or not graph_package_pattern.fullmatch(dependency)
            ):
                raise RepositoryError(
                    f"некорректная зависимость пакета {name}"
                )
        graph[name] = list(dict.fromkeys(dependencies))
    return graph


def records_to_graph(records: list[dict[str, str]]) -> dict[str, list[str]]:
    """Преобразует записи APT в словарь зависимостей."""
    graph = {}
    for package in records:
        name = package.get("Package")
        if name:
            graph[name] = extract_dependencies(package)
    return graph


def load_test_graph(path: str) -> dict[str, list[str]]:
    """Загружает JSON-граф или локальный индекс Ubuntu Packages."""
    try:
        content = Path(path).read_text(encoding="utf-8").lstrip()
    except OSError as exc:
        raise RepositoryError(
            f"тестовый файл не найден или недоступен: {exc}"
        ) from exc
    if content.startswith("{"):
        return parse_graph_file(path)
    return records_to_graph(parse_control_data(read_local_repository(path)))


def build_dependency_graph(
    root: str,
    dependency_provider,
    package_filter: str = "",
) -> dict[str, list[str]]:
    """Строит граф BFS с рекурсивной обработкой очереди."""
    if package_filter and package_filter in root:
        raise RepositoryError(
            f"корневой пакет {root!r} исключён фильтром {package_filter!r}"
        )
    graph = {root: []}
    visited = {root}
    queue = deque([root])

    def process_queue() -> None:
        """Обрабатывает очередной узел и рекурсивно продолжает BFS."""
        if not queue:
            return
        current = queue.popleft()
        for dependency in dependency_provider(current):
            if package_filter and package_filter in dependency:
                continue
            graph[current].append(dependency)
            if dependency not in visited:
                visited.add(dependency)
                graph.setdefault(dependency, [])
                queue.append(dependency)
        process_queue()

    process_queue()
    return graph


def print_graph(graph: dict[str, list[str]]) -> None:
    """Выводит узлы и рёбра графа в текстовом виде."""
    print("\nГраф зависимостей:")
    for package, dependencies in graph.items():
        if dependencies:
            print(f"{package} -> {', '.join(dependencies)}")
        else:
            print(f"{package} -> (нет зависимостей)")
    print(f"\nКоличество узлов: {len(graph)}")
    print(f"Количество рёбер: {sum(map(len, graph.values()))}")


def main(argv: list[str] | None = None) -> int:
    """Разбирает параметры, строит граф и выводит результат."""
    parser = build_parser()
    args = parser.parse_args(argv)
    print_options(args)
    try:
        if args.test_repository:
            graph_data = load_test_graph(args.repository)
            if args.package not in graph_data:
                raise RepositoryError(
                    f"пакет {args.package!r} не найден в тестовом файле"
                )
            provider = lambda name: graph_data.get(name, [])
        else:
            get_package_dependencies(
                args.repository, args.package, False
            )
            provider = lambda name: get_package_dependencies(
                args.repository, name, False
            )
        graph = build_dependency_graph(
            args.package, provider, args.package_filter or ""
        )
        print_graph(graph)
    except RepositoryError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
