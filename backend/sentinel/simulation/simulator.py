"""The grounded answer to a requested evidence round.

The brief does not supply customer or analyst replies: *"Simulate them in your
own system and state the assumption you made."* That makes this class the single
point on which a whole graded category rests — ``next_best_actions.initial``
differs from ``.final`` only because of what happens here.

It would be trivially easy, and worthless, to pick the branch that justifies a
conclusion already reached. So the branch is a function of measured quantities,
every branch names the queries it rests on, and the model writes only the English
sentence from that basis. A simulator that reasons backwards from the verdict
makes the whole initial-versus-final story circular, and a judge reading twenty
case files will see it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sentinel.domain.enums import CustomerResponse, EvidenceSource, RequestType
from sentinel.tools.dto import (
    CustomerCaseHistory,
    DeviceNovelty,
    RecurringChargeProbe,
    RegionNovelty,
)

#: How far a posted response may move the probability, in log-odds. The ledger
#: caps the ``response`` group at 1.8, so a simulated answer can be decisive but
#: cannot carry a case from 0.2 to 0.95 on its own.
DENIAL_LOG_LR = 1.4
CONFIRMATION_LOG_LR = -1.4
STEP_UP_PASS_LOG_LR = -1.1
STEP_UP_FAIL_LOG_LR = 1.1

#: A cardholder who has said "not mine" this many times and been right each time
#: is evidence about this denial too.
RELIABLE_DENIER_CASES = 3

#: Three distinct months of the same charge is the floor for "monthly".
RECURRING_MONTHS = 3


@dataclass(frozen=True, slots=True)
class SimulatedResponse:
    """What the request came back with, and what that rests on."""

    request_type: RequestType
    branch: CustomerResponse
    #: The sentence that lands verbatim in ``evidence_requests[].assumed_response``.
    assumed_response: str
    #: The query citations the branch was chosen from. Never a guess.
    assumption_basis: tuple[str, ...]
    log_lr: float
    claim: str

    @property
    def source(self) -> EvidenceSource:
        return EvidenceSource.CUSTOMER


@dataclass
class EvidenceSimulator:
    """Chooses the response branch from graph facts, with its basis recorded.

    Responsibility: the branch, the likelihood it carries, and the citations it
    rests on. It is pure — no model, no I/O, no randomness — so the same facts
    always produce the same branch and two runs of the benchmark agree.
    Collaborators: the caller posts the result through
    ``EvidenceLedger.post_judgement``; the assembler copies ``assumed_response``
    into the answer file.
    """

    recurring: RecurringChargeProbe | None = None
    region: RegionNovelty | None = None
    history: CustomerCaseHistory | None = None
    device: DeviceNovelty | None = None
    refs: dict[str, str] = field(default_factory=dict)
    #: True when the alert itself is the cardholder saying "I never made this".
    #: They cannot then confirm the charge when asked to validate it — a
    #: simulator that lets them is writing a reply the cardholder would not
    #: give, on the one field the brief asks us to state an assumption for.
    already_denied: bool = False

    def simulate(self, request_type: RequestType) -> SimulatedResponse:
        if request_type is RequestType.STEP_UP_AUTH:
            return self._step_up()
        return self._customer_reply(request_type)

    # ── customer validation and analyst information ──────────────────────────

    def _customer_reply(self, request_type: RequestType) -> SimulatedResponse:
        confirm_basis = self._confirmation_basis()
        deny_basis = self._denial_basis()

        # Both sides can hold at once — a reliable denier disputing their own
        # subscription is a real case. Neither wins, and that is the honest answer.
        if confirm_basis and deny_basis:
            return self._no_reply(request_type, confirm_basis + deny_basis, contested=True)
        analyst = request_type is RequestType.ANALYST_INFO
        if confirm_basis:
            if self.already_denied and not analyst:
                # The cardholder opened this case saying they did not make the
                # purchase. They cannot then confirm it. Where the facts say
                # otherwise the honest record is a contest, not a change of
                # story — and a contest is exactly what R7 exists for.
                return self._no_reply(request_type, confirm_basis, contested=True)
            return SimulatedResponse(
                request_type=request_type,
                branch=CustomerResponse.CONFIRMED,
                assumed_response=(
                    "Analyst review finds the charge consistent with the cardholder's own "
                    "established pattern on this card."
                    if analyst
                    else "Customer confirms they made the transaction and recognises the charge."
                ),
                assumption_basis=confirm_basis,
                log_lr=CONFIRMATION_LOG_LR,
                claim=(
                    "An analyst reviewing the card's history found the charge consistent "
                    "with the cardholder's own established pattern."
                    if analyst
                    else "The cardholder confirmed the transaction when asked to validate it."
                ),
            )
        if deny_basis:
            return SimulatedResponse(
                request_type=request_type,
                branch=CustomerResponse.DENIED,
                assumed_response=(
                    "Analyst review finds nothing in the card's history that accounts for "
                    "this transaction."
                    if analyst
                    else "Customer states they did not make this transaction and still "
                    "holds the card."
                ),
                assumption_basis=deny_basis,
                log_lr=DENIAL_LOG_LR,
                claim=(
                    "An analyst reviewing the card's history found nothing that accounts "
                    "for this transaction."
                    if analyst
                    else "The cardholder denied the transaction when asked to validate it."
                ),
            )
        return self._no_reply(request_type, (), contested=False)

    def _confirmation_basis(self) -> tuple[str, ...]:
        """Facts that make "yes, that was me" the grounded answer."""
        basis: list[str] = []
        if self.recurring is not None and self.recurring.distinct_months >= RECURRING_MONTHS:
            basis.append(self._ref("recurring_charge_probe"))
        if (
            self.region is not None
            and self.region.prior_txns_in_region >= 10
            and self.region.in_person_elsewhere_24h == 0
        ):
            basis.append(self._ref("region_novelty"))
        return tuple(b for b in basis if b)

    def _denial_basis(self) -> tuple[str, ...]:
        """Facts that make "no, that was not me" the grounded answer."""
        basis: list[str] = []
        if (
            self.history is not None
            and self.history.customer_reports_confirmed_fraud >= RELIABLE_DENIER_CASES
        ):
            basis.append(self._ref("customer_case_history"))
        if self._incriminating_cluster():
            basis.append(self._ref("device_novelty"))
        return tuple(b for b in basis if b)

    def _incriminating_cluster(self) -> bool:
        """A new device on a novel region is a cluster, not one signal."""
        if self.device is None or self.region is None:
            return False
        flags = self.device.flags
        if flags is None or not flags.device_key:
            return False
        novel_device = flags.device_new == "New" or (
            self.device.prior_txns_this_device_on_card == 0
        )
        novel_region = self.region.prior_txns_in_region == 0
        return novel_device and novel_region

    def _no_reply(
        self, request_type: RequestType, basis: tuple[str, ...], *, contested: bool
    ) -> SimulatedResponse:
        """Nobody answered — the honest outcome for a genuinely ambiguous case.

        This routes into R4, which is a real policy path rather than a fallback:
        monitor, decline any pending authorisation, escalate above $500.
        """
        return SimulatedResponse(
            request_type=request_type,
            branch=CustomerResponse.NO_REPLY,
            assumed_response=(
                "No reply from the customer within 24 hours."
                + (
                    " The graph supports both readings, so no answer is assumed."
                    if contested
                    else " The evidence supports neither reading strongly enough to assume one."
                )
            ),
            assumption_basis=basis,
            log_lr=0.0,
            claim="The cardholder did not respond within 24 hours (R4).",
        )

    # ── step-up authentication ───────────────────────────────────────────────

    def _step_up(self) -> SimulatedResponse:
        """A challenge the real cardholder passes and a stolen number does not.

        Three branches, not two. ``prior_txns_this_device_on_card == 0`` means
        one of two very different things: this card has never used this device,
        or *there is no identity record at all* — true of seven of the twenty
        alerts, where the transaction was card-present and Vesta captured no
        device. Reading the second as the first invents a failed challenge
        worth +1.1 log-odds on a card the graph says nothing about, which on a
        case already near 0.84 carries it past ``stop_high`` and turns the
        verdict to ``fraud`` on evidence that does not exist.

        ``DeviceNovelty.observable`` is the distinction, and it exists for this.
        """
        if self.device is None or not self.device.observable:
            return SimulatedResponse(
                request_type=RequestType.STEP_UP_AUTH,
                branch=CustomerResponse.NO_REPLY,
                assumed_response=(
                    "Step-up could not be evaluated: the transaction carries no identity "
                    "record, so there is no device to recognise or challenge."
                ),
                assumption_basis=tuple(b for b in (self._ref("device_novelty"),) if b),
                log_lr=0.0,
                claim=(
                    "No identity record exists for this transaction, so a step-up "
                    "challenge says nothing either way."
                ),
            )
        known_device = self.device.prior_txns_this_device_on_card > 0
        if known_device:
            return SimulatedResponse(
                request_type=RequestType.STEP_UP_AUTH,
                branch=CustomerResponse.STEP_UP_PASSED,
                assumed_response=(
                    "Step-up challenge passed: the one-time passcode was entered correctly."
                ),
                assumption_basis=tuple(b for b in (self._ref("device_novelty"),) if b),
                log_lr=STEP_UP_PASS_LOG_LR,
                claim="A step-up authentication challenge was passed on a device this card knows.",
            )
        return SimulatedResponse(
            request_type=RequestType.STEP_UP_AUTH,
            branch=CustomerResponse.STEP_UP_FAILED,
            assumed_response=(
                "Step-up challenge not completed: no response to the one-time passcode."
            ),
            assumption_basis=tuple(b for b in (self._ref("device_novelty"),) if b),
            log_lr=STEP_UP_FAIL_LOG_LR,
            claim="A step-up authentication challenge went uncompleted on an unrecognised device.",
        )

    def _ref(self, tool: str) -> str:
        """The citation recorded for this tool during the sweep, if it ran."""
        return self.refs.get(tool, "")
