"""CLI-инструмент для построения графа зависимостей Ubuntu APT."""
import argparse
import gzip
import json
import lzma
import os
import platform
import shlex
import shutil
import subprocess
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
    if not re.fullmatch(package_pattern, value):
        raise argparse.ArgumentTypeError(
            "имя должно содержать латинские буквы, цифры, '.', '_' или '-'"
        )
    return value


def repository_value(value: str) -> str:
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
    if not value:
        raise argparse.ArgumentTypeError(
            "подстрока фильтрации не может быть пустой"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument(
        "--installation-order", action="store_true",
        help="вывести порядок установки зависимостей (сначала зависимости)",
    )
    parser.add_argument(
        "--image-file", default="dependency_graph.png",
        help="имя PNG-файла с изображением графа",
    )
    parser.add_argument(
        "--ascii-tree", action="store_true",
        help="вывести зависимости в виде ASCII-дерева",
    )
    return parser


def print_options(args: argparse.Namespace) -> None:
    print(f"package={args.package}")
    print(f"repository={args.repository}")
    print(f"test_repository={args.test_repository}")
    print(f"filter={args.package_filter or ''}")
    print(f"installation_order={args.installation_order}")
    print(f"image_file={args.image_file}")
    print(f"ascii_tree={args.ascii_tree}")


def parse_control_data(data: str) -> list[dict[str, str]]:
    packages = []
    fields: dict[str, str] = {}
    last_field = None

    def save_record() -> None:
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
    dependencies = []
    for field in ("Pre-Depends", "Depends"):
        for group in package.get(field, "").split(","):
            for alternative in group.split("|"):
                match = dependency_pattern.match(alternative)
                if match and match.group(1) not in dependencies:
                    dependencies.append(match.group(1))
    return dependencies


def decode_index(raw: bytes, source: str) -> str:
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
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RepositoryError(
            f"не удалось прочитать файл репозитория: {exc}"
        ) from exc
    return decode_index(raw, Path(path).name)


def fetch_url(url: str) -> str:
    request = Request(url, headers={"User-Agent": "dependency-graph/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RepositoryError(f"не удалось загрузить {url}: {exc}") from exc
    return decode_index(raw, urlparse(url).path)


def architecture_name() -> str:
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


def make_remote_dependency_provider(repository: str):
    """Загружает индекс APT один раз и переиспользует его для всех узлов."""
    package_records: dict[str, dict[str, str]] | None = None
    provided_records: dict[str, dict[str, str]] | None = None

    def load_index() -> None:
        nonlocal package_records, provided_records
        if package_records is not None:
            return

        errors = []
        for url in index_urls(repository):
            try:
                print(f"Загрузка индекса APT: {url}", flush=True)
                records = parse_control_data(fetch_url(url))
                package_records = {}
                provided_records = {}
                for record in records:
                    name = record.get("Package")
                    if name:
                        package_records[name] = record
                    for item in record.get("Provides", "").split(","):
                        match = dependency_pattern.match(item)
                        if match:
                            provided_records.setdefault(match.group(1), record)
                print(
                    f"Индекс загружен: {len(package_records)} пакетов. "
                    "Повторная загрузка для зависимостей не потребуется.",
                    flush=True,
                )
                return
            except RepositoryError as exc:
                errors.append(str(exc))

        detail = errors[-1] if errors else "нет доступных URL индекса"
        raise RepositoryError(f"не удалось загрузить индекс APT: {detail}")

    def provider(requested_name: str) -> list[str]:
        load_index()
        assert package_records is not None
        assert provided_records is not None
        record = package_records.get(requested_name)
        if record is None:
            record = provided_records.get(requested_name)
        if record is None:
            raise RepositoryError(
                f"пакет {requested_name!r} не найден в загруженном индексе"
            )
        return extract_dependencies(record)

    return provider


def get_package_dependencies(
    repository: str,
    requested_name: str,
    test_repository: bool = False,
) -> list[str]:
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
    graph = {}
    for package in records:
        name = package.get("Package")
        if name:
            graph[name] = extract_dependencies(package)
    return graph


def load_test_graph(path: str) -> dict[str, list[str]]:
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


def installation_order(
    graph: dict[str, list[str]],
) -> tuple[list[str], list[tuple[str, str]]]:
    state: dict[str, int] = {}  
    order: list[str] = []
    cycle_edges: list[tuple[str, str]] = []

    def visit(package: str) -> None:
        status = state.get(package, 0)
        if status == 2:
            return
        if status == 1:
            return

        state[package] = 1
        for dependency in graph.get(package, []):
            dependency_status = state.get(dependency, 0)
            if dependency_status == 1:
                cycle_edges.append((package, dependency))
            elif dependency_status == 0:
                visit(dependency)

        state[package] = 2
        order.append(package)

    for package in graph:
        if state.get(package, 0) == 0:
            visit(package)
    for dependencies in graph.values():
        for dependency in dependencies:
            if state.get(dependency, 0) == 0:
                visit(dependency)

    return order, cycle_edges


def print_installation_order(graph: dict[str, list[str]]) -> None:
    order, cycle_edges = installation_order(graph)
    print("\nПорядок установки (сначала зависимости):")
    for number, package in enumerate(order, start=1):
        print(f"{number}. {package}")

    if cycle_edges:
        print("\nПредупреждение: обнаружены циклические зависимости:")
        for package, dependency in cycle_edges:
            print(f"{package} -> {dependency}")
        print(
            "Для пакетов внутри цикла строгий порядок установки "
            "определить невозможно."
        )

def print_graph(graph: dict[str, list[str]]) -> None:
    print("\nГраф зависимостей:")
    for package, dependencies in graph.items():
        if dependencies:
            print(f"{package} -> {', '.join(dependencies)}")
        else:
            print(f"{package} -> (нет зависимостей)")
    print(f"\nКоличество узлов: {len(graph)}")
    print(f"Количество рёбер: {sum(map(len, graph.values()))}")



def plantuml_source(graph: dict[str, list[str]]) -> str:
    """Формирует PlantUML-описание графа с безопасными алиасами узлов."""
    lines = [
        "@startuml",
        "left to right direction",
        "skinparam packageStyle rectangle",
        "skinparam shadowing false",
    ]
    aliases = {
        package: f"node_{number}"
        for number, package in enumerate(graph, start=1)
    }
    for package, alias in aliases.items():
        label = package.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'object "{label}" as {alias}')
    for package, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in aliases:
                alias = f"node_{len(aliases) + 1}"
                aliases[dependency] = alias
                label = dependency.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'object "{label}" as {alias}')
            lines.append(f"{aliases[package]} --> {aliases[dependency]}")
    lines.append("@enduml")
    return "\n".join(lines) + "\n"


def graphviz_source(graph: dict[str, list[str]]) -> str:
    """Формирует эквивалентное описание DOT для резервного рендерера."""
    lines = ["digraph dependencies {", "  rankdir=LR;", "  node [shape=box];"]
    aliases = {
        package: f"node_{number}"
        for number, package in enumerate(graph, start=1)
    }
    for package, alias in aliases.items():
        label = package.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'  {alias} [label="{label}"];')
    for package, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in aliases:
                alias = f"node_{len(aliases) + 1}"
                aliases[dependency] = alias
                label = dependency.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'  {alias} [label="{label}"];')
            lines.append(f"  {aliases[package]} -> {aliases[dependency]};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_graph(
    graph: dict[str, list[str]],
    image_file: str,
) -> tuple[Path, Path]:
    """Сохраняет PlantUML и PNG; при отсутствии PlantUML использует Graphviz."""
    image_path = Path(image_file)
    if image_path.suffix.lower() != ".png":
        raise RepositoryError(
            "имя файла изображения должно иметь расширение .png"
        )
    if not image_path.parent.exists():
        raise RepositoryError(
            f"каталог для изображения не существует: {image_path.parent}"
        )

    source_path = image_path.with_suffix(".puml")
    try:
        source_path.write_text(
            plantuml_source(graph), encoding="utf-8"
        )
    except OSError as exc:
        raise RepositoryError(
            f"не удалось сохранить PlantUML-файл: {exc}"
        ) from exc

    plantuml = shutil.which("plantuml")
    jar_path = os.environ.get("PLANTUML_JAR")
    if plantuml:
        command = [plantuml, "-tpng", "-o", str(image_path.parent.resolve()),
                   str(source_path.resolve())]
        expected_png = image_path.parent / f"{source_path.stem}.png"
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
        except OSError as exc:
            raise RepositoryError(
                f"не удалось запустить PlantUML: {exc}"
            ) from exc
        if result.returncode == 0 and expected_png.exists():
            if expected_png.resolve() != image_path.resolve():
                expected_png.replace(image_path)
            return source_path, image_path
        detail = result.stderr.strip() or result.stdout.strip()
        raise RepositoryError(f"PlantUML не смог создать PNG: {detail}")

    if jar_path and Path(jar_path).is_file():
        try:
            result = subprocess.run(
                ["java", "-jar", jar_path, "-tpng", "-o",
                 str(image_path.parent.resolve()), str(source_path.resolve())],
                capture_output=True, text=True, check=False,
            )
        except OSError as exc:
            raise RepositoryError(
                f"не удалось запустить PlantUML: {exc}"
            ) from exc
        expected_png = image_path.parent / f"{source_path.stem}.png"
        if result.returncode == 0 and expected_png.exists():
            if expected_png.resolve() != image_path.resolve():
                expected_png.replace(image_path)
            return source_path, image_path
        detail = result.stderr.strip() or result.stdout.strip()
        raise RepositoryError(f"PlantUML не смог создать PNG: {detail}")

    dot = shutil.which("dot")
    if dot:
        dot_path = image_path.with_suffix(".dot")
        try:
            dot_path.write_text(graphviz_source(graph), encoding="utf-8")
            result = subprocess.run(
                [dot, "-Tpng", str(dot_path), "-o", str(image_path)],
                capture_output=True, text=True, check=False,
            )
        except OSError as exc:
            raise RepositoryError(
                f"не удалось создать PNG через Graphviz: {exc}"
            ) from exc
        if result.returncode == 0 and image_path.is_file():
            print(
                "Предупреждение: PlantUML не найден; PNG создан "
                "резервным рендерером Graphviz из того же графа."
            )
            return source_path, image_path
        detail = result.stderr.strip() or result.stdout.strip()
        raise RepositoryError(f"Graphviz не смог создать PNG: {detail}")

    raise RepositoryError(
        "для PNG нужен PlantUML (команда plantuml или переменная PLANTUML_JAR) "
        "либо Graphviz (команда dot). PlantUML-описание сохранено."
    )


def print_ascii_tree(
    graph: dict[str, list[str]],
    root: str,
) -> None:
    """Печатает дерево зависимостей, помечая повторные узлы и циклы."""
    print("\nДерево зависимостей:")
    active: set[str] = set()
    expanded: set[str] = set()

    def visit(package: str, prefix: str, is_last: bool) -> None:
        connector = "└── " if is_last else "├── "
        print(f"{prefix}{connector}{package}")
        if package in active:
            print(f"{prefix}{'    ' if is_last else '│   '}↳ цикл")
            return
        if package in expanded:
            print(f"{prefix}{'    ' if is_last else '│   '}↳ уже показан")
            return
        active.add(package)
        dependencies = graph.get(package, [])
        for index, dependency in enumerate(dependencies):
            visit(
                dependency,
                prefix + ("    " if is_last else "│   "),
                index == len(dependencies) - 1,
            )
        active.remove(package)
        expanded.add(package)

    print(root)
    active.add(root)
    dependencies = graph.get(root, [])
    for index, dependency in enumerate(dependencies):
        visit(dependency, "", index == len(dependencies) - 1)
    active.remove(root)


def main(argv: list[str] | None = None) -> int:
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
            # Индекс Packages может занимать сотни мегабайт. Загружаем и
            # разбираем его один раз, а не заново для каждого пакета графа.
            provider = make_remote_dependency_provider(args.repository)
        graph = build_dependency_graph(
            args.package, provider, args.package_filter or ""
        )
        print_graph(graph)
        source_path, image_path = render_graph(graph, args.image_file)
        print(f"PlantUML: {source_path}")
        print(f"PNG: {image_path}")
        if args.ascii_tree:
            print_ascii_tree(graph, args.package)
        if args.installation_order:
            print_installation_order(graph)
    except RepositoryError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
