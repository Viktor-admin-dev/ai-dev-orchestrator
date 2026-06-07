# CLAUDE.md -- Контекст для AI-агентов

> Этот файл автоматически загружается Claude Code при работе с проектом.

## Что это за проект

**AI Dev Orchestrator** -- Python-приложение, управляющее AI-агентами для автоматизации полного цикла разработки: из ТЗ проекта до работающего продукта. Использует Claude Agent SDK для управления двумя независимыми агентами (разработчик + аудитор) с принудительным контролем качества.

## Стек

- Python >= 3.11
- Claude Agent SDK (хуки, субагенты, учёт токенов)
- Typer (CLI), Rich (рендеринг), PyYAML (конфиг)
- SQLite (персистенция состояния)
- pytest, ruff, mypy (качество кода)

## Иерархия документов

Приоритет при расхождениях:
1. **SPEC.md** -- ЧТО строим (требования, инварианты)
2. **DEVELOPMENT_RULES.md** -- КАК работаем (процесс, качество)
3. **AGENT.md** -- инструкции агентам
4. **docs/adr/** -- архитектурные решения

## Архитектура (ключевые файлы)

| Файл | Что делает |
|------|------------|
| `src/orchestrator/cli.py` | Точка входа: 14 Typer-команд |
| `src/orchestrator/state_machine.py` | FSM задачи: TaskContext, TaskCycleFSM, 12 состояний |
| `src/orchestrator/stage.py` | FSM стадии: StageContext, StageFSM, 6 состояний |
| `src/orchestrator/graph.py` | DAG зависимостей: TaskNode, TaskGraph |
| `src/orchestrator/runner.py` | TaskRunner -- оркестрация жизненного цикла задач |
| `src/orchestrator/project.py` | ProjectOrchestrator -- координация стадий + граф + runner |
| `src/orchestrator/loop.py` | `run_loop()` -- основной async-цикл |
| `src/orchestrator/store.py` | SQLite-персистенция (схема + save/load) |
| `src/orchestrator/evidence.py` | EvidencePack, TestResults, AuditorVerdict, MutationResults |
| `src/orchestrator/cost.py` | CostTracker с бюджетным контролем |
| `src/orchestrator/config.py` | ProjectConfig из YAML |
| `src/orchestrator/executor/base.py` | ExecutorAdapter -- протокол исполнителя |
| `src/orchestrator/executor/developer.py` | DeveloperExecutor (Claude Code) |
| `src/orchestrator/executor/auditor.py` | AuditorExecutor (Claude Opus) |
| `src/orchestrator/executor/hooks.py` | branch_guard, readonly_bash, tool_logger |

## Бизнес-инварианты (не нарушать!)

1. **INV-1:** Задача не в PR_READY без зелёных тестов И approve аудитора -- guard в `state_machine.py`
2. **INV-2:** Разработчик не коммитит в main -- hook `branch_guard`
3. **INV-3:** Аудитор не видит рассуждения разработчика -- `EvidencePack.to_auditor_input()`
4. **INV-4:** Стадия/задача не ACCEPTED без архитектора -- guard `architect_approved`
5. **INV-5:** Бюджет не превышается -- `CostTracker` выбрасывает `BudgetExceededError`

Каждый инвариант покрыт тестами. Изменение инварианта требует двухфазного коммита.

## Жизненный цикл задачи (12 состояний)

```
DRAFT -> PLAN_REVIEW -> PLAN_APPROVED -> IN_PROGRESS -> TESTING
  -> AWAIT_AUDIT -> PR_READY -> MERGED -> ACCEPTED
                  -> MUTATION -> PR_READY (для критичных модулей)
                  -> REWORK -> IN_PROGRESS (при неудаче, до 3 попыток)
  Любое -> FAILED (терминальное)
```

## Жизненный цикл стадии (6 состояний)

```
PLANNING -> IN_PROGRESS -> INTEGRATING -> REVIEW -> ACCEPTED
  Любое -> FAILED (терминальное)
```

## CLI-команды

```bash
orchestrator init                           # создать БД
orchestrator add-task T-001 [--plan/--criteria/--stage/--budget/--critical/--depends-on]
orchestrator add-stage S1 "Name" [--task/--budget]
orchestrator run [--auto-approve]           # запустить цикл
orchestrator approve-plan / reject-plan     # утвердить/отклонить план
orchestrator accept-task / accept-stage     # принять задачу/стадию
orchestrator project / stage / task / cost / actions / log / demo  # наблюдение
```

## Конвенции кода

- **Линтер:** `ruff check src/ tests/` -- строка <= 100 символов
- **Формат:** `ruff format` -- автоформатирование
- **Типизация:** `mypy --strict src/` -- строгая типизация
- **Тесты:** `pytest --cov=orchestrator --cov-fail-under=60`
- **Коммиты:** Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)
- **PR:** squash-merge в main, <= 400 строк (макс. 800)
- **Импорты:** lazy-импорты в CLI-командах (внутри функций), чтобы не грузить всё при старте

## Паттерны проекта

### FSM-подход

Вся бизнес-логика в чистых конечных автоматах без I/O:
- `TaskCycleFSM.can_transition(ctx, target)` -> `(bool, reason)`
- `TaskCycleFSM.transition(ctx, target, reason)` -> мутирует ctx, аппендит в history

### Персистенция

- БД: `.orchestrator/state.db` (SQLite, WAL mode)
- Таблицы: `tasks`, `task_transitions`, `stages`, `stage_transitions`, `graph_nodes`, `cost_entries`, `meta`
- Функции: `save_task()`, `save_stage()`, `save_graph_node()` -- upsert; `save_snapshot()` -- полный дамп
- Загрузка: `load_orchestrator()` реконструирует полный ProjectOrchestrator из БД

### Evidence-based аудит

- Разработчик собирает EvidencePack (дифф + тесты + логи)
- `to_auditor_input()` передаёт аудитору только дифф + критерии + тесты (INV-3)
- Вердикт аудитора парсится из текста: `parse_verdict(raw)` -> AuditorVerdict

### Протокольный адаптер

```python
class ExecutorAdapter(Protocol):
    async def execute(self, prompt: str, task_id: str) -> ExecutionResult: ...
```

Можно подставить любую реализацию (Claude, OpenAI, локальная модель).

### Hook-система (Agent SDK)

- `branch_guard` -- блокирует git push/checkout на main (INV-2)
- `readonly_bash` -- аудитор не может модифицировать файлы
- `tool_logger` -- логирует все вызовы инструментов

## Запуск проверок

```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/ && pytest
```

## CI

GitHub Actions: Python 3.11 + 3.12, ruff -> mypy -> pytest (покрытие >= 60%). Branch protection на main с обязательными status checks.

## Чего НЕ делать

- Не нарушать инварианты INV-1..INV-5
- Не коммитить в main напрямую (только PR через feature-ветку)
- Не отключать CI-проверки (`[skip ci]`, `--no-verify`)
- Не делать drive-by правок за пределами задачи
- Не менять публичные API без ADR
- Не глотать исключения (`except: pass`)
