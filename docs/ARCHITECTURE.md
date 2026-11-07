# Архитектура

Харнесс кодового агента на `openjiuwen`: читает задачи из каталога, решает их в пределах общего бюджета
выходных токенов и пишет ответы. Решения и их причины — `docs/DECISIONS.md`.

## Модули и их единственная ответственность
| Модуль | Отвечает за | НЕ отвечает за | Общается с |
|---|---|---|---|
| `run_agent.py` (`Harness`) | разбор аргументов, порядок и параллельность задач, итог задачи в журнал | решение задачи, учёт токенов | planner, solver, results, calllog |
| `harness/settings.py` | `config/harness.json` + env (+ `.env` локально), загрузка промптов | логику решения | все читают `Settings` |
| `harness/tasks.py` | загрузка `*/task.json` в `Task` | проверку содержимого | — |
| `harness/planner.py` | порядок задач `reward / оценка расхода` | выполнимость, решение | `Task`, конфиг |
| `harness/feasibility/` | этап 0: невыполнимые задачи без LLM и без запуска кода задачи — общая проверка файлов (`files`) + по типу (`coding`, `data`, `math_check`, `search`), агрегатор `detector` | решение, порядок задач | `Task`, `config/feasibility.json` |
| `harness/snapshot.py` | этап 0: срез папки задачи для промпта (файлы, вывод тестов, шапка CSV) | вызовы LLM | executor |
| `harness/solver.py` | этап 1: вызов модели → скрипт → запуск → проверка, повтор с ошибкой | учёт бюджета, запись ответа | llm, executor, verifier |
| `harness/verifier.py` | бесплатная проверка результата по `check` (тесты, число, строка) | — | executor |
| `harness/executor.py` | запуск Python в папке задачи с таймаутом, без ключа API в env | — | — |
| `harness/llm.py` | вызов модели через openjiuwen `Model`, семафор 3, повтор при 429, списание бюджета | выбор модели | budget, calllog |
| `harness/budget.py` | цена вызова и общий счётчик бюджета | — | — |
| `harness/results.py` | `answer.txt` и атомарная запись `OUT` | — | — |
| `harness/calllog.py` | JSONL-журнал вызовов и итогов задач | — | — |
| `tools/` | замеры моделей, запуск в Docker, локальная оценка ответов | работу харнесса в контейнере | — |
| `tools/gen/` | генерация тренировочных наборов (по модулю на тип), разметка `_labels.json`, офлайн-оценка детектора, отчёт прогона | работу харнесса | `harness.feasibility` (оценка) |

## Откуда берётся контент
- Параметры (модели, лимиты, бюджет, оценки расхода): `config/harness.json`.
- Ключевые слова детектора невыполнимых (синонимы полей, слова-заменители чисел): `config/feasibility.json`.
- Шаблоны инструкций и словари генератора: `tools/gen/content/` (`templates.json`, `vocab.json`, пул функций
  `coding_pool.py`).
- Тексты промптов: `prompts/*.txt`.
- Примеры задач организаторов: `data/task-examples/task-dir`.

## Поток данных
`load_tasks` → `TaskPlanner.order` → для каждой задачи (до 5 параллельно): `FeasibilityDetector` (hard →
пропуск, 0 токенов) → `WorkspaceSnapshot` →
`Stage1Solver` (LLM → скрипт → `Verifier`) → `ResultsWriter` (`answer.txt`, `OUT`) + `JsonlLog`.

## Бюджеты
См. `.claude/limits.conf`. Превышение = сигнал разделить модуль.
