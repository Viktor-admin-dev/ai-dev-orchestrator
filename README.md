# AI Dev Orchestrator

**AI-контур разработки проектов** -- из ТЗ до работающего продукта.

Оркестратор управляет двумя независимыми AI-агентами (Claude Code как разработчик, Claude Opus как аудитор), принудительно проводя каждую задачу через защиту качества: план -> код -> тесты -> аудит -> merge -> приёмка.

## Зачем это нужно

При работе с AI-агентами главная проблема -- качество и контроль. Агент может написать код, который "работает", но содержит костыли, пропускает edge-cases или нарушает архитектуру проекта.

Оркестратор решает это через:

- **Двухагентный процесс** -- разработчик и аудитор работают в отдельных контекстах, аудитор не видит рассуждения разработчика
- **State machine с принуждением** -- задача не может перейти в PR_READY без зелёных тестов И approve аудитора
- **Mutation testing** на критичных модулях -- код должен не просто проходить тесты, а быть действительно корректным
- **Бюджетный контроль** -- расходы на токены ограничены на уровне задачи и проекта
- **Явная приёмка архитектором** -- человек остаётся в цикле принятия решений

## Быстрый старт

### Установка

```bash
git clone https://github.com/Viktor-admin-dev/ai-dev-orchestrator.git
cd ai-dev-orchestrator
pip install -e ".[dev,panel]"
```

### Использование

```bash
# 1. Инициализация базы данных
orchestrator init

# 2. Добавление задач
orchestrator add-task T-001 \
  --plan "Реализовать аутентификацию через JWT" \
  --criteria "POST /auth принимает email/password, возвращает JWT" \
  --budget 5.0

orchestrator add-task T-002 \
  --plan "Добавить refresh-токены" \
  --criteria "POST /refresh возвращает новый JWT" \
  --depends-on T-001 \
  --budget 3.0

# 3. Объединение задач в стадию
orchestrator add-stage S1 "Аутентификация" \
  --task T-001 --task T-002 \
  --budget 10.0

# 4. Запуск
orchestrator run --auto-approve   # полностью автоматически
orchestrator run                  # с ручными контрольными точками

# 5. Наблюдение
orchestrator project              # обзор проекта
orchestrator stage S1             # детали стадии
orchestrator task T-001           # детали задачи
orchestrator cost                 # отчёт по расходам
orchestrator actions              # ожидающие действия
orchestrator log T-001            # история переходов задачи
```

## Архитектура

```
                    Архитектор (человек)
                         |
                   утверждает планы,
                   принимает стадии
                         |
              +----- Orchestrator -----+
              |                        |
         TaskRunner               TaskGraph
         (жизненный                (граф
          цикл задач)               зависимостей)
              |                        |
     +--------+--------+         StageFSM
     |                  |        (жизненный
DeveloperExecutor  AuditorExecutor  цикл стадий)
 (Claude Code)     (Claude Opus)
     |                  |
   пишет код      проверяет по
   и тесты        чек-листу
```

### Компоненты

| Компонент | Файл | Назначение |
|-----------|------|------------|
| Task FSM | `state_machine.py` | Конечный автомат жизненного цикла задачи (12 состояний) |
| Stage FSM | `stage.py` | Конечный автомат стадий проекта (6 состояний) |
| TaskGraph | `graph.py` | DAG зависимостей с обнаружением циклов |
| TaskRunner | `runner.py` | Оркестрация полного цикла задачи с вызовами исполнителей |
| ProjectOrchestrator | `project.py` | Координация стадий, графа и runner'а |
| RunLoop | `loop.py` | Основной асинхронный цикл выполнения |
| Store | `store.py` | SQLite-персистенция полного состояния |
| Evidence | `evidence.py` | Пакеты доказательств: дифф, тесты, вердикт аудита |
| CostTracker | `cost.py` | Учёт токенов и контроль бюджета |

## Жизненный цикл задачи

```
DRAFT -> PLAN_REVIEW -> PLAN_APPROVED -> IN_PROGRESS -> TESTING
                                                          |
                                          +---------- REWORK <-+
                                          |               |     |
                                          v               v     |
                                      AWAIT_AUDIT --------+-----+
                                          |
                              +-----------+-----------+
                              |                       |
                         (не критичный)          (критичный)
                              |                       |
                              v                       v
                          PR_READY               MUTATION
                              |                       |
                              |          (score >= 0.8)|
                              |<----------------------+
                              v
                           MERGED -> ACCEPTED

        Любое состояние (кроме терминальных) -> FAILED
```

### Защитные условия (guards)

| Переход | Условие |
|---------|---------|
| TESTING -> AWAIT_AUDIT | Все тесты зелёные |
| AWAIT_AUDIT -> PR_READY | Зелёные тесты И approve аудитора (INV-1) |
| AWAIT_AUDIT -> MUTATION | Код помечен как критичный + approve аудитора |
| MUTATION -> PR_READY | Mutation score >= 0.8 |
| REWORK -> IN_PROGRESS | Не превышено макс. количество попыток (по умолчанию 3) |
| MERGED -> ACCEPTED | Явное утверждение архитектором (INV-4) |

## Жизненный цикл стадии

```
PLANNING -> IN_PROGRESS -> INTEGRATING -> REVIEW -> ACCEPTED
                                |
                          (E2E тесты красные)
                                |
                                v
                           IN_PROGRESS (откат)

    Любое состояние (кроме терминальных) -> FAILED
```

| Переход | Условие |
|---------|---------|
| IN_PROGRESS -> INTEGRATING | Все задачи стадии завершены |
| INTEGRATING -> REVIEW | E2E тесты зелёные |
| REVIEW -> ACCEPTED | Утверждение архитектором (INV-4) |

## Бизнес-инварианты

| # | Инвариант | Механизм принуждения |
|---|-----------|---------------------|
| INV-1 | Задача не в PR_READY без зелёных тестов И approve аудитора | Guard в FSM (`state_machine.py`) |
| INV-2 | Разработчик не коммитит в main | Hook `branch_guard` через Agent SDK |
| INV-3 | Аудитор не видит рассуждения разработчика | `EvidencePack.to_auditor_input()` исключает логи |
| INV-4 | Стадия/задача не ACCEPTED без архитектора | Guard в FSM требует `architect_approved=True` |
| INV-5 | Бюджет не превышается | `CostTracker.record()` выбрасывает `BudgetExceededError` |

## CLI-команды

### Управление

| Команда | Описание |
|---------|----------|
| `orchestrator init` | Создать `.orchestrator/state.db` |
| `orchestrator add-task <id> [опции]` | Добавить задачу |
| `orchestrator add-stage <id> <name> [опции]` | Добавить стадию |
| `orchestrator run [--auto-approve]` | Запустить/продолжить цикл |
| `orchestrator approve-plan <id>` | Утвердить план задачи |
| `orchestrator reject-plan <id>` | Отклонить план задачи |
| `orchestrator accept-task <id>` | Принять выполненную задачу |
| `orchestrator accept-stage <id>` | Принять стадию |

### Наблюдение

| Команда | Описание |
|---------|----------|
| `orchestrator project` | Обзор проекта: стадии, количество задач, расходы |
| `orchestrator stage <id>` | Детали стадии: задачи, состояние, E2E-результаты |
| `orchestrator task <id>` | Детали задачи: состояние, evidence, расходы |
| `orchestrator cost` | Отчёт по расходам: по модели, задаче, стадии |
| `orchestrator actions` | Действия, ожидающие человека |
| `orchestrator log <id>` | История переходов состояний задачи |
| `orchestrator demo` | Демонстрация с примерными данными |

### Опции add-task

```
orchestrator add-task T-001 \
  --plan "Текст плана"              # план задачи
  --criteria "Критерии приёмки"     # критерии приёмки
  --stage S1                        # привязка к стадии
  --budget 5.0                      # бюджет в USD (по умолчанию 5.0)
  --critical                        # пометить как критичный код
  --depends-on T-000                # зависимость (можно указать несколько раз)
```

### Опции add-stage

```
orchestrator add-stage S1 "Название стадии" \
  --task T-001 --task T-002         # задачи стадии (можно несколько)
  --budget 15.0                     # бюджет стадии в USD (по умолчанию 100.0)
```

## Конфигурация

Файл `orchestrator.yaml` в корне целевого проекта:

```yaml
target_project:
  repo: .
  main_branch: main
  phase: prototype      # prototype | mvp | production

  docs:
    spec: docs/SPEC.md
    rules: docs/DEVELOPMENT_RULES.md
    agent: AGENT.md
    tasks: docs/TASKS.md

  commands:
    install: pip install -e ".[dev]"
    test: pytest --cov=myapp
    lint: ruff check src/ tests/
    types: mypy src/
    mutation: mutmut run

  critical_modules:
    - src/myapp/auth
    - src/myapp/payments

  budgets:
    per_task_usd: 5.0
    per_project_usd: 100.0

  models:
    developer: claude-sonnet-4-5    # модель разработчика
    auditor: claude-opus-4-6        # модель аудитора
```

## Структура проекта

```
ai-dev-orchestrator/
├── src/orchestrator/
│   ├── __init__.py              # публичный API, версия
│   ├── cli.py                   # Typer CLI (14 команд)
│   ├── config.py                # ProjectConfig из YAML
│   ├── types.py                 # перечисления: TaskState, StageState, ModelId
│   ├── state_machine.py         # TaskContext, TaskCycleFSM
│   ├── stage.py                 # StageContext, StageFSM
│   ├── graph.py                 # TaskNode, TaskGraph (DAG)
│   ├── runner.py                # TaskRunner (оркестрация с I/O)
│   ├── project.py               # ProjectOrchestrator
│   ├── loop.py                  # run_loop (основной цикл)
│   ├── store.py                 # SQLite-персистенция
│   ├── evidence.py              # EvidencePack, TestResults, AuditorVerdict
│   ├── cost.py                  # CostTracker, BudgetExceededError
│   ├── panel.py                 # view-датаклассы для визуализации
│   ├── render.py                # Rich-рендеринг CLI
│   └── executor/
│       ├── base.py              # ExecutorAdapter (протокол)
│       ├── developer.py         # DeveloperExecutor (Claude Code)
│       ├── auditor.py           # AuditorExecutor (Claude Opus)
│       └── hooks.py             # branch_guard, readonly_bash, tool_logger
├── tests/                       # 311 тестов, покрытие >= 60%
├── docs/
│   ├── TASKS.md
│   └── adr/                     # Architecture Decision Records
├── SPEC.md                      # что строим
├── DEVELOPMENT_RULES.md         # как работаем
├── AGENT.md                     # инструкции агентам
└── pyproject.toml
```

## Разработка

### Зависимости

```bash
pip install -e ".[dev,panel]"
```

### Проверки

```bash
ruff check src/ tests/            # линтер
ruff format --check src/ tests/   # формат
mypy src/                         # типизация
pytest --cov=orchestrator         # тесты с покрытием
```

### CI

GitHub Actions запускается на PR в `main` и push в `main`:
- Матрица: Python 3.11 + 3.12
- Шаги: ruff check -> ruff format -> mypy -> pytest (покрытие >= 60%)
- Branch protection: обязательные status checks, запрет force-push

## Лицензия

Проприетарный проект.
