# Визуализатор графа зависимостей — этап 1

Минимальный CLI-прототип на Python 3.10+. На этом этапе приложение проверяет параметры и печатает их в формате `ключ=значение`. Получение зависимостей и визуализация графа пока не реализованы.

## Параметры

| Параметр | Назначение |
|---|---|
| `--package` | Имя анализируемого пакета (обязательный) |
| `--repository` | URL HTTP(S) или путь к файлу тестового репозитория (обязательный) |
| `--test-repository` | Режим локального тестового репозитория; проверяет, что путь существует и указывает на читаемый файл |
| `--output` | Имя файла графа; по умолчанию `dependencies.png`; разрешены `.png`, `.svg`, `.jpg`, `.jpeg`, `.pdf` |
| `--ascii-tree` | Включить ASCII-дерево |
| `--filter` | Подстрока для фильтрации пакетов |

## Примеры

Обычный режим:

```bash
python dependency_graph.py --package requests --repository https://pypi.org/simple
```

Тестовый режим:

```bash
printf '{"packages": []}' > repo.json
python dependency_graph.py --package demo --repository repo.json --test-repository --output graph.svg --ascii-tree --filter demo
```

Успешный запуск печатает все шесть настроек как `ключ=значение`. Ошибочные параметры приводят к ненулевому коду завершения и сообщению об ошибке в `stderr`.

## Сценарные тесты (без pytest)

```bash
python tests/test_cli.py
```

## Стандартный коммит

После проверки создайте git-репозиторий (если его ещё нет), добавьте файлы и выполните коммит в стиле Conventional Commits:

```bash
git init
git add dependency_graph.py tests/test_cli.py README.md
git commit -m "feat: add configurable dependency graph CLI prototype"
```
