# Архитектура

Харнесс кодового агента на `openjiuwen`: читает задачи из каталога, решает их в пределах общего бюджета
выходных токенов и пишет ответы.

## Модули и их единственная ответственность
| Модуль | Отвечает за | НЕ отвечает за | Общается с |
|---|---|---|---|
| `run_agent.py` (`Harness`) | разбор аргументов, порядок и параллельность задач, λ по ещё не начатым задачам, пропуск soft-задач по политике, события `run_start` / `task_start` и итог задачи в журнал; `main` подписывает консоль на журнал и запускает прогон через `HarnessAgent.invoke` | решение задачи, развилки, учёт токенов, вывод в консоль | planner, decision, pipeline, results, calllog, console |
| `harness/agent.py` (`HarnessAgent`) | наследник openjiuwen `BaseAgent` с `AgentCard` из `config/harness.json` (`agent`): `invoke({"tasks_dir", "only"})` — загрузка задач, события `BEFORE/AFTER_INVOKE` и `BEFORE/AFTER_MODEL_CALL` (их шлёт шлюз через `hooks`) для рельс, прогон через `Harness.run`; `build_agent` регистрирует `TokenBudgetRail` | порядок, бюджет, развилки | tasks, `Harness` (через протокол `TaskRunner`) |
| `harness/settings.py` | `config/harness.json` + профиль стенда (`config/profiles/<имя>.json`, `HARNESS_PROFILE` / `--profile`; ключ, которого нет в базе, — ошибка) + env (+ `.env` локально), загрузка промптов | логику решения | все читают `Settings` |
| `harness/tasks.py` | загрузка `*/task.json` в `Task`; битый файл — пропуск, нет `reward` — 1.0 | проверку содержимого | — |
| `harness/planner.py` | порядок задач по `DecisionPolicy.priority`, допуск задачи по `high` с резервом в бюджете | выполнимость, решение | cost_model, budget |
| `harness/success_model.py` | вероятности успеха веток (`stage1` по типу, `retry`/`stage2` по виду провала) и `p_feasible` по коду soft-сигнала: бета-априор + факт прогона | цены, выбор | `config/success_model.json` |
| `harness/decision.py` (`DecisionPolicy`) | λ — цена токена (жадный рюкзак оставшихся), EMV веток после провала и резерв выбранной, `skip_soft`, приоритет `reward · p / E[цена]`; без LLM и ввода-вывода | вызовы модели | success_model, cost_model, budget |
| `harness/pipeline.py` (`TaskPipeline`) | задача по дереву: этап 1 → развилка → повтор / этап 2 / стоп; событие `decision` в момент развилки (с началом текста провала `problem`); режимы замера `--force-stage2` и `--force-branch retry\|stage2` (после провала этапа 1 — только эта ветка при λ = 0, после неудачного повтора — этап 2) | расчёт EMV | solver, stage2, decision, calllog |
| `harness/stage2.py` (`Stage2Solver`) | этап 2: короткий цикл через `LlmGateway`, инструмент `run` (Python-скрипт в папке задачи; у `patch_types` блоки `# file:` вклеиваются), ответ `FINAL`, стоп по шагам / бюджету ветки / повтору; после правки с красными тестами наблюдение — их провал (`stage2.test_feedback`, `prompts/stage2_obs_tests.txt`) | выбор ветки | llm, executor, verifier, cost_model |
| `harness/crosscheck.py` (`CrossCheck`) | флаг `crosscheck.enabled`: решённая math/data/search — второе решение этапом 1 другой моделью (`second_model`), расхождение — третье (`arbiter_model`), ответ меняется только по большинству; сбой, исчерпанный бюджет, остаток меньше `min_available_tokens` — ответ этапа 1. Выключен по замеру 10.11 (var1/var2, по 2 прогона на плечо): решено не больше (62/62; 61/62 против 60–61/62), расход +63–89 % | выбор ветки | solver, budget |
| `harness/outcome.py` | итог задачи по ходу решения (вызовы, развилки, переписка этапа 1) | — | — |
| `harness/cost_model.py` | оценка цены этапа 1 (`mean`, `high`), повтора и этапа 2 (и одного его шага) по длине промпта и типу, поправка по факту прогона | выбор задач, учёт | `config/cost_model.json` |
| `harness/prompting.py` | сообщения этапа 1 (система + правило отказа `prompts/system_unsolvable.txt`, если включено; задача + срез + хвост формы ответа по типу `stage1.tail_prompts`, повтор; у `stage1.patch_types` — свои система и повтор `stage1.patch_prompts`) и их длина — без LLM | вызов модели | snapshot |
| `harness/feasibility/` | этап 0: невыполнимые задачи без LLM и без запуска кода задачи — общая проверка файлов (`files`) + по типу (`coding`, `data`, `math_check`, `search`), агрегатор `detector` | решение, порядок задач | `Task`, `config/feasibility.json` |
| `harness/snapshot.py` | этап 0: срез папки задачи для промпта (файлы, вывод тестов, шапка CSV; тест-файл без прошедших методов, `snapshot.trim_passing_tests`) | вызовы LLM | test_runner, kb_slice, test_output, test_slice |
| `harness/test_runner.py` | изолированный запуск unittest discovery, подтверждение ненулевого числа выполненных тестов (не skip), свежий импорт после правки; отсутствие отчёта — провал | оценку правильности вне тестов, сжатие контекста | executor |
| `harness/test_slice.py` | упавшие тесты из вывода unittest и срез тест-файла: методы `test*`, прошедшие в выводе, убираются (`ast`), остальное как есть; сомнение (упал не метод, не разбирается) — файл целиком | запуск тестов | — |
| `harness/test_output.py` | сжатие вывода unittest для среза (`snapshot.compact_test_output`), сообщения провала тестов (`stage1.compact_test_output`) и наблюдений этапа 2 (`stage2.test_feedback`): имя теста, кадры одной строкой, первая строка исключения; не вывод unittest — без изменений | запуск тестов | — |
| `harness/kb_slice.py` | узкий срез базы знаний: файлы с заголовком из вопроса + обход связей `Ключ: Значение` (`snapshot.kb_*`) | ранжирование без семян | — |
| `harness/solver.py` | этап 1 по готовым сообщениям: одна попытка (`first` / `retry` с ошибкой) — вызов модели → скрипт → запуск (у `patch_types` — вклейка правки) → проверка; у `patch_types` до вклейки — событие `answer_shape` | решение о повторе, учёт бюджета, запись ответа | llm, prompting, patching, answer_shape, executor, verifier, calllog |
| `harness/patching.py` | ответ-правка: блоки с `# file: <путь>` (внутри или строкой перед блоком) или блок без заголовка из одних функций (или методов классов) единственного модуля задачи; запись файлов — все или ни одного, только внутри папки задачи и не `test_*.py`; итог — `RunResult` для `Verifier` | выбор формата ответа, сама вклейка | merge, executor (`RunResult`) |
| `harness/merge.py` | вклейка исходника правки в исходник файла через `ast` заменой диапазонов строк (остальное байт-в-байт): функции и присваивания по имени (среди одноимённых — с теми же декораторами: property/setter, overload), класс — по членам, изменённый заголовок класса (базы, декораторы) переносится, одиночный метод (`self`/`cls` или декоратор метода) — в единственный класс, где он есть, даже при одноимённой функции модуля (в нескольких — `PatchError`), новые функции — перед `if __name__`, импорты — если их нет | чтение и запись файлов | source_lines |
| `harness/source_lines.py` | строки исходника для вклейки: разбиение только по `\n`/`\r\n`/`\r` (как `ast`), фрагмент узла, сдвиг отступа без изменения многострочных строковых литералов | — | — |
| `harness/answer_shape.py` | структура ответа этапа 1 в числах: символы до/после блоков кода, число блоков, функции правки изменённые / прежние (`ast` совпадает) / новые, `whole_file`; без ввода-вывода, текст ответа не хранится | — | merge, patching |
| `harness/verifier.py` | бесплатная проверка результата по `check` (тесты, число, строка), вид провала (`no_code`, `script_error`, `tests_fail`, `bad_format`) или отказ модели `unsolvable` (маркер — вся последняя строка stdout), ответ из свежего `answer.txt` | — | test_runner, test_output |
| `harness/executor.py` | запуск Python в папке задачи с таймаутом, без ключа API в env | — | — |
| `harness/llm.py` | вызов модели через openjiuwen `Model`, семафор `max_inflight`, повтор при 429 и при обрыве/5xx (`network_*`), логическое имя → имя на сервере и поля тела (`model_map` профиля); вызов — событие `ModelCall` агента (`hooks`): допуск и списание делает рельса, шлюз — только если она этого не сделала (нет рельсы, сбой); нет `usage` — оценка по символам с запасом (`usage_missing`); ошибки попыток — отдельная оценка входа, допуск проверяется после семафора перед каждой попыткой | выбор модели | budget, calllog, model_call |
| `harness/attempt_cost.py` | оценка входа каждой ошибочной попытки после входа в Model.invoke, списание и событие `llm_attempt_error` (только вход, не точный usage) | повторы, оценку неизвестного выхода, калибровку | budget, calllog, model_call |
| `harness/model_call.py` | `ModelCall` (вход `BEFORE/AFTER_MODEL_CALL` openjiuwen + цена, usage), допуск по остатку (`admit`) и списание ровно один раз с записью `llm_call` (`settle`) | сам вызов | budget, calllog |
| `harness/rails.py` (`TokenBudgetRail`) | рельса openjiuwen: до вызова — допуск (исчерпан — `AbortError` с причиной `BudgetExhausted`), после — списание и журнал | — | model_call |
| `harness/budget.py` | цена вызова, общий счётчик бюджета, резервы задач в работе | — | — |
| `harness/results.py` | `answer.txt` рядом с `task.json` (у задачи без ответа — удалить), атомарная запись `OUT` (`{}` при старте); ошибку записи возвращает текстом, не бросает | — | — |
| `harness/calllog.py` | JSONL-журнал вызовов, развилок и итогов задач; подписчики получают те же записи под замком журнала (порядок = порядок строк), упавший подписчик отключается | — | — |
| `harness/console.py` (`ConsoleView`) | консоль прогона в stderr: счётчики по событиям, живая строка прогресса (терминал — `\r`, иначе — строка раз в `plain_every_sec`), строки развилок / задач / 429, сводка; безопасная запись (замена символов, выключение при сбое) | решение, бюджет — только читает `BudgetLedger` | calllog (подписчик), budget, console_format |
| `harness/console_format.py` (`LineFormat`) | строки консоли из событий и чисел (разделители, подписи статусов) — без ввода-вывода | — | `config/console.json` |
| `harness/jiuwen_logging.py` | тихий openjiuwen: его журнал только в файл, фильтр `SyntaxWarning` pysbd; импортируется до `openjiuwen.core.foundation.llm` | — | `config/console.json` |
| `tools/` | замеры моделей, запуск в Docker или Apptainer (`docker_run`, проверка сервера стенда перед стартом), проверка сервера моделей (`probe_server`, только stdlib) и колёс lock под x86_64/aarch64 (`check_wheels`), локальная оценка ответов; калибровка модели расхода (`calibrate_cost`, данные — `cost_data`) и вероятностей (`calibrate_success`), проигрыш журналов при урезанном бюджете (`simulate_budget`, в т. ч. с политикой EMV), парный отчёт A/B прогонов на одних задачах (`ab_report`: решено, с первой попытки, цена, форма ответа, регрессии) | работу харнесса в контейнере | — |
| `harness/rules/` | этап 0: решение math/data/search кодом, когда условие целиком по шаблону (`math_rules`, `data_rules`, `search_rules`, диспетчер `RulesSolver`); иначе `None`; при soft-сигналах только из `soft_ok` (data: месяцы за 12, значения фильтра нет в CSV и нет похожего — ответ «0») | вызов LLM, невыполнимость | `Task`, `config/rules.json` |
| `tools/gen/` | генерация тренировочных наборов (по модулю на тип), разметка `_labels.json`, офлайн-оценка детектора, отчёт прогона; набор вариантов — новые формулировки и виды вопросов (`variants`, `variants_data`, `variants_search`, эталоны и скрытые тесты coding вне папки задач) и его оценка (`score_variants`: strip + lower, видимые + скрытые тесты) | работу харнесса | `harness.feasibility` (оценка) |
| `tools/gen/holdout*` | независимый фиксированный набор: сборка и SHA256-манифест, oracle не-coding и валидация original/reference/partial для coding | вызовы модели, настройку промптов | content/holdout_*, score_variants |
| `tools/gen/score_variants.py` | сравнение ответов и изолированная проверка видимых/скрытых coding-тестов с подтверждением ненулевого выполнения; require_hidden для нового holdout | выбор модели, генерацию эталонов | harness/test_runner |

## Откуда берётся контент
- Параметры (модели, лимиты, бюджет, `stage2.max_steps`): `config/harness.json`; отличия стенда (адрес, имена
  моделей на сервере, `max_inflight`) — `config/profiles/*.json` (локальные, в git не входят; тестовый — `tests/fixtures/profiles/stand.json`); параметры модели расхода:
  `config/cost_model.json` (пишет `tools/calibrate_cost.py`); вероятности веток и `p_feasible`:
  `config/success_model.json` (раздел `priors` — вручную, остальное пишет `tools/calibrate_success.py`).
- Форматы строк консоли, подписи статусов, интервалы, замены символов, выводы журнала openjiuwen: `config/console.json`.
- Шаблоны решателя по правилам (предложения условия, вопросы, поля kb): `config/rules.json`.
- Ключевые слова детектора невыполнимых (синонимы полей, слова-заменители чисел): `config/feasibility.json`.
- Шаблоны инструкций и словари генератора: `tools/gen/content/` (`templates.json`, `vocab.json`, пул функций
  `coding_pool.py`; набор вариантов — `variants_pool.py`, `variants_coding.py`).
- Тексты промптов: `prompts/*.txt`.
- Примеры задач организаторов: `data/task-examples/task-dir`.

## Поток данных
`HarnessAgent.invoke` → `load_tasks` → фаза A для всех задач (до 5 параллельно, без LLM): `FeasibilityDetector` (hard → пропуск,
0 токенов) → без сигналов (или только из `rules.soft_ok`) `RulesSolver` (ответ по шаблону → `verified`, `solved_by: rules`, 0 токенов) → `Stage1Prompt.build` (`WorkspaceSnapshot`) → `TaskPlanner.order` (`DecisionPolicy.priority`) →
`DecisionPolicy.skip_soft` при λ всех задач (`skipped_low_value`, 0 токенов) →
фаза B (до 5 параллельно): `TaskPlanner.admit` (резерв `high` в `BudgetLedger`; не поместилась — второй
проход после остальных, затем `skipped_budget`) →
`TaskPipeline`: `Stage1Solver.first` (LLM → скрипт → `Verifier`) → отказ `unsolvable` — конец задачи без ответа; провал → `DecisionPolicy.branch` при λ по ещё
не начатым задачам (`retry` | `stage2` | `stop`, резерв `high` ветки) → `Stage1Solver.retry` или `Stage2Solver` →
`TaskPlanner.finish` (снять резерв, `CostModel.observe` по первому вызову этапа 1) →
`ResultsWriter` (`answer.txt`, `OUT`) + `JsonlLog` (`prompt_chars`, оценки, `lambda`, `p_success`, `decisions`,
`solved_by`, `stage2_stop`). Каждая запись журнала (`run_start`, `task_start`, `decision`, `rate_limit`, `net_retry`, `detector_error`, `answer_shape`, `task`)
сразу уходит подписчику `ConsoleView` → stderr.

## Бюджеты
Размер модулей держим небольшим: модуль растёт — выделяем подсистему, а не дописываем (как `harness/feasibility/`).
