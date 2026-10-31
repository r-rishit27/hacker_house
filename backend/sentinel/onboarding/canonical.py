"""Sentinel's canonical model: the few things an investigation needs from any bank.

A new bank is onboarded by mapping its own graph onto these concepts, relations
and attributes. The detectors never see a bank's names; they read canonical
transaction rows built through the mapping (adapter.py). What a bank cannot
supply is reported as a capability gap (capabilities.py) rather than guessed.
"""

from __future__ import annotations

CONCEPTS: dict[str, str] = {
    "Person": "A customer of this bank: an individual or organisation that owns accounts here.",
    "Account": "An account held at this bank (current, savings, card account). Money moves in and out of it.",
    "Transaction": "One completed movement of money involving an account here: a transfer, a payment execution, a "
                   "cash deposit or withdrawal, a card purchase. Not a payment order or request, not a batch, not a "
                   "ledger/statement posting derived from a transaction, not a return of an earlier transaction.",
    "Device": "The physical or software device a person used to act: a phone, browser fingerprint, terminal. "
              "A bank-issued card is a Card, not a Device.",
    "IPAddress": "The network address an action came from.",
    "LoginEvent": "One sign-on or authentication by a person through a channel.",
    "Beneficiary": "The party (or saved payee) that receives money and is not this bank's customer.",
    "Card": "A payment card issued by this bank on an account.",
    "Merchant": "A merchant where a card was used.",
    "InvestigationCase": "A case a person at the bank opened to investigate. A fraud score, alert flag, block code or "
                         "regulatory report is not a case.",
}

#: relation -> (from concept, to concept, meaning). Direction is the canonical one; a path may use reverse edges.
RELATIONS: dict[str, tuple[str, str, str]] = {
    "OWNS": ("Person", "Account", "the person owns or jointly holds the account"),
    "INITIATED": ("Account", "Transaction", "the account the money left (the debited account)"),
    "PAYS_TO_ACCOUNT": ("Transaction", "Account", "an account at this bank that received the money (the credited account)"),
    "PAYS": ("Transaction", "Beneficiary", "the external party that received the money"),
    "FROM_DEVICE": ("Transaction", "Device", "the device the transaction was made from"),
    "FROM_IP": ("Transaction", "IPAddress", "the network address the transaction was made from"),
    "AT": ("Transaction", "Merchant", "the merchant where a card transaction happened"),
    "VIA": ("Transaction", "Card", "the card used for the transaction"),
    "HOLDS": ("Account", "Card", "a card issued on the account"),
    "LOGGED_IN": ("LoginEvent", "Account", "an account reachable by the person who signed on"),
    "LOGIN_FROM_DEVICE": ("LoginEvent", "Device", "the device used to sign on"),
    "LOGIN_FROM_IP": ("LoginEvent", "IPAddress", "the address the sign-on came from"),
    "INVESTIGATED_IN": ("Account", "InvestigationCase", "a case concerning the account"),
}

ATTRIBUTES: dict[str, str] = {
    "Transaction.amount": "the amount of money moved, in settlement or account currency",
    "Transaction.timestamp": "when the transaction happened, with time of day if available",
    "Transaction.channel": "how it was made: online/internet, mobile, branch/teller cash, card, wire...",
    "Account.open_date": "when the account was opened",
    "Account.status": "whether the account is active, blocked, closed",
    "Account.kyc_score": "a numeric know-your-customer risk score for the account",
    "Person.anonymized_ssn_hash": "a hashed national identity number",
    "Person.kyc_verified_at": "when the person's identity was last verified",
    "Beneficiary.bank_identifier": "the beneficiary's bank: BIC, sort code, routing number",
    "Beneficiary.name_hash": "the hashed beneficiary name",
    "InvestigationCase.status": "open, closed...",
    "IPAddress.anonymizer": "whether the address is an anonymous proxy, VPN or Tor exit",
    "account_under_investigation": "a signal that the account is currently under investigation (case, flag, block)",
}

#: Value transforms the adapter knows how to apply, so a mapping never needs code.
TRANSFORMS = ("none", "datetime", "yyyymmdd", "yymmddhhmm", "yyyymmddhhmmss", "epoch_s", "epoch_ms", "yes_no", "code")

#: What each detector needs. A detector whose needs are unmapped is switched off for that bank, with the reason.
DETECTOR_NEEDS: dict[str, tuple[str, ...]] = {
    "structuring_under_threshold": ("Transaction", "INITIATED", "Transaction.amount", "Transaction.timestamp"),
    "escalating_charges": ("Transaction", "INITIATED", "Transaction.amount", "Transaction.timestamp"),
    "micro_charges": ("Transaction", "INITIATED", "Transaction.amount", "Transaction.timestamp"),
    "dormant_reactivation": ("Transaction", "INITIATED", "Transaction.timestamp"),
    "card_testing": ("Transaction", "INITIATED", "Transaction.amount", "Transaction.timestamp"),
    "recipient_concentration": ("Transaction", "INITIATED", "Transaction.timestamp", "PAYS|PAYS_TO_ACCOUNT"),
    "shared_device_ring": ("Transaction", "INITIATED", "Transaction.timestamp", "Device", "FROM_DEVICE|LOGIN_FROM_DEVICE"),
    "ring_device_returns": ("Transaction", "INITIATED", "Transaction.timestamp", "Device", "FROM_DEVICE|LOGIN_FROM_DEVICE",
                            "InvestigationCase|account_under_investigation"),
    "anonymous_proxy_signal": ("IPAddress", "FROM_IP|LOGIN_FROM_IP", "IPAddress.anonymizer"),
    "precedent_from_cases": ("InvestigationCase|account_under_investigation",),
}
