"""The seven controllers, one per resource, and the app's route table.

Each is a :class:`~api.controller.RouterController`: a class that declares its
routes and binds its own bound methods. They are listed in :data:`CONTROLLERS`
in mount order, which is also the order they appear in the OpenAPI page — the
28 endpoints of TECHNICAL.md §6, under ``/api``.

Nothing here decides anything. A controller resolves the container, calls one
service and shapes the result into a model from ``api.schemas``; every fraud
judgement was made in ``sentinel`` and every permission answer in
``RoutePermissionPolicy``.
"""

from api.controllers.actions import ActionsController
from api.controllers.approvals import ApprovalsController
from api.controllers.benchmark import BenchmarkController
from api.controllers.cases import CasesController
from api.controllers.graph import GraphController
from api.controllers.investigations import InvestigationsController
from api.controllers.system import SystemController

#: Mount order. Static paths before parameterised ones, so ``/api/cases`` and
#: ``/api/benchmark/report`` can never be matched as somebody's ``{case_id}``.
CONTROLLERS = (
    SystemController,
    CasesController,
    InvestigationsController,
    ActionsController,
    ApprovalsController,
    GraphController,
    BenchmarkController,
)

__all__ = [
    "CONTROLLERS",
    "ActionsController",
    "ApprovalsController",
    "BenchmarkController",
    "CasesController",
    "GraphController",
    "InvestigationsController",
    "SystemController",
]
