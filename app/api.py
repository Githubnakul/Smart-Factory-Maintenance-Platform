"""FastAPI application providing clean RESTful endpoints for the scheduler."""

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.models import (
    DashboardStats,
    MachineCreate,
    MachineResponse,
    TaskCreate,
    TaskComplete,
    TaskReschedule,
    TaskResponse,
)
from app.store import Store, parse_date, status_for


def create_app(db_path: Optional[str | Path] = None) -> FastAPI:
    """Factory creating FastAPI application configured with SQLite Store."""
    if db_path is None:
        db_path = Path(__file__).resolve().parent.parent / "factory.db"

    store = Store(db_path)

    @asynccontextmanager
    async def lifespan(app_inst: FastAPI):
        yield
        store.close()

    app = FastAPI(
        title="Smart Factory Maintenance Scheduler",
        version="2.0.0",
        description="Vercel-inspired preventive maintenance platform with recurring cycles.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach store to state for lifecycle management
    app.state.store = store

    api = APIRouter(prefix="/api")

    def get_store() -> Store:
        return app.state.store

    @api.get("/health")
    def health_check():
        return {"status": "healthy", "engine": "sqlite", "service": "maintenance-scheduler"}

    @api.get("/stats", response_model=DashboardStats)
    def stats(db: Store = Depends(get_store)):
        return db.get_stats()

    @api.get("/machines", response_model=List[MachineResponse])
    def list_machines(db: Store = Depends(get_store)):
        rows = db.machines()
        return [
            MachineResponse(
                id=r["id"],
                name=r["name"],
                location=r["location"],
                criticality=r["criticality"],
            )
            for r in rows
        ]

    @api.post("/machines", response_model=MachineResponse, status_code=status.HTTP_201_CREATED)
    def add_machine(payload: MachineCreate, db: Store = Depends(get_store)):
        try:
            mid = db.add_machine(payload.name, payload.location, payload.criticality)
            return MachineResponse(
                id=mid,
                name=payload.name.strip(),
                location=payload.location.strip(),
                criticality=payload.criticality,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.delete("/machines/{machine_id}")
    def delete_machine(machine_id: int, db: Store = Depends(get_store)):
        success = db.delete_machine(machine_id)
        if not success:
            raise HTTPException(status_code=404, detail="Machine not found")
        return {"message": "Machine deleted successfully", "id": machine_id}

    @api.get("/tasks", response_model=List[TaskResponse])
    def list_tasks(
        status_filter: Optional[str] = Query(None, alias="status"),
        priority_filter: Optional[str] = Query(None, alias="priority"),
        machine_id: Optional[int] = Query(None),
        search: Optional[str] = Query(None),
        db: Store = Depends(get_store),
    ):
        rows = db.tasks(completed=False)
        results = []
        search_query = (search or "").strip().lower()

        for r in rows:
            st = status_for(r["due"])
            if status_filter and status_filter.lower() != "all" and st.lower() != status_filter.lower():
                continue
            if priority_filter and priority_filter.lower() != "all" and r["priority"].lower() != priority_filter.lower():
                continue
            if machine_id is not None and r["machine_id"] != machine_id:
                continue
            if search_query:
                combined = f"{r['title']} {r['machine']} {r['technician']} {r['location']}".lower()
                if search_query not in combined:
                    continue

            results.append(
                TaskResponse(
                    id=r["id"],
                    machine_id=r["machine_id"],
                    machine=r["machine"],
                    location=r["location"],
                    machine_criticality=r["machine_criticality"],
                    title=r["title"],
                    technician=r["technician"],
                    due=r["due"],
                    interval_days=r["interval_days"],
                    priority=r["priority"],
                    completed_on=r["completed_on"],
                    notes=r["notes"],
                    status=st,
                )
            )
        return results

    @api.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
    def create_task(payload: TaskCreate, db: Store = Depends(get_store)):
        try:
            tid = db.add_task(
                machine_id=payload.machine_id,
                title=payload.title,
                technician=payload.technician,
                due=payload.due,
                interval=payload.interval_days,
                priority=payload.priority,
                notes=payload.notes or "",
            )
            task = db.get_task(tid)
            if not task:
                raise HTTPException(status_code=500, detail="Failed to retrieve created task")
            return TaskResponse(
                id=task["id"],
                machine_id=task["machine_id"],
                machine=task["machine"],
                location=task["location"],
                machine_criticality=task["machine_criticality"],
                title=task["title"],
                technician=task["technician"],
                due=task["due"],
                interval_days=task["interval_days"],
                priority=task["priority"],
                completed_on=task["completed_on"],
                notes=task["notes"],
                status=status_for(task["due"]),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/tasks/{task_id}/complete")
    def complete_task(task_id: int, payload: TaskComplete, db: Store = Depends(get_store)):
        try:
            comp_date = parse_date(payload.completed_on) if payload.completed_on else None
            next_due = db.complete(task_id, notes=payload.notes or "", today=comp_date)
            return {
                "message": "Task marked as completed and next occurrence created",
                "task_id": task_id,
                "next_due": next_due,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/tasks/{task_id}/reschedule")
    def reschedule_task(task_id: int, payload: TaskReschedule, db: Store = Depends(get_store)):
        try:
            new_due = db.reschedule(task_id, payload.due)
            return {"message": "Task rescheduled successfully", "task_id": task_id, "due": new_due}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.delete("/tasks/{task_id}")
    def delete_task(task_id: int, db: Store = Depends(get_store)):
        success = db.delete_task(task_id)
        if not success:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"message": "Task deleted successfully", "id": task_id}

    @api.get("/history", response_model=List[TaskResponse])
    def list_history(
        search: Optional[str] = Query(None),
        db: Store = Depends(get_store),
    ):
        rows = db.tasks(completed=True)
        results = []
        search_query = (search or "").strip().lower()

        for r in rows:
            if search_query:
                combined = f"{r['title']} {r['machine']} {r['technician']} {r['location']} {r['notes']}".lower()
                if search_query not in combined:
                    continue

            results.append(
                TaskResponse(
                    id=r["id"],
                    machine_id=r["machine_id"],
                    machine=r["machine"],
                    location=r["location"],
                    machine_criticality=r["machine_criticality"],
                    title=r["title"],
                    technician=r["technician"],
                    due=r["due"],
                    interval_days=r["interval_days"],
                    priority=r["priority"],
                    completed_on=r["completed_on"],
                    notes=r["notes"],
                    status="Completed",
                )
            )
        return results

    @api.post("/demo")
    def load_demo(db: Store = Depends(get_store)):
        try:
            db.seed_demo()
            return {"message": "Demo factory equipment and maintenance schedules loaded successfully"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.delete("/demo")
    def reset_demo(db: Store = Depends(get_store)):
        db.reset_database()
        return {"message": "Database reset. All equipment and tasks removed."}

    @api.get("/export")
    def export_data(dataset: str = Query("tasks", pattern="^(tasks|history|machines)$"), db: Store = Depends(get_store)):
        csv_content = db.export_csv(dataset)
        filename = f"factory_{dataset}_{date.today().isoformat()}.csv"
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    app.include_router(api)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def index_page():
            index_path = static_dir / "index.html"
            if index_path.exists():
                return FileResponse(str(index_path))
            return PlainTextResponse("Static index.html not found.")

    return app
