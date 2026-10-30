"""The tunable numbers of the Fraud Policy, in one frozen object.

Collaborators: ``RoutingTable``, ``SarPolicy``, ``StoppingPolicy`` and
``PolicyEngine`` all read their thresholds from an injected instance, so a
threshold exists exactly once and a test can move one without patching a module
global. Every value is transcribed from ``Guide.md`` sections 3, 3a and 6.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    """The six policy thresholds plus the three stopping constants.

    Responsibility: hold the numbers, nothing else. No method here decides
    anything — the decision classes do, so that a threshold can be read off this
    object and shown in the UI beside the rule that used it.
    """

    #: R-routing. Exposure at or below this routes ``BLOCK_CARD`` to ``L1``.
    #: The boundary is inclusive of L1: 2500.00 is L1, 2500.01 is L2.
    block_card_l2_threshold: float = 2500.0

    #: Section 3a. Exposure must *exceed* this for the first SAR trigger.
    sar_exposure_threshold: float = 1000.0

    #: Section 3a. A case is opened once fraud is this plausible.
    case_probability_threshold: float = 0.30

    #: R1. Below this, a single-signal case must be verified before any block.
    verify_before_block_threshold: float = 0.70

    #: R8. Uncertain plus exposure above this escalates.
    escalate_exposure_threshold: float = 500.0

    #: R4. No reply plus exposure above this escalates.
    r4_escalate_threshold: float = 500.0

    #: Policy 6. Stop above this with corroboration.
    stop_high: float = 0.85

    #: Policy 6. Stop below this with corroboration.
    stop_low: float = 0.15

    #: Policy 6's "at least two independent pieces of evidence".
    min_independent_support: int = 2


#: The policy as published. Callers that have no reason to vary a threshold
#: share this instance rather than each building their own.
DEFAULT_POLICY_CONFIG = PolicyConfig()
