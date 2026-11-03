"""Pydantic schemas for request validation and API responses."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


CriticalityType = Literal["Low", "Medium", "High"]
PriorityType = Literal["Low", "Medium", "High"]


class MachineCreate(BaseModel):
    name: str = Field(..., min_length=1, description="Machine name or identifier")
    location: str = Field(..., min_length=1, description="Physical plant floor or department")
    criticality: CriticalityType = Field(default="Medium", description="Operational criticality")


class MachineResponse(BaseModel):
    id: int
    name: str
    location: str
    criticality: CriticalityType


class TaskCreate(BaseModel):
    machine_id: int = Field(..., description="Target equipment ID")
    title: str = Field(..., min_length=1, description="Description of the maintenance task")
    technician: str = Field(..., min_length=1, description="Assigned technician or crew")
    due: str = Field(..., description="Due date in YYYY-MM-DD format")
    interval_days: int = Field(..., gt=0, description="Recurring interval in days")
    priority: PriorityType = Field(default="Medium", description="Maintenance priority")
    notes: Optional[str] = Field(default="", description="Initial notes")


class TaskComplete(BaseModel):
    notes: Optional[str] = Field(default="", description="Service observations or parts replaced")
    completed_on: Optional[str] = Field(default=None, description="Optional completion date override (YYYY-MM-DD)")


class TaskReschedule(BaseModel):
    due: str = Field(..., description="New due date in YYYY-MM-DD format")


class TaskResponse(BaseModel):
    id: int
    machine_id: int
    machine: str
    location: str
    machine_criticality: Optional[str] = None
    title: str
    technician: str
    due: str
    interval_days: int
    priority: PriorityType
    completed_on: Optional[str] = None
    notes: str
    status: str


class DashboardStats(BaseModel):
    total_machines: int
    high_criticality_machines: int
    open_tasks: int
    completed_tasks: int
    overdue: int
    due_today: int
    due_soon: int
    scheduled: int
    high_priority_open: int
    completion_rate: float
