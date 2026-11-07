# Архитектура

Харнесс кодового агента на `openjiuwen`: читает задачи из каталога, решает их в пределах общего бюджета
выходных токенов и пишет ответы. Решения и их причины — `docs/DECISIONS.md`.

## Модули и их единственная ответственность
| Модуль | Отвечает за | НЕ отвечает за | Общается с |
|---|---|---|---|
| `run_agent.py` (`Harness`) | разбор аргументов, порядок и параллельность задач, итог задачи в журнал | решение задачи, учёт токенов | planner, solver, results, calllog |
| `harness/settings.py` | `config/harness.json` + env (+ `.env` локально), загрузка промптов | логику решения | все читают `Settings` |
| `harness/tasks.py` | загрузка `*/task.json` в `Task` | проверку содержимого | — |
| `harness/planner.py` | порядок задач `reward / mean`, допуск задачи по `high` с резервом в бюджете | выполнимость, решение | cost_model, budget |
| `harness/cost_model.py` | оценка цены задачи (`mean`, `high`) по длине промпта и типу, поправка по факту прогона | выбор задач, учёт | `config/cost_model.json` |
| `harness/prompting.py` | сообщения этапа 1 (система, задача + срез, повтор) и их длина — без LLM | вызов модели | snapshot |
| `harness/feasibility/` | этап 0: невыполнимые задачи без LLM и без запуска кода задачи — общая проверка файлов (`files`) + по типу (`coding`, `data`, `math_check`, `search`), агрегатор `detector` | решение, порядок задач | `Task`, `config/feasibility.json` |
| `harness/snapshot.py` | этап 0: срез папки задачи для промпта (файлы, вывод тестов, шапка CSV) | вызовы LLM | executor |
| `harness/solver.py` | этап 1 по готовым сообщениям: вызов модели → скрипт → запуск → проверка, повтор с ошибкой | учёт бюджета, запись ответа | llm, prompting, executor, verifier |
| `harness/verifier.py` | бесплатная проверка результата по `check` (тесты, число, строка) | — | executor |
| `harness/executor.py` | запуск Python в папке задачи с таймаутом, без ключа API в env | — | — |
| `harness/llm.py` | вызов модели через openjiuwen `Model`, семафор 3, повтор при 429, списание бюджета | выбор модели | budget, calllog |
| `harness/budget.py` | цена вызова, общий счётчик бюджета, резервы задач в работе | — | — |
| `harness/results.py` | `answer.txt` и атомарная запись `OUT` | — | — |
| `harness/calllog.py` | JSONL-журнал вызовов и итогов задач | — | — |
| `tools/` | замеры моделей, запуск в Docker, локальная оценка ответов; калибровка модели расхода (`calibrate_cost`, данные — `cost_data`) и проигрыш журналов при урезанном бюджете (`simulate_budget`) | работу харнесса в контейнере | — |
| `tools/gen/` | генерация тренировочных наборов (по модулю на тип), разметка `_labels.json`, офлайн-оценка детектора, отчёт прогона | работу харнесса | `harness.feasibility` (оценка) |

## Откуда берётся контент
- Параметры (модели, лимиты, бюджет): `config/harness.json`; параметры модели расхода: `config/cost_model.json`
  (пишет `tools/calibrate_cost.py`).
- Ключевые слова детектора невыполнимых (синонимы полей, слова-заменители чисел): `config/feasibility.json`.
- Шаблоны инструкций и словари генератора: `tools/gen/content/` (`templates.json`, `vocab.json`, пул функций
  `coding_pool.py`).
- Тексты промптов: `prompts/*.txt`.
- Примеры задач организаторов: `data/task-examples/task-dir`.

## Поток данных
`load_tasks` → фаза A для всех задач (до 5 параллельно, без LLM): `FeasibilityDetector` (hard → пропуск,
0 токенов) → `Stage1Prompt.build` (`WorkspaceSnapshot`) → `TaskPlanner.order` (`reward / CostModel.mean`) →
фаза B (до 5 параллельно): `TaskPlanner.admit` (резерв `high` в `BudgetLedger`; не поместилась — второй
проход после остальных, затем `skipped_budget`) →
`Stage1Solver` (LLM → скрипт → `Verifier`) → `TaskPlanner.finish` (снять резерв, `CostModel.observe`) →
`ResultsWriter` (`answer.txt`, `OUT`) + `JsonlLog` (`prompt_chars`, `estimate_mean`, `estimate_high`).

## Бюджеты
См. `.claude/limits.conf`. Превышение = сигнал разделить модуль.
