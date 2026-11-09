# Сборка и прогоны на DGX Spark

## Зачем
1. Жюри собирает наш `Dockerfile` на своей машине, а её архитектура неизвестна (вопрос 2 к организаторам,
   vault `Conference-2026-11.md`). Нужно доказать, что образ собирается и работает на arm64, а не только на amd64.
2. На Spark владельца те же модели (Qwen3.6 / Qwen3.8), запросы бесплатны и без лимита прокси в 3 запроса.
   Прогоны и калибровку можно гонять там вместо прокси жюри, автоматически.

Сейчас мешает: адрес, имена моделей и `max_inflight` зашиты в общий `config/harness.json` под прокси; ключ
обязателен; если сервер не вернёт `usage`, расход молча считается нулевым; команды запуска есть только для Docker
с доступом к сокету.

## Критерии готовности (проверяемые)
- [ ] Профиль стенда: `HARNESS_PROFILE=spark` (или `--profile spark` у `run_agent.py`) накладывает
      `config/profiles/spark.json` поверх `config/harness.json`. Без профиля настройки равны `config/harness.json`
      как сейчас. Общий `config/harness.json` меняется только добавлением пустого `model_map`.
- [ ] Профиль задаёт адрес `http://127.0.0.1:22500/v1`, `llm.max_inflight` 4 (vllm `--batch-size 4`) и
      соответствие имён: логическое имя из `models.*` (`qwen3.6-35b-a3b-noreason`) → имя на сервере
      (`nvidia/Qwen3.6-35B-A3B-NVFP4`) + поля тела запроса (`chat_template_kwargs.enable_thinking: false`).
      В журнале вызова — оба имени (`model`, `served_model`), в `run_start` — имя профиля.
- [ ] Ключ: если профиль разрешает (`api_key_optional`), харнесс работает без ключа; без профиля отсутствие
      ключа — по-прежнему ошибка запуска.
- [ ] Нет `usage` в ответе (или `completion_tokens` = 0 при непустом ответе): цена вызова оценивается по длине
      промпта и ответа с запасом (`accounting.missing_usage_chars_per_token`), в журнале `usage_missing: true`.
      Бюджет никогда не списывается нулём за непустой ответ. Работает и с прокси, не только на Spark.
- [ ] `tools/probe_server.py` (только stdlib, запускается системным `python3` на Spark): `GET /v1/models`,
      по одному короткому запросу с рассуждениями и без. Печатает имена моделей, есть ли `usage`, есть ли
      `completion_tokens_details.reasoning_tokens`, совпадает ли `completion_tokens` с ответом + рассуждением
      (→ значение `accounting.reasoning_in_completion`). Ключ не печатает.
- [ ] `tools/docker_run.py --profile spark`: проверка перед стартом (сервер отвечает, нужная модель есть в
      `/v1/models`, иначе выход с понятной ошибкой, 0 токенов), `--network host`, передача `HARNESS_PROFILE`.
      `--runtime apptainer --image <файл.sif>` — тот же запуск через Apptainer (на Spark нет доступа к сокету
      Docker по диагностике 2026-10-29).
- [ ] Образ под arm64 собирается из того же `Dockerfile` и того же `requirements.lock`: на ПК —
      `docker buildx build --platform linux/arm64`, в контейнере `import openjiuwen` и `run_agent.py --help`
      проходят; сборка amd64 не изменилась. Команды сборки, переноса (`docker save` → `apptainer build
      … docker-archive://…`) и запуска на Spark — в README.
- [ ] `tools/check_wheels.py`: для каждой строки `requirements.lock` есть колесо `py3-none-any` или под
      `manylinux … x86_64` и `aarch64` для cp312 — иначе список проблемных пакетов и код 1.
- [ ] На Spark (делает владелец): `probe_server` → 8 примеров (`run23`) → набор gen с `--budget 1000000`;
      журналы с `profile: spark`.
- Ключевые критерии парой (тесты пишу я):
  - профиль, `tests/test_settings_profile.py`: должно пройти — `spark` меняет адрес, `max_inflight`,
    `model_map`, остальное равно базе; без профиля — словарь равен `config/harness.json`. Должно быть отклонено —
    профиль с ключом, которого нет в базе (опечатка `max_inflght`), и имя профиля с `../` → ошибка запуска;
  - учёт, `tests/test_llm_usage.py` (поддельный OpenAI-сервер на `http.server`, без моков openjiuwen): должно
    пройти — ответ с `usage` списывает ровно `completion + prompt × 0.1`, в тело запроса ушли `served_model` и
    `chat_template_kwargs`. Должно быть отклонено (не списать ноль) — тот же ответ без `usage` даёт цену > 0 и
    `usage_missing: true`.

## Не входит (non-goals)
- Смена базового образа (openEuler) — ждёт ответа организаторов 09.11.
- Отдельный lock под aarch64: проверено, что текущий годится обеим архитектурам (см. Пре-мортем).
- Запуск и смена моделей LAIKA из харнесса (`laika start/stop`) — делает владелец руками.
- Ограничения `--cpus`/`--memory` в Apptainer (нужны делегированные cgroups) — если не заработают, без них.
- Перенос калибровки со Spark в общие `config/*.json` и выбор модели этапа 2 — отдельное решение (Открытые вопросы).
- Песочница скриптов (handoff, п. 3) и GPU в контейнере.

## Ограничения
- Безопасность/данные: ключ LAIKA — только в env по имени (`-e NAME` / `--env` из env), не в командной строке,
  не в журнале и не в выводе `probe_server`. Доступа к Spark у меня нет: всё на Spark запускает владелец.
- Архитектура: `harness/settings.py` (профиль, слияние), `harness/llm.py` (имя на сервере, тело запроса, учёт без
  `usage`), `run_agent.py` (`--profile`, профиль в `run_start`), `tools/docker_run.py` (сборка команды выделяется в
  функцию — тестируется без Docker), новые `tools/probe_server.py`, `tools/check_wheels.py`. Новых `*Manager` нет.
- Контент: адрес, имена, `max_inflight`, поля тела — в `config/profiles/spark.json`; коэффициент оценки — в
  `config/harness.json` (`accounting`).
- Внешние контракты: `answer.txt`, `OUT`, `CMD` образа не меняются; без профиля поведение для жюри прежнее.
  В журнале новые поля `served_model`, `usage_missing`, `profile` — совместимо: `tools/cost_data`,
  `calibrate_success`, `gen/report` читают поля по имени и лишние пропускают.
- Консоль закоммичена отдельно (`f212d8a`, 2026-11-07) — эта фича лежит поверх неё отдельным diff.

## Пре-мортем
- Допущения о коде (проверено 2026-11-07):
  - база `python:3.12-slim@sha256:4840d5…` — OCI-индекс, в нём `linux/arm64/v8` (запрос к Docker Hub);
  - `uv pip compile requirements.in --python-platform aarch64-unknown-linux-gnu` даёт тот же набор, что под
    x86_64; по PyPI все 169 закреплённых версий ставятся колёсами: 143 `py3-none-any`, 26 `manylinux` aarch64
    с glibc ≤ 2.28 (в Debian 13 — 2.41). Сборка из исходников не понадобится;
  - без `usage` openjiuwen отдаёт `usage_metadata = None` (`openai_model_client.py:2806-2807`), а
    `harness/llm.py:89-95` превращает это в цену 0 без ошибки — риск и для прокси жюри;
  - `Model.invoke(..., **kwargs)` (`model.py:125`), `chat_template_kwargs` / `enable_thinking` openjiuwen сам
    переносит в `extra_body` (`openai_model_client.py:120-130`) — точное прохождение проверит тест с поддельным
    сервером, а не чтение кода;
  - `tools/docker_run.py` импортирует только `harness.settings` (stdlib + `dotenv` лишь при наличии `.env`) —
    запустится системным `python3` на Spark без venv.
- Частичный сбой:
  - LAIKA не запущена или запущена другая модель → проверка перед стартом в `docker_run` останавливает прогон
    до первой задачи; модель остановили посреди прогона → задачи `error`, как при обрыве прокси сейчас;
  - сервер без `usage` → оценка с запасом и `usage_missing` (критерий выше), а не тихий ноль;
  - Apptainer игнорирует `WORKDIR` и монтирует `$HOME` → openjiuwen создаст `./logs` не там или упадёт на
    чтении-только: запуск с `--containall --pwd /tmp --writable-tmpfs`, монтирование только задач и выхода;
  - на Spark нет сети наружу → нативная сборка невозможна; путь — образ с ПК (`buildx` + `docker save`).
    На ПК сборка под arm64 идёт через эмуляцию QEMU: медленно (оценка 10–20 мин `#гипотеза`) и тяжело для RAM —
    после сборки гасить Docker и `wsl --shutdown`.
- Одновременность: 5 задач параллельно, `max_inflight` 4 = `--batch-size 4` у vllm; лишние запросы ждут в
  семафоре, 429 на локальном сервере не ожидается. Одна модель LAIKA за раз → этап 1 и этап 2 на Spark должны
  быть одной моделью (сейчас так: обе `qwen3.6-35b-a3b-noreason`).
- Достоверность как «замены прокси»: на Spark веса NVFP4, у прокси — неизвестная сборка → `p₁`, `p₂` и длина
  ответов могут отличаться. Журналы помечены `profile`, чтобы не смешивать их с прокси вслепую.
- Источник ожидаемых значений в тестах: эта spec (профиль, пара), формула учёта из `harness/budget.py`
  (контракт регламента: выходные + вход × 0.1), формат ответа OpenAI Chat Completions (`usage.prompt_tokens`,
  `completion_tokens`).
- Не заметит verify.sh: настоящую сборку под arm64, Apptainer на Spark, формат `usage` у LAIKA/vllm, качество
  NVFP4 против прокси. Это шаги 6 и 8 — вручную, итог в README и vault.

## План (заполняет Claude, утверждаешь ты)
| # | Шаг | Файлы | Проверка |
|---|---|---|---|
| ✅ 1 | Профиль: `HARNESS_PROFILE` / `--profile`, глубокое слияние, отказ на неизвестный ключ и плохое имя, `api_key_optional` | `harness/settings.py`, `config/harness.json` (+ `model_map: {}`), `config/profiles/spark.json`, `run_agent.py` | `tests/test_settings_profile.py` (пара профиля) |
| ✅ 2 | Имя на сервере и поля тела запроса из `model_map`; `served_model` в журнале, `profile` в `run_start` | `harness/llm.py`, `run_agent.py` | `tests/test_llm_usage.py`: поддельный сервер получил нужные `model` и `chat_template_kwargs` |
| ✅ 3 | Учёт без `usage`: оценка по символам с запасом, `usage_missing` | `harness/llm.py`, `config/harness.json` (`accounting`) | пара учёта в `tests/test_llm_usage.py` (падает на текущем коде) |
| ✅ 4 | `probe_server`: модели, `usage`, рассуждения в `completion_tokens` | `tools/probe_server.py` | тест с поддельным сервером: два варианта `usage` → верный совет по `reasoning_in_completion`; ключ не в выводе |
| ✅ 5 | `docker_run`: `--profile`, проверка перед стартом, `--network host`, `--runtime apptainer` | `tools/docker_run.py` | тест сборки команд (docker / apptainer): ключ только по имени, монтирования, `--pwd /tmp` |
| ✅ 6 | Сборка под arm64 и проверка колёс | `tools/check_wheels.py`, `Dockerfile` (комментарий про arm64, без смены digest) | `check_wheels` на lock → 0; `buildx --platform linux/arm64` на ПК, в образе `import openjiuwen`; amd64 собирается |
| ✅ 7 | Docs | `README.md` (раздел «DGX Spark»), `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` | — |
| 8 | Прогоны на Spark (владелец по README) | — | `probe_server`, затем `run23` (8 примеров), набор gen с `--budget 1000000`; журналы — в vault History |

## Открытые вопросы
1. Ключ LAIKA: как получить его для своего клиента (команда `laika …`, переменная)? Или API на 127.0.0.1 работает
   без ключа? От ответа зависит `api_key_optional` в профиле.
2. Сейчас на Spark есть доступ к сокету Docker и сеть наружу (PyPI, Docker Hub)? Если да — проще собрать образ прямо
   там (`docker build`), это и есть проверка «как у жюри на arm64». Если нет — образ с ПК + Apptainer. В spec оба
   пути; узнать можно `docker info` и `curl -I https://pypi.org`.
3. Журналы Spark — в общую калибровку (`calibrate_cost`, `calibrate_success`) или отдельно? Предлагаю отдельно:
   по умолчанию инструменты калибровки их пропускают, пока на одном наборе не сравним Spark с прокси.
4. Консоль не закоммичена и правит те же файлы. Закоммитить её до начала кода (отдельным коммитом)? Предлагаю да:
   иначе в одном коммите смешаются две фичи.

Решения 2026-11-08 (владелец: «Ок, начинай»): 1 — `api_key_optional` в профиле, реальный ключ из env берётся,
если задан; 2 — в README оба пути (Docker на Spark / образ с ПК + Apptainer); 3 — журналы Spark помечены
`profile: spark`, в общую калибровку вслепую не идут (фильтр в инструментах калибровки — не в этой фиче);
4 — консоль закоммичена до кода (`f212d8a`).
