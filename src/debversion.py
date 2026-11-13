"""Версии Debian и объединение ограничений версий зависимостей."""
import re
from typing import Iterable, NamedTuple

EQUAL = 0
LESS = -1
GREATER = 1
TILDE_ORDER = -1
OTHER_OFFSET = 256
ZERO_TEXT = "0"
EXACT_OPERATOR = "="
LOWER_OPERATORS = (">>", ">=")
STRICT_OPERATORS = (">>", "<<")
LEGACY_OPERATORS = {"<": "<=", ">": ">="}
VERSION_CLAUSE = r"(?:\(\s*(<<|<=|>>|>=|=|<|>)\s*([^)\s]+)\s*\))?"
DEPENDENCY_PATTERN = re.compile(
    r"^\s*([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9-]+)?\s*" + VERSION_CLAUSE
)
NON_DIGITS = re.compile(r"[^0-9]*")
DIGITS = re.compile(r"[0-9]*")


class Constraint(NamedTuple):
    """Предикат версии, например ``>= 2.34``."""

    operator: str
    version: str

    def __str__(self) -> str:
        return f"{self.operator} {self.version}"


class Dependency(NamedTuple):
    """Зависимость от пакета с необязательными ограничениями версии."""

    name: str
    constraints: tuple[Constraint, ...] = ()


def char_order(char: str) -> int:
    """Возвращает вес символа по правилам сравнения dpkg."""
    if char == "~":
        return TILDE_ORDER
    if char.isalpha():
        return ord(char)
    return ord(char) + OTHER_OFFSET


def compare_values(left, right) -> int:
    """Возвращает -1, 0 или 1 в зависимости от порядка значений."""
    return (left > right) - (left < right)


def order_keys(text: str, size: int) -> list[int]:
    """Строит список весов символов, дополненный нулями до size."""
    keys = [char_order(char) for char in text]
    return keys + [EQUAL] * (size - len(keys))


def compare_text(left: str, right: str) -> int:
    """Сравнивает нецифровые части версий."""
    size = max(len(left), len(right))
    return compare_values(order_keys(left, size), order_keys(right, size))


def take(text: str, pattern: re.Pattern) -> tuple[str, str]:
    """Отделяет от начала строки фрагмент, подходящий под pattern."""
    matched = pattern.match(text).group(0)
    return matched, text[len(matched):]


def compare_part(left: str, right: str) -> int:
    """Сравнивает upstream-версии или ревизии по алгоритму dpkg."""
    while left or right:
        left_text, left = take(left, NON_DIGITS)
        right_text, right = take(right, NON_DIGITS)
        result = compare_text(left_text, right_text)
        if result:
            return result
        left_number, left = take(left, DIGITS)
        right_number, right = take(right, DIGITS)
        result = compare_values(
            int(left_number or ZERO_TEXT), int(right_number or ZERO_TEXT)
        )
        if result:
            return result
    return EQUAL


def split_version(version: str) -> tuple[int, str, str]:
    """Разбивает версию на эпоху, upstream-часть и ревизию."""
    epoch, colon, rest = version.partition(":")
    if not colon:
        epoch, rest = ZERO_TEXT, version
    upstream, dash, revision = rest.rpartition("-")
    if not dash:
        upstream, revision = rest, ""
    return int(epoch) if epoch.isdigit() else EQUAL, upstream, revision


def compare_versions(left: str, right: str) -> int:
    """Сравнивает две версии Debian: -1, 0 или 1."""
    left_epoch, left_upstream, left_revision = split_version(left)
    right_epoch, right_upstream, right_revision = split_version(right)
    result = compare_values(left_epoch, right_epoch)
    if result:
        return result
    result = compare_part(left_upstream, right_upstream)
    if result:
        return result
    return compare_part(left_revision, right_revision)


def normalize(constraint: Constraint) -> Constraint:
    """Заменяет устаревшие операторы ``<`` и ``>`` на ``<=`` и ``>=``."""
    operator = LEGACY_OPERATORS.get(constraint.operator, constraint.operator)
    return Constraint(operator, constraint.version)


def pick_stricter(
    current: Constraint | None,
    candidate: Constraint,
    direction: int,
) -> Constraint:
    """Выбирает более строгую границу: direction задаёт сторону."""
    if current is None:
        return candidate
    result = compare_versions(candidate.version, current.version)
    if result == direction:
        return candidate
    if result == EQUAL and candidate.operator in STRICT_OPERATORS:
        return candidate
    return current


def merge_constraints(
    constraints: Iterable[Constraint],
) -> tuple[Constraint, ...]:
    """Оставляет самые строгие нижнюю и верхнюю границы (или точное ``=``)."""
    exact = lower = upper = None
    for item in map(normalize, constraints):
        if item.operator == EXACT_OPERATOR:
            exact = exact or item
        elif item.operator in LOWER_OPERATORS:
            lower = pick_stricter(lower, item, GREATER)
        else:
            upper = pick_stricter(upper, item, LESS)
    if exact:
        return (exact,)
    return tuple(item for item in (lower, upper) if item)


def format_constraints(constraints: Iterable[Constraint]) -> str:
    """Форматирует ограничения как ``>= 2.34, << 3``."""
    return ", ".join(str(item) for item in constraints)


def dependency_from_match(match: re.Match) -> Dependency:
    """Создаёт Dependency из совпадения с DEPENDENCY_PATTERN."""
    name, operator, version = match.groups()
    if operator is None:
        return Dependency(name)
    return Dependency(name, (Constraint(operator, version),))


def parse_dependency(text: str) -> Dependency | None:
    """Разбирает одну зависимость вида ``name (>= 1.0)``."""
    match = DEPENDENCY_PATTERN.match(text)
    return dependency_from_match(match) if match else None


def merge_dependencies(items: Iterable[Dependency]) -> list[Dependency]:
    """Объединяет повторы одного пакета, оставляя строгие ограничения."""
    grouped: dict[str, list[Constraint]] = {}
    for item in items:
        grouped.setdefault(item.name, []).extend(item.constraints)
    return [
        Dependency(name, merge_constraints(found))
        for name, found in grouped.items()
    ]
