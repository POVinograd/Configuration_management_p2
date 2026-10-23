# Dependency Visualizer — этап 1

Минимальный CLI-прототип для настройки будущего инструмента визуализации графа зависимостей Python-пакетов. На первом этапе приложение только валидирует конфигурацию и выводит параметры `ключ=значение`; загрузка репозитория, разрешение зависимостей и построение изображения пока не выполняются.

## Требования

- Python 3.10+
- Сторонние runtime-зависимости отсутствуют.

## Запуск из исходников

Linux/macOS:

```bash
./run.sh --package requests --repo https://example.org/repository
```

Windows (cmd.exe):

```bat
run.bat --package requests --repo https://example.org/repository
```

Запуск через `PYTHONPATH` также поддерживается:

```bash
PYTHONPATH=src python -m dependency_visualizer.cli \
  --package requests --repo https://example.org/repository
```

Локальный тестовый репозиторий должен быть существующим каталогом и использоваться вместе с тестовым режимом:

```bash
mkdir -p test_repo
PYTHONPATH=src python -m dependency_visualizer.cli \
  --package demo --repo ./test_repo --test-mode true \
  --output graph.svg --ascii --filter dep
```

`--test-mode` принимает `true/false`, `yes/no`, `да/нет`, `1/0`. URL допускает схемы HTTP, HTTPS и file. Формат выходного файла проверяется, но изображение на этапе 1 не создаётся.

## Опции

- `--package NAME` — обязательное имя пакета.
- `--repo URL_OR_PATH` — обязательный URL или путь к существующему локальному каталогу.
- `--test-mode BOOL` — режим тестового репозитория, по умолчанию `false`.
- `--output FILE` — имя файла изображения, по умолчанию `graph.png`; разрешены `.png`, `.svg`, `.jpg`, `.jpeg`, `.pdf`.
- `--ascii` — включить режим ASCII-дерева.
- `--filter SUBSTRING` — фильтрующая подстрока; по умолчанию фильтр не задан.

## Проверки

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Обрабатываются ошибки отсутствующих обязательных аргументов, пустого/некорректного имени пакета, неизвестного или недоступного репозитория, несовместимого режима репозитория, некорректного булева значения, неверного расширения/пути выходного файла и пустого фильтра. `argparse` выводит сообщение об ошибке и завершает CLI с кодом 2.

## Коммит этапа 1

Рекомендуемое сообщение коммита по Conventional Commits:

```text
feat: add configurable dependency visualizer CLI prototype
```

## Состав репозитория и стиль

- Исходный код находится в `src/`, тесты — в `tests/`.
- `run.sh` и `run.bat` предоставляют скрипты запуска без установки пакета.
- `.gitignore` исключает кэш Python, артефакты сборки, архивы и файлы редакторов.
- Изменения оформляются коммитами Conventional Commits, например `fix: enforce repository style checks`.
