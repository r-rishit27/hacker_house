"""The permission boundary: what a route is, and who may act without one.

This module is the API's half of ADR-6. The policy engine already routed every
recommendation when it wrote the answer file; this recomputes that route from
the same :class:`RoutingTable` on every read and every execute, so a client
cannot downgrade ``BLOCK_CARD`` to ``auto`` by sending a friendlier route, and
an answer file whose route drifted since it was written is detected rather than
obeyed. Without the recomputation the whole permission story would rest on a
string the caller supplied.

The second rule here is the one people get wrong: **no role executes an L1 or
an L2 action directly.** A fraud manager approves an L2; approving and doing
are different acts, they leave different rows, and collapsing them would make
the audit log unable to say who authorised a block.
"""

from __future__ import annotations

from api.errors import PermissionDenied
from api.principal import Principal
from sentinel.domain.enums import APPROVERS, Action, Role, Route
from sentinel.policy.routing import RoutingTable


class RoutePermissionPolicy:
    """Authorization over the auto / L1 / L2 table.

    Responsibility: turn (action, exposure) into a route, and answer whether
    this principal may execute at that route — nothing else. It holds no
    session, writes no row and has no opinion about fraud.
    Collaborators: ``RoutingTable``, which is the single source of every route;
    ``APPROVERS`` for who may sign off; ``api.errors.PermissionDenied``, which
    carries the route and the approving roles back to the console.
    """

    def __init__(self, routing: RoutingTable) -> None:
        # Injected rather than constructed: the table is configured with the
        # $2,500 BLOCK_CARD threshold, and an API that built its own would be
        # free to disagree with the engine that wrote the answer file.
        self._routing = routing

    def route_of(self, action: Action, exposure_usd: float = 0.0) -> Route:
        """The route for this action at this exposure, recomputed every time."""
        return self._routing.route_for(action, exposure_usd)

    def may_execute(self, principal: Principal, action: Action, exposure_usd: float = 0.0) -> bool:
        """Whether this principal may execute the action with no approval.

        True only when the recomputed route is ``auto`` — for every role,
        including a fraud manager. The role is deliberately not consulted: an
        L2 action is executed by the *system* once an approval exists, and the
        approval is a separate, recorded act. A fraud manager who could also
        execute directly would leave an audit trail in which half the blocks
        have no approval behind them.
        """
        return self.route_of(action, exposure_usd) is Route.AUTO

    def approvers_for(self, route: Route) -> tuple[Role, ...]:
        """The roles that may approve this route, from the one enum table."""
        return APPROVERS[route]

    def assert_may_execute(
        self, principal: Principal, action: Action, exposure_usd: float = 0.0
    ) -> Route:
        """The route, or a 403 that names who could approve it.

        Returns the route on success so the caller records the recomputed value
        rather than re-deriving it and risking a different answer.
        """
        route = self.route_of(action, exposure_usd)
        if route is Route.AUTO:
            return route
        raise PermissionDenied(
            action=action.value,
            required_route=route,
            role=principal.role,
            exposure_usd=exposure_usd,
        )
