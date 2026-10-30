"""The approval routing table: which actions an agent may execute alone.

Collaborators: ``PolicyConfig`` for the one threshold that matters, the
``Action``/``Route`` vocabularies from ``sentinel.domain.enums``, and every
caller that needs a route — ``RecommendationSet``, ``RouteRecomputeGate``, the
answer assembler and the API's permission check. It is the only place a route is
computed; a route written by hand anywhere else is a bug.
"""

from __future__ import annotations

from sentinel.domain.enums import FIXED_ROUTES, Action, Route
from sentinel.domain.errors import NoRouteDefined
from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig


class RoutingTable:
    """Maps an action plus exposure to its approval route.

    Thirteen of the fourteen actions have a fixed route, held in
    ``FIXED_ROUTES``. ``BLOCK_CARD`` is the only exposure-dependent action in the
    whole policy, which is why it is absent from that table and handled here.
    """

    def __init__(self, config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> None:
        self._config = config

    def route_for(self, action: Action | str, exposure_usd: float = 0.0) -> Route:
        """The route for one action.

        Raises ``NoRouteDefined`` for anything outside the fourteen, rather than
        guessing ``auto`` — an invented action name that silently routed itself
        past approval is the worst failure this table can have.
        """
        known = self._as_action(action)
        fixed = FIXED_ROUTES.get(known)
        if fixed is not None:
            return fixed
        if known is Action.BLOCK_CARD:
            # Inclusive of L1: exactly $2,500.00 is L1, $2,500.01 is L2.
            return Route.L1 if exposure_usd <= self._config.block_card_l2_threshold else Route.L2
        raise NoRouteDefined(f"no route defined for {known.value}", action=known.value)

    def fixed_routes(self) -> dict[Action, Route]:
        """The exposure-independent entries, copied so callers cannot edit them."""
        return dict(FIXED_ROUTES)

    @staticmethod
    def _as_action(action: Action | str) -> Action:
        if isinstance(action, Action):
            return action
        try:
            return Action(action)
        except ValueError as exc:
            raise NoRouteDefined(
                f"{action!r} is not one of the 14 policy actions", action=str(action)
            ) from exc
