"""CLI-инструмент для построения графа зависимостей Ubuntu APT."""
import argparse
import gzip
import itertools
import json
import lzma
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from debversion import (
    VERSION_CLAUSE,
    Constraint,
    Dependency,
    dependency_from_match,
    format_constraints,
    merge_constraints,
    merge_dependencies,
    parse_dependency,
)

PACKAGE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
GRAPH_PACKAGE_PATTERN = re.compile(r"^[A-Z]+$")
TEST_DEPENDENCY_PATTERN = re.compile(
    r"^\s*([A-Z]+)\s*" + VERSION_CLAUSE + r"\s*$"
)
WINDOWS_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
BINARY_DIR_PATTERN = re.compile(r"binary-[A-Za-z0-9_-]+")
URL_SCHEMES = {"http", "https"}
DEPENDENCY_FIELDS = ("Pre-Depends", "Depends")
PACKAGES_NAMES = {"Packages", "Packages.gz", "Packages.xz"}
DEFAULT_SUITES = ("noble", "jammy", "focal")
COMPONENTS = ("main", "universe", "restricted", "multiverse")
ARCHITECTURES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "i386": "i386",
    "i686": "i386",
    "armv7l": "armhf",
}
PLANTUML_HEADER = (
    "@startuml",
    "left to right direction",
    "skinparam packageStyle rectangle",
    "skinparam shadowing false",
)
TIMEOUT_SECONDS = 15
EXIT_OK = 0
EXIT_ERROR = 1
SUCCESS = 0
UNVISITED = 0
IN_PROGRESS = 1
DONE = 2
BRANCH = "├── "
LAST_BRANCH = "└── "
PIPE = "│   "
INDENT = "    "

Provider = Callable[[str], list[Dependency]]


class RepositoryError(Exception):
    """Ошибка загрузки или разбора репозитория."""


class DependencyGraph:
    """Граф зависимостей: узлы, рёбра и ограничения версий на рёбрах."""

    def __init__(self, root: str) -> None:
        self.edges: dict[str, list[str]] = {root: []}
        self.constraints: dict[tuple[str, str], tuple[Constraint, ...]] = {}

    def add_edge(self, source: str, dependency: Dependency) -> None:
        """Добавляет ребро source -> dependency с его ограничениями."""
        self.edges[source].append(dependency.name)
        key = (source, dependency.name)
        self.constraints[key] = dependency.constraints

    def iter_edges(self) -> Iterator[tuple[str, str]]:
        """Перебирает все рёбра графа."""
        for source, targets in self.edges.items():
            for target in targets:
                yield source, target

    def edge_count(self) -> int:
        """Возвращает количество рёбер."""
        return sum(map(len, self.edges.values()))

    def edge_label(self, source: str, target: str) -> str:
        """Возвращает предикат версии на ребре (пустую строку, если нет)."""
        found = self.constraints.get((source, target), ())
        return format_constraints(found)

    def edge_text(self, source: str, target: str) -> str:
        """Возвращает имя зависимости вместе с предикатом версии."""
        label = self.edge_label(source, target)
        return f"{target} ({label})" if label else target

    def strictest(self, target: str) -> tuple[Constraint, ...]:
        """Объединяет ограничения всех входящих рёбер в самые строгие."""
        collected = [
            item
            for (_, end), found in self.constraints.items()
            if end == target
            for item in found
        ]
        return merge_constraints(collected)


def package_name(value: str) -> str:
    """Проверяет имя пакета из командной строки."""
    if not re.fullmatch(PACKAGE_PATTERN, value):
        raise argparse.ArgumentTypeError(
            "имя должно содержать латинские буквы, цифры, '.', '_' или '-'"
        )
    return value


def repository_value(value: str) -> str:
    """Проверяет URL репозитория или путь к локальному файлу."""
    if not value.strip():
        raise argparse.ArgumentTypeError(
            "адрес репозитория не может быть пустым"
        )
    if WINDOWS_PATH_PATTERN.match(value) or "://" not in value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in URL_SCHEMES or not parsed.netloc:
        raise argparse.ArgumentTypeError(
            "URL должен начинаться с http:// или https://"
        )
    return value


def filter_value(value: str) -> str:
    """Проверяет подстроку фильтрации."""
    if not value:
        raise argparse.ArgumentTypeError(
            "подстрока фильтрации не может быть пустой"
        )
    return value


def add_source_options(parser: argparse.ArgumentParser) -> None:
    """Добавляет параметры источника данных."""
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


def add_output_options(parser: argparse.ArgumentParser) -> None:
    """Добавляет параметры вывода."""
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


def build_parser() -> argparse.ArgumentParser:
    """Создаёт разборщик аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Построение графа зависимостей пакетов."
    )
    add_source_options(parser)
    add_output_options(parser)
    return parser


def print_options(args: argparse.Namespace) -> None:
    """Выводит все настраиваемые параметры в формате ключ=значение."""
    print(f"package={args.package}")
    print(f"repository={args.repository}")
    print(f"test_repository={args.test_repository}")
    print(f"filter={args.package_filter or ''}")
    print(f"installation_order={args.installation_order}")
    print(f"image_file={args.image_file}")
    print(f"ascii_tree={args.ascii_tree}")


def parse_control_data(data: str) -> list[dict[str, str]]:
    """Разбирает текст в формате Debian control на список записей."""
    packages: list[dict[str, str]] = []
    fields: dict[str, str] = {}
    last_field = None

    def save_record() -> None:
        """Сохраняет накопленную запись и очищает буфер."""
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


def extract_dependencies(package: dict[str, str]) -> list[Dependency]:
    """Извлекает прямые зависимости с самыми строгими предикатами версий."""
    items = []
    for field in DEPENDENCY_FIELDS:
        for group in package.get(field, "").split(","):
            for alternative in group.split("|"):
                parsed = parse_dependency(alternative)
                if parsed:
                    items.append(parsed)
    return merge_dependencies(items)


def decode_index(raw: bytes, source: str) -> str:
    """Распаковывает (по расширению) и декодирует индекс пакетов."""
    try:
        if source.endswith(".gz"):
            raw = gzip.decompress(raw)
        elif source.endswith(".xz"):
            raw = lzma.decompress(raw)
        return raw.decode("utf-8-sig")
    except (OSError, EOFError, lzma.LZMAError, UnicodeDecodeError) as exc:
        raise RepositoryError(
            f"не удалось прочитать индекс {source}: {exc}"
        ) from exc


def read_file_bytes(path: str) -> bytes:
    """Читает локальный файл репозитория."""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise RepositoryError(
            f"тестовый файл не найден или недоступен: {exc}"
        ) from exc


def fetch_url(url: str) -> str:
    """Скачивает индекс по URL и возвращает его текст."""
    request = Request(url, headers={"User-Agent": "dependency-graph/1.0"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RepositoryError(f"не удалось загрузить {url}: {exc}") from exc
    return decode_index(raw, urlparse(url).path)


def architecture_name() -> str:
    """Возвращает имя архитектуры в терминах APT."""
    machine = platform.machine().lower()
    return ARCHITECTURES.get(machine, machine)


def ubuntu_codename() -> str | None:
    """Возвращает кодовое имя текущего выпуска Ubuntu, если оно известно."""
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


def candidate_suites() -> list[str]:
    """Возвращает список выпусков для поиска индекса без повторов."""
    suites: list[str] = []
    for suite in (ubuntu_codename(), *DEFAULT_SUITES):
        if suite and suite not in suites:
            suites.append(suite)
    return suites


def folder_urls(folder: str) -> list[str]:
    """Возвращает варианты файла Packages в каталоге binary-*."""
    return [
        f"{folder}/Packages.xz",
        f"{folder}/Packages.gz",
        f"{folder}/Packages",
    ]


def index_urls(repository: str) -> list[str]:
    """Строит список URL, по которым может лежать индекс Packages."""
    base = repository.rstrip("/")
    filename = urlparse(repository).path.rstrip("/").rsplit("/", 1)[-1]
    if filename in PACKAGES_NAMES:
        return [repository]
    if BINARY_DIR_PATTERN.fullmatch(filename):
        return folder_urls(base)
    urls: list[str] = []
    variants = itertools.product(
        candidate_suites(), COMPONENTS, (architecture_name(), "all")
    )
    for suite, component, arch in variants:
        folder = f"{base}/dists/{suite}/{component}/binary-{arch}"
        urls.extend(folder_urls(folder))
    return urls


class RemoteIndex:
    """Скачивает индекс APT один раз и отдаёт зависимости любого пакета."""

    def __init__(self, repository: str) -> None:
        self.repository = repository
        self.loaded = False
        self.packages: dict[str, dict[str, str]] = {}
        self.provided: dict[str, dict[str, str]] = {}

    def load(self) -> None:
        """Загружает первый доступный индекс из списка URL."""
        if self.loaded:
            return
        errors = []
        for url in index_urls(self.repository):
            try:
                self.fill(url)
                return
            except RepositoryError as exc:
                errors.append(str(exc))
        detail = errors[-1] if errors else "нет доступных URL индекса"
        raise RepositoryError(f"не удалось загрузить индекс APT: {detail}")

    def fill(self, url: str) -> None:
        """Скачивает индекс по url и заполняет словари пакетов."""
        print(f"Загрузка индекса APT: {url}", flush=True)
        records = parse_control_data(fetch_url(url))
        self.packages = {}
        self.provided = {}
        for record in records:
            self.add_record(record)
        self.loaded = True
        print(
            f"Индекс загружен: {len(self.packages)} пакетов. "
            "Повторная загрузка для зависимостей не потребуется.",
            flush=True,
        )

    def add_record(self, record: dict[str, str]) -> None:
        """Регистрирует запись пакета и его виртуальные имена."""
        name = record.get("Package")
        if name:
            self.packages[name] = record
        for item in record.get("Provides", "").split(","):
            parsed = parse_dependency(item)
            if parsed:
                self.provided.setdefault(parsed.name, record)

    def __call__(self, requested_name: str) -> list[Dependency]:
        """Возвращает прямые зависимости пакета из загруженного индекса."""
        self.load()
        record = self.packages.get(requested_name)
        if record is None:
            record = self.provided.get(requested_name)
        if record is None:
            raise RepositoryError(
                f"пакет {requested_name!r} не найден в загруженном индексе"
            )
        return extract_dependencies(record)


def parse_test_dependency(owner: str, value: object) -> Dependency:
    """Разбирает зависимость тестового графа: ``B`` или ``B (>= 1.2)``."""
    match = None
    if isinstance(value, str):
        match = TEST_DEPENDENCY_PATTERN.match(value)
    if match is None:
        raise RepositoryError(f"некорректная зависимость пакета {owner}")
    return dependency_from_match(match)


def parse_graph_entry(name: str, dependencies: object) -> list[Dependency]:
    """Проверяет и разбирает одну запись JSON-графа."""
    if not GRAPH_PACKAGE_PATTERN.fullmatch(name):
        raise RepositoryError(f"недопустимое имя пакета: {name!r}")
    if not isinstance(dependencies, list):
        raise RepositoryError(f"зависимости {name} должны быть списком")
    items = [parse_test_dependency(name, value) for value in dependencies]
    return merge_dependencies(items)


def parse_graph_text(text: str) -> dict[str, list[Dependency]]:
    """Разбирает JSON-описание тестового графа."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RepositoryError(f"не удалось прочитать JSON-граф: {exc}") from exc
    if not isinstance(data, dict):
        raise RepositoryError("граф должен быть JSON-объектом")
    return {
        name: parse_graph_entry(name, dependencies)
        for name, dependencies in data.items()
    }


def records_to_graph(
    records: list[dict[str, str]],
) -> dict[str, list[Dependency]]:
    """Преобразует записи control-файла в словарь зависимостей."""
    graph = {}
    for package in records:
        name = package.get("Package")
        if name:
            graph[name] = extract_dependencies(package)
    return graph


def load_test_graph(path: str) -> dict[str, list[Dependency]]:
    """Загружает тестовый репозиторий: JSON-граф или файл Packages."""
    text = decode_index(read_file_bytes(path), Path(path).name)
    if text.lstrip().startswith("{"):
        return parse_graph_text(text)
    return records_to_graph(parse_control_data(text))


def make_local_provider(graph_data: dict[str, list[Dependency]]) -> Provider:
    """Создаёт поставщика зависимостей поверх тестового графа."""

    def provider(name: str) -> list[Dependency]:
        """Возвращает зависимости пакета (пустой список, если их нет)."""
        return graph_data.get(name, [])

    return provider


def is_excluded(name: str, package_filter: str) -> bool:
    """Проверяет, исключает ли фильтр пакет с данным именем."""
    return bool(package_filter) and package_filter in name


def build_dependency_graph(
    root: str,
    dependency_provider: Provider,
    package_filter: str = "",
) -> DependencyGraph:
    """Строит граф BFS с рекурсивной обработкой очереди."""
    if is_excluded(root, package_filter):
        raise RepositoryError(
            f"корневой пакет {root!r} исключён фильтром {package_filter!r}"
        )
    graph = DependencyGraph(root)
    visited = {root}
    queue = deque([root])

    def process_queue() -> None:
        """Обрабатывает очередной узел и рекурсивно продолжает BFS."""
        if not queue:
            return
        current = queue.popleft()
        for dependency in dependency_provider(current):
            if is_excluded(dependency.name, package_filter):
                continue
            graph.add_edge(current, dependency)
            if dependency.name not in visited:
                visited.add(dependency.name)
                graph.edges.setdefault(dependency.name, [])
                queue.append(dependency.name)
        process_queue()

    process_queue()
    return graph


def installation_order(
    graph: DependencyGraph,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Возвращает порядок установки (DFS) и рёбра, замыкающие циклы."""
    state: dict[str, int] = {}
    order: list[str] = []
    cycle_edges: list[tuple[str, str]] = []

    def visit(package: str) -> None:
        """Обходит зависимости пакета и добавляет его после них."""
        if state.get(package, UNVISITED) != UNVISITED:
            return
        state[package] = IN_PROGRESS
        for dependency in graph.edges.get(package, []):
            dependency_state = state.get(dependency, UNVISITED)
            if dependency_state == IN_PROGRESS:
                cycle_edges.append((package, dependency))
            else:
                visit(dependency)
        state[package] = DONE
        order.append(package)

    for package in graph.edges:
        visit(package)
    return order, cycle_edges


def print_installation_order(graph: DependencyGraph) -> None:
    """Печатает порядок установки и предупреждение о циклах."""
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


def print_strictest(graph: DependencyGraph) -> None:
    """Печатает для каждого пакета самые строгие ограничения версий."""
    print("\nНаиболее строгие ограничения версий:")
    found = False
    for package in graph.edges:
        text = format_constraints(graph.strictest(package))
        if text:
            print(f"{package}: {text}")
            found = True
    if not found:
        print("(ограничений версий нет)")


def print_graph(graph: DependencyGraph) -> None:
    """Печатает граф зависимостей с предикатами версий на рёбрах."""
    print("\nГраф зависимостей:")
    for package, targets in graph.edges.items():
        if targets:
            items = ", ".join(graph.edge_text(package, t) for t in targets)
            print(f"{package} -> {items}")
        else:
            print(f"{package} -> (нет зависимостей)")
    print_strictest(graph)
    print(f"\nКоличество узлов: {len(graph.edges)}")
    print(f"Количество рёбер: {graph.edge_count()}")


def escape_label(text: str) -> str:
    """Экранирует обратную косую черту и кавычки в подписи."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def node_aliases(graph: DependencyGraph) -> dict[str, str]:
    """Создаёт безопасные алиасы узлов node_1, node_2, ..."""
    return {
        package: f"node_{number}"
        for number, package in enumerate(graph.edges, start=1)
    }


def plantuml_source(graph: DependencyGraph) -> str:
    """Формирует PlantUML-описание графа с предикатами версий на рёбрах."""
    aliases = node_aliases(graph)
    lines = list(PLANTUML_HEADER)
    for package, alias in aliases.items():
        lines.append(f'object "{escape_label(package)}" as {alias}')
    for source, target in graph.iter_edges():
        label = graph.edge_label(source, target)
        suffix = f" : {label}" if label else ""
        lines.append(f"{aliases[source]} --> {aliases[target]}{suffix}")
    lines.append("@enduml")
    return "\n".join(lines) + "\n"


def graphviz_source(graph: DependencyGraph) -> str:
    """Формирует эквивалентное описание DOT для резервного рендерера."""
    aliases = node_aliases(graph)
    lines = ["digraph dependencies {", "  rankdir=LR;", "  node [shape=box];"]
    for package, alias in aliases.items():
        lines.append(f'  {alias} [label="{escape_label(package)}"];')
    for source, target in graph.iter_edges():
        label = escape_label(graph.edge_label(source, target))
        attributes = f' [label="{label}"]' if label else ""
        lines.append(
            f"  {aliases[source]} -> {aliases[target]}{attributes};"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def validate_image_path(image_file: str) -> Path:
    """Проверяет расширение и каталог файла изображения."""
    path = Path(image_file)
    if path.suffix.lower() != ".png":
        raise RepositoryError(
            "имя файла изображения должно иметь расширение .png"
        )
    if not path.parent.exists():
        raise RepositoryError(
            f"каталог для изображения не существует: {path.parent}"
        )
    return path


def write_text_file(path: Path, content: str, description: str) -> None:
    """Записывает текстовый файл, превращая ошибки в RepositoryError."""
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise RepositoryError(
            f"не удалось сохранить {description}: {exc}"
        ) from exc


def run_command(command: list[str], tool: str):
    """Запускает внешнюю программу и возвращает результат."""
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise RepositoryError(
            f"не удалось запустить {tool}: {exc}"
        ) from exc


def render_with_plantuml(
    prefix: list[str],
    source_path: Path,
    image_path: Path,
) -> None:
    """Создаёт PNG из .puml программой PlantUML."""
    out_dir = str(image_path.parent.resolve())
    command = [*prefix, "-tpng", "-o", out_dir, str(source_path.resolve())]
    result = run_command(command, "PlantUML")
    produced = image_path.parent / f"{source_path.stem}.png"
    if result.returncode == SUCCESS and produced.exists():
        if produced.resolve() != image_path.resolve():
            produced.replace(image_path)
        return
    detail = result.stderr.strip() or result.stdout.strip()
    raise RepositoryError(f"PlantUML не смог создать PNG: {detail}")


def render_with_graphviz(
    dot: str,
    graph: DependencyGraph,
    image_path: Path,
) -> None:
    """Создаёт PNG через Graphviz, если PlantUML недоступен."""
    dot_path = image_path.with_suffix(".dot")
    write_text_file(dot_path, graphviz_source(graph), "DOT-файл")
    command = [dot, "-Tpng", str(dot_path), "-o", str(image_path)]
    result = run_command(command, "Graphviz")
    if result.returncode == SUCCESS and image_path.is_file():
        print(
            "Предупреждение: PlantUML не найден; PNG создан "
            "резервным рендерером Graphviz из того же графа."
        )
        return
    detail = result.stderr.strip() or result.stdout.strip()
    raise RepositoryError(f"Graphviz не смог создать PNG: {detail}")


def render_graph(
    graph: DependencyGraph,
    image_file: str,
) -> tuple[Path, Path]:
    """Сохраняет PlantUML и PNG; при отсутствии PlantUML — Graphviz."""
    image_path = validate_image_path(image_file)
    source_path = image_path.with_suffix(".puml")
    write_text_file(source_path, plantuml_source(graph), "PlantUML-файл")
    plantuml = shutil.which("plantuml")
    jar_path = os.environ.get("PLANTUML_JAR")
    dot = shutil.which("dot")
    if plantuml:
        render_with_plantuml([plantuml], source_path, image_path)
    elif jar_path and Path(jar_path).is_file():
        prefix = ["java", "-jar", jar_path]
        render_with_plantuml(prefix, source_path, image_path)
    elif dot:
        render_with_graphviz(dot, graph, image_path)
    else:
        raise RepositoryError(
            "для PNG нужен PlantUML (команда plantuml или переменная "
            "PLANTUML_JAR) либо Graphviz (команда dot). "
            "PlantUML-описание сохранено."
        )
    return source_path, image_path


class TreePrinter:
    """Печатает дерево зависимостей, помечая повторные узлы и циклы."""

    def __init__(self, graph: DependencyGraph) -> None:
        self.graph = graph
        self.active: set[str] = set()
        self.expanded: set[str] = set()

    def show_children(self, package: str, prefix: str) -> None:
        """Печатает все зависимости пакета с заданным отступом."""
        targets = self.graph.edges.get(package, [])
        last_index = len(targets) - 1
        for index, target in enumerate(targets):
            self.show_node(package, target, prefix, index == last_index)

    def show_node(
        self,
        parent: str,
        package: str,
        prefix: str,
        is_last: bool,
    ) -> None:
        """Печатает один узел и рекурсивно его поддерево."""
        connector = LAST_BRANCH if is_last else BRANCH
        label = self.graph.edge_text(parent, package)
        print(f"{prefix}{connector}{label}")
        child_prefix = prefix + (INDENT if is_last else PIPE)
        if package in self.active:
            print(f"{child_prefix}↳ цикл")
            return
        if package in self.expanded:
            print(f"{child_prefix}↳ уже показан")
            return
        self.active.add(package)
        self.show_children(package, child_prefix)
        self.active.discard(package)
        self.expanded.add(package)


def print_ascii_tree(graph: DependencyGraph, root: str) -> None:
    """Печатает ASCII-дерево зависимостей от корневого пакета."""
    print("\nДерево зависимостей:")
    printer = TreePrinter(graph)
    print(root)
    printer.active.add(root)
    printer.show_children(root, "")


def make_provider(args: argparse.Namespace) -> Provider:
    """Выбирает источник зависимостей: тестовый файл или URL."""
    if not args.test_repository:
        return RemoteIndex(args.repository)
    graph_data = load_test_graph(args.repository)
    if args.package not in graph_data:
        raise RepositoryError(
            f"пакет {args.package!r} не найден в тестовом файле"
        )
    return make_local_provider(graph_data)


def run(args: argparse.Namespace) -> None:
    """Выполняет все этапы: граф, изображение, дерево, порядок."""
    graph = build_dependency_graph(
        args.package, make_provider(args), args.package_filter or ""
    )
    print_graph(graph)
    source_path, image_path = render_graph(graph, args.image_file)
    print(f"PlantUML: {source_path}")
    print(f"PNG: {image_path}")
    if args.ascii_tree:
        print_ascii_tree(graph, args.package)
    if args.installation_order:
        print_installation_order(graph)


def main(argv: list[str] | None = None) -> int:
    """Точка входа: разбирает аргументы и запускает построение графа."""
    args = build_parser().parse_args(argv)
    print_options(args)
    try:
        run(args)
    except RepositoryError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
