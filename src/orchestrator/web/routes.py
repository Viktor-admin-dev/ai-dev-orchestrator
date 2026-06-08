"""All web routes: pages, JSON API, and mutations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from orchestrator.web.serializers import view_to_dict

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from orchestrator.panel import PanelData
    from orchestrator.project import ProjectOrchestrator


def _load_orch(db_path: Path) -> ProjectOrchestrator:
    """Load orchestrator from DB with mock executors."""
    from unittest.mock import AsyncMock

    from orchestrator.config import ProjectConfig
    from orchestrator.executor.base import ExecutorAdapter
    from orchestrator.store import load_orchestrator

    config_path = Path("orchestrator.yaml")
    config = ProjectConfig.from_yaml(config_path) if config_path.exists() else ProjectConfig()
    developer = AsyncMock(spec=ExecutorAdapter)
    auditor = AsyncMock(spec=ExecutorAdapter)
    return load_orchestrator(db_path, config, developer, auditor)


def _panel_data(db_path: Path) -> PanelData:
    """Build PanelData from DB."""
    from orchestrator.panel import PanelData as _PanelData

    return _PanelData(_load_orch(db_path))


def build_router(db_path: Path, templates: Jinja2Templates) -> APIRouter:
    """Build the APIRouter with all routes bound to db_path."""
    router = APIRouter()

    # ── Page routes ──

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        if not db_path.exists():
            return templates.TemplateResponse(request, "dashboard.html", {"view": None})
        panel = _panel_data(db_path)
        view = panel.project_status()
        return templates.TemplateResponse(request, "dashboard.html", {"view": view_to_dict(view)})

    @router.get("/stage/{stage_id}", response_class=HTMLResponse)
    async def stage_page(request: Request, stage_id: str) -> HTMLResponse:
        panel = _panel_data(db_path)
        try:
            view = panel.stage_status(stage_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' not found") from None
        return templates.TemplateResponse(request, "stage.html", {"view": view_to_dict(view)})

    @router.get("/task/{task_id}", response_class=HTMLResponse)
    async def task_page(request: Request, task_id: str) -> HTMLResponse:
        panel = _panel_data(db_path)
        try:
            view = panel.task_status(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found") from None
        return templates.TemplateResponse(request, "task.html", {"view": view_to_dict(view)})

    @router.get("/cost", response_class=HTMLResponse)
    async def cost_page(request: Request) -> HTMLResponse:
        panel = _panel_data(db_path)
        view = panel.cost_report()
        return templates.TemplateResponse(request, "cost.html", {"view": view_to_dict(view)})

    @router.get("/actions", response_class=HTMLResponse)
    async def actions_page(request: Request) -> HTMLResponse:
        panel = _panel_data(db_path)
        actions = panel.pending_actions()
        return templates.TemplateResponse(
            request,
            "actions.html",
            {"actions": [view_to_dict(a) for a in actions]},
        )

    @router.get("/log/{task_id}", response_class=HTMLResponse)
    async def log_page(request: Request, task_id: str) -> HTMLResponse:
        panel = _panel_data(db_path)
        try:
            view = panel.task_log(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found") from None
        return templates.TemplateResponse(request, "log.html", {"view": view_to_dict(view)})

    # ── JSON API ──

    @router.get("/api/project")
    async def api_project() -> JSONResponse:
        panel = _panel_data(db_path)
        view = panel.project_status()
        return JSONResponse(view_to_dict(view))

    @router.get("/api/task/{task_id}")
    async def api_task(task_id: str) -> JSONResponse:
        panel = _panel_data(db_path)
        try:
            view = panel.task_status(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found") from None
        return JSONResponse(view_to_dict(view))

    @router.get("/api/actions")
    async def api_actions() -> JSONResponse:
        panel = _panel_data(db_path)
        actions = panel.pending_actions()
        return JSONResponse([view_to_dict(a) for a in actions])

    # ── Mutations ──

    @router.post("/approve-plan/{task_id}")
    async def approve_plan(task_id: str) -> RedirectResponse:
        from orchestrator.store import get_connection, save_task, save_task_transition

        orch = _load_orch(db_path)
        try:
            ctx = orch.runner.get_task(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found") from None

        prev_len = len(ctx.history)
        try:
            orch.runner.approve_plan(task_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        conn = get_connection(db_path)
        try:
            for t in ctx.history[prev_len:]:
                save_task_transition(conn, task_id, t)
            save_task(conn, ctx)
        finally:
            conn.close()

        return RedirectResponse("/actions", status_code=303)

    @router.post("/reject-plan/{task_id}")
    async def reject_plan(task_id: str) -> RedirectResponse:
        from orchestrator.store import get_connection, save_task, save_task_transition

        orch = _load_orch(db_path)
        try:
            ctx = orch.runner.get_task(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found") from None

        prev_len = len(ctx.history)
        try:
            orch.runner.reject_plan(task_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        conn = get_connection(db_path)
        try:
            for t in ctx.history[prev_len:]:
                save_task_transition(conn, task_id, t)
            save_task(conn, ctx)
        finally:
            conn.close()

        return RedirectResponse("/actions", status_code=303)

    @router.post("/accept-task/{task_id}")
    async def accept_task(task_id: str) -> RedirectResponse:
        from orchestrator.store import get_connection, save_task, save_task_transition

        orch = _load_orch(db_path)
        try:
            ctx = orch.runner.get_task(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found") from None

        prev_len = len(ctx.history)
        try:
            orch.runner.accept(task_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        conn = get_connection(db_path)
        try:
            for t in ctx.history[prev_len:]:
                save_task_transition(conn, task_id, t)
            save_task(conn, ctx)
        finally:
            conn.close()

        return RedirectResponse("/actions", status_code=303)

    @router.post("/accept-stage/{stage_id}")
    async def accept_stage(stage_id: str) -> RedirectResponse:
        from orchestrator.store import get_connection, save_stage, save_stage_transition

        orch = _load_orch(db_path)
        try:
            stage_ctx = orch.get_stage(stage_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' not found") from None

        prev_len = len(stage_ctx.history)
        try:
            orch.accept_stage(stage_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        conn = get_connection(db_path)
        try:
            for t in stage_ctx.history[prev_len:]:
                save_stage_transition(conn, stage_id, t)
            save_stage(conn, stage_ctx)
        finally:
            conn.close()

        return RedirectResponse("/actions", status_code=303)

    @router.post("/add-task")
    async def add_task_form(
        task_id: str = Form(...),
        plan: str = Form(""),
        criteria: str = Form(""),
        stage: str = Form(""),
        budget: float = Form(5.0),
        critical: bool = Form(False),
    ) -> RedirectResponse:
        from orchestrator.graph import TaskNode
        from orchestrator.store import get_connection, save_graph_node, save_task

        orch = _load_orch(db_path)
        if task_id in orch.runner.task_ids:
            raise HTTPException(status_code=400, detail=f"Task '{task_id}' already exists")

        ctx = orch.runner.create_task(task_id, budget_usd=budget, touches_critical=critical)
        if plan:
            ctx.plan = plan
        if criteria:
            ctx.criteria = criteria

        node = TaskNode(task_id=task_id, stage_id=stage)
        orch.graph.add_task(node)

        conn = get_connection(db_path)
        try:
            save_task(conn, ctx)
            save_graph_node(conn, node)
        finally:
            conn.close()

        return RedirectResponse("/", status_code=303)

    @router.post("/add-stage")
    async def add_stage_form(
        stage_id: str = Form(...),
        name: str = Form(...),
        budget: float = Form(100.0),
    ) -> RedirectResponse:
        from orchestrator.store import get_connection, save_stage

        orch = _load_orch(db_path)
        if stage_id in orch.stage_ids:
            raise HTTPException(status_code=400, detail=f"Stage '{stage_id}' already exists")

        stage_ctx = orch.create_stage(stage_id, name, [], budget_usd=budget)

        conn = get_connection(db_path)
        try:
            save_stage(conn, stage_ctx)
        finally:
            conn.close()

        return RedirectResponse("/", status_code=303)

    return router
