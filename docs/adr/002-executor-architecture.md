# ADR-002: Executor Architecture — Protocol + Agent SDK

## Статус

Принят

## Контекст

Оркестратору нужен программный слой для управления AI-агентами (разработчик + аудитор). Требования:
- Разделение ролей: разработчик пишет код, аудитор проверяет (§1.1)
- Разные модели: developer на Sonnet, auditor на Opus
- Принуждение через хуки: branch_guard (INV-2), readonly_bash для аудитора
- INV-3: аудитор не видит рассуждения разработчика
- INV-5: контроль бюджета
- Возможность замены Claude на OpenAI Codex в будущем

## Решение

**Protocol, не ABC.** `ExecutorAdapter` определён как `typing.Protocol` с единственным методом `async execute(prompt, task_id) -> ExecutionResult`. Любой класс, реализующий этот метод, является валидным адаптером без наследования.

**Два исполнителя:**
- `DeveloperExecutor` — Claude Sonnet через `claude_agent_sdk.query()`, с `branch_guard` хуком и полным набором инструментов (Read, Edit, Write, Bash, Glob, Grep)
- `AuditorExecutor` — Claude Opus через `claude_agent_sdk.query()`, с `readonly_bash` хуком и ограниченным набором инструментов (Read, Glob, Grep, Bash read-only)

**Хуки как принуждение:**
- `branch_guard` (PreToolUse) — блокирует `git push/checkout` в main
- `readonly_bash` (PreToolUse) — блокирует деструктивные bash-команды для аудитора
- `tool_logger` (PostToolUse) — записывает все вызовы инструментов

**INV-3 enforcement:** `AuditorExecutor.audit()` принимает `EvidencePack` и использует `to_auditor_input()`, который строит промпт из diff + критерии + тесты, исключая developer_log.

**CostTracker** — отдельный компонент, warning при 80%, `BudgetExceededError` при 100%.

## Альтернативы

1. **ABC вместо Protocol** — отвергнуто: Protocol не требует наследования, упрощает подмену на OpenAI Codex и тестирование.
2. **Один исполнитель с параметром role** — отвергнуто: разные модели, инструменты, хуки и промпты делают общий класс неоправданно сложным.
3. **Прямые вызовы Anthropic API** — отвергнуто: Agent SDK даёт хуки, учёт токенов, субагентов из коробки (ADR-001).

## Последствия

**Плюсы:**
- Protocol позволяет добавить OpenAI Codex адаптер без изменения оркестратора
- Хуки обеспечивают принудительное соблюдение INV-2 и INV-3
- Тесты не требуют API-ключей (мокается `query()`)
- CostTracker отделён от исполнителей — можно использовать независимо

**Минусы:**
- Зависимость от claude-agent-sdk (pin ≥0.1)
- Хуки ограничены возможностями SDK (regex matching)
- Мокирование SDK в тестах требует знания внутренней структуры сообщений
