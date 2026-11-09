# Архитектура

Харнесс кодового агента на `openjiuwen`: читает задачи из каталога, решает их в пределах общего бюджета
выходных токенов и пишет ответы. Решения и их причины — `docs/DECISIONS.md`.

## Модули и их единственная ответственность
| Модуль | Отвечает за | НЕ отвечает за | Общается с |
|---|---|---|---|
| `run_agent.py` (`Harness`) | разбор аргументов, порядок и параллельность задач, λ по ещё не начатым задачам, пропуск soft-задач по политике, события `run_start` / `task_start` и итог задачи в журнал; `main` подписывает консоль на журнал | решение задачи, развилки, учёт токенов, вывод в консоль | planner, decision, pipeline, results, calllog, console |
| `harness/settings.py` | `config/harness.json` + профиль стенда (`config/profiles/<имя>.json`, `HARNESS_PROFILE` / `--profile`; ключ, которого нет в базе, — ошибка) + env (+ `.env` локально), загрузка промптов | логику решения | все читают `Settings` |
| `harness/tasks.py` | загрузка `*/task.json` в `Task` | проверку содержимого | — |
| `harness/planner.py` | порядок задач по `DecisionPolicy.priority`, допуск задачи по `high` с резервом в бюджете | выполнимость, решение | cost_model, budget |
| `harness/success_model.py` | вероятности успеха веток (`stage1` по типу, `retry`/`stage2` по виду провала) и `p_feasible` по коду soft-сигнала: бета-априор + факт прогона | цены, выбор | `config/success_model.json` |
| `harness/decision.py` (`DecisionPolicy`) | λ — цена токена (жадный рюкзак оставшихся), EMV веток после провала и резерв выбранной, `skip_soft`, приоритет `reward · p / E[цена]`; без LLM и ввода-вывода | вызовы модели | success_model, cost_model, budget |
| `harness/pipeline.py` (`TaskPipeline`) | задача по дереву: этап 1 → развилка → повтор / этап 2 / стоп; событие `decision` в момент развилки; режим `--force-stage2` | расчёт EMV | solver, stage2, decision, calllog |
| `harness/stage2.py` (`Stage2Solver`) | этап 2: короткий цикл через `LlmGateway`, инструмент `run` (Python-скрипт в папке задачи), ответ `FINAL`, стоп по шагам / бюджету ветки / повтору | выбор ветки | llm, executor, verifier, cost_model |
| `harness/outcome.py` | итог задачи по ходу решения (вызовы, развилки, переписка этапа 1) | — | — |
| `harness/cost_model.py` | оценка цены этапа 1 (`mean`, `high`), повтора и этапа 2 (и одного его шага) по длине промпта и типу, поправка по факту прогона | выбор задач, учёт | `config/cost_model.json` |
| `harness/prompting.py` | сообщения этапа 1 (система + правило отказа `prompts/system_unsolvable.txt`, если включено; задача + срез + хвост формы ответа по типу `stage1.tail_prompts`, повтор) и их длина — без LLM | вызов модели | snapshot |
| `harness/feasibility/` | этап 0: невыполнимые задачи без LLM и без запуска кода задачи — общая проверка файлов (`files`) + по типу (`coding`, `data`, `math_check`, `search`), агрегатор `detector` | решение, порядок задач | `Task`, `config/feasibility.json` |
| `harness/snapshot.py` | этап 0: срез папки задачи для промпта (файлы, вывод тестов, шапка CSV) | вызовы LLM | executor, kb_slice |
| `harness/kb_slice.py` | узкий срез базы знаний: файлы с заголовком из вопроса + обход связей `Ключ: Значение` (`snapshot.kb_*`) | ранжирование без семян | — |
| `harness/solver.py` | этап 1 по готовым сообщениям: одна попытка (`first` / `retry` с ошибкой) — вызов модели → скрипт → запуск → проверка | решение о повторе, учёт бюджета, запись ответа | llm, prompting, executor, verifier |
| `harness/verifier.py` | бесплатная проверка результата по `check` (тесты, число, строка), вид провала (`no_code`, `script_error`, `tests_fail`, `bad_format`) или отказ модели `unsolvable` (маркер — вся последняя строка stdout), ответ из свежего `answer.txt` | — | executor |
| `harness/executor.py` | запуск Python в папке задачи с таймаутом, без ключа API в env | — | — |
| `harness/llm.py` | вызов модели через openjiuwen `Model`, семафор `max_inflight`, повтор при 429, логическое имя → имя на сервере и поля тела (`model_map` профиля), списание бюджета; нет `usage` — оценка по символам с запасом (`usage_missing`) | выбор модели | budget, calllog |
| `harness/budget.py` | цена вызова, общий счётчик бюджета, резервы задач в работе | — | — |
| `harness/results.py` | `answer.txt` рядом с `task.json` (у задачи без ответа — удалить), атомарная запись `OUT` | — | — |
| `harness/calllog.py` | JSONL-журнал вызовов, развилок и итогов задач; подписчики получают те же записи под замком журнала (порядок = порядок строк), упавший подписчик отключается | — | — |
| `harness/console.py` (`ConsoleView`) | консоль прогона в stderr: счётчики по событиям, живая строка прогресса (терминал — `\r`, иначе — строка раз в `plain_every_sec`), строки развилок / задач / 429, сводка; безопасная запись (замена символов, выключение при сбое) | решение, бюджет — только читает `BudgetLedger` | calllog (подписчик), budget, console_format |
| `harness/console_format.py` (`LineFormat`) | строки консоли из событий и чисел (разделители, подписи статусов) — без ввода-вывода | — | `config/console.json` |
| `harness/jiuwen_logging.py` | тихий openjiuwen: его журнал только в файл, фильтр `SyntaxWarning` pysbd; импортируется до `openjiuwen.core.foundation.llm` | — | `config/console.json` |
| `tools/` | замеры моделей, запуск в Docker или Apptainer (`docker_run`, проверка сервера стенда перед стартом), проверка сервера моделей (`probe_server`, только stdlib) и колёс lock под x86_64/aarch64 (`check_wheels`), локальная оценка ответов; калибровка модели расхода (`calibrate_cost`, данные — `cost_data`) и вероятностей (`calibrate_success`), проигрыш журналов при урезанном бюджете (`simulate_budget`, в т. ч. с политикой EMV) | работу харнесса в контейнере | — |
| `tools/gen/` | генерация тренировочных наборов (по модулю на тип), разметка `_labels.json`, офлайн-оценка детектора, отчёт прогона | работу харнесса | `harness.feasibility` (оценка) |

## Откуда берётся контент
- Параметры (модели, лимиты, бюджет, `stage2.max_steps`): `config/harness.json`; отличия стенда (адрес, имена
  моделей на сервере, `max_inflight`) — `config/profiles/*.json` (сейчас `spark`); параметры модели расхода:
  `config/cost_model.json` (пишет `tools/calibrate_cost.py`); вероятности веток и `p_feasible`:
  `config/success_model.json` (раздел `priors` — вручную, остальное пишет `tools/calibrate_success.py`).
- Форматы строк консоли, подписи статусов, интервалы, замены символов, выводы журнала openjiuwen: `config/console.json`.
- Ключевые слова детектора невыполнимых (синонимы полей, слова-заменители чисел): `config/feasibility.json`.
- Шаблоны инструкций и словари генератора: `tools/gen/content/` (`templates.json`, `vocab.json`, пул функций
  `coding_pool.py`).
- Тексты промптов: `prompts/*.txt`.
- Примеры задач организаторов: `data/task-examples/task-dir`.

## Поток данных
`load_tasks` → фаза A для всех задач (до 5 параллельно, без LLM): `FeasibilityDetector` (hard → пропуск,
0 токенов) → `Stage1Prompt.build` (`WorkspaceSnapshot`) → `TaskPlanner.order` (`DecisionPolicy.priority`) →
`DecisionPolicy.skip_soft` при λ всех задач (`skipped_low_value`, 0 токенов) →
фаза B (до 5 параллельно): `TaskPlanner.admit` (резерв `high` в `BudgetLedger`; не поместилась — второй
проход после остальных, затем `skipped_budget`) →
`TaskPipeline`: `Stage1Solver.first` (LLM → скрипт → `Verifier`) → отказ `unsolvable` — конец задачи без ответа; провал → `DecisionPolicy.branch` при λ по ещё
не начатым задачам (`retry` | `stage2` | `stop`, резерв `high` ветки) → `Stage1Solver.retry` или `Stage2Solver` →
`TaskPlanner.finish` (снять резерв, `CostModel.observe` по первому вызову этапа 1) →
`ResultsWriter` (`answer.txt`, `OUT`) + `JsonlLog` (`prompt_chars`, оценки, `lambda`, `p_success`, `decisions`,
`solved_by`, `stage2_stop`). Каждая запись журнала (`run_start`, `task_start`, `decision`, `rate_limit`, `task`)
сразу уходит подписчику `ConsoleView` → stderr.

## Бюджеты
См. `.claude/limits.conf`. Превышение = сигнал разделить модуль.
