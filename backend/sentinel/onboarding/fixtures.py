"""One fraud story, written three times in three banks' own schemas.

Six customers with ordinary history; account 0 runs a structuring burst (three
online payments just under $500 within 40 minutes) and accounts 1 and 2 run the
same burst in the same fortnight. Where the bank records devices and IP
addresses (BIAN), a new device behind Tor is adopted by four accounts within a
week. Each bank also carries its schema's traps — ledger postings, payment
orders, batches, returns — so a mapping that picks the wrong thing double-counts
or misses, and the end-to-end test notices.

Deterministic: no randomness, so a test failure is the mapping's, not the data's.
"""

from __future__ import annotations

from datetime import datetime, timedelta

START = datetime(2026, 8, 1, 9, 0)
AS_OF = START + timedelta(days=61)
N = 6
BURST = (455.10, 472.40, 489.90)
#: account index -> start of its structuring burst
BURSTS = {0: START + timedelta(days=60, hours=5), 1: START + timedelta(days=52, hours=3), 2: START + timedelta(days=55, hours=8)}
RING = (0, 3, 4, 5)  # BIAN: accounts that adopt the Tor device
RING_DAY = START + timedelta(days=57)


def _history():
    """(account index, when, amount, payee index) for ordinary activity, then the bursts."""
    rows = []
    for a in range(N):
        for d in range(0, 50, 3):
            rows.append((a, START + timedelta(days=d, hours=a), round(20 + (a * 13 + d * 7) % 130 + 0.45, 2), (a + d) % 4, "hist"))
    for a, when in BURSTS.items():
        for k, amt in enumerate(BURST):
            rows.append((a, when + timedelta(minutes=13 * k + a), amt, 9, "burst"))
    for k, a in enumerate(RING):
        rows.append((a, RING_DAY + timedelta(hours=7 * k), round(61.5 + k, 2), 8, "ring"))
    return rows


def iso20022() -> dict:
    v = {t: {} for t in ("Pty", "CshAcct", "BranchAndFinInstnId", "GrpHdr", "PmtInf", "CdtTrfTxInf", "TxInf")}
    e = []
    v["BranchAndFinInstnId"]["AGT-HOME"] = {"FinInstnId_BICFI": "HOMEGB2L"}
    v["BranchAndFinInstnId"]["AGT-EXT"] = {"FinInstnId_BICFI": "EXTRDEFF"}
    for a in range(N):
        v["Pty"][f"P{a}"] = {"Nm_Hash": f"h{a}", "IsCstmr": True}
        v["CshAcct"][f"A{a}"] = {"Id_IBAN": f"GB00HOME{a:08d}", "OpngDt": datetime(2024, 1, 1 + a), "Sts": "ENAB", "AMLAlrtInd": False}
        e += [["CshAcct_Ownr", "Pty", f"P{a}", "CshAcct", f"A{a}"], ["CshAcct_Svcr", "CshAcct", f"A{a}", "BranchAndFinInstnId", "AGT-HOME"]]
    for p in (*range(4), 8, 9):
        v["Pty"][f"X{p}"] = {"Nm_Hash": f"payee{p}", "IsCstmr": False}
    for n, (a, when, amt, payee, _) in enumerate(_history()):
        tx, hdr, order = f"UETR-{n:04d}", f"MSG-{n:04d}", f"PMTINF-{n:04d}"
        v["GrpHdr"][hdr] = {"MsgDefIdr": "pacs.008.001.08", "CreDtTm": when - timedelta(seconds=30), "NbOfTxs": 1}
        v["PmtInf"][order] = {"PmtMtd": "TRF", "ReqdExctnDt": when, "CtrlSum": amt * 10, "NbOfTxs": 10}  # a batch: trap
        v["CdtTrfTxInf"][tx] = {"IntrBkSttlmAmt": amt, "IntrBkSttlmAmt_Ccy": "GBP", "AccptncDtTm": when, "InstdAmt": amt * 1.17,
                                "PmtTpInf_SvcLvl_Cd": "SEPA", "PmtTpInf_LclInstrm_Cd": "INST"}
        e += [["CdtTrfTxInf_GrpHdr", "CdtTrfTxInf", tx, "GrpHdr", hdr], ["PmtInf_CdtTrfTxInf", "PmtInf", order, "CdtTrfTxInf", tx],
              ["PmtInf_DbtrAcct", "PmtInf", order, "CshAcct", f"A{a}"],
              ["CdtTrfTxInf_Dbtr", "CdtTrfTxInf", tx, "Pty", f"P{a}"], ["CdtTrfTxInf_DbtrAcct", "CdtTrfTxInf", tx, "CshAcct", f"A{a}"],
              ["CdtTrfTxInf_UltmtDbtr", "CdtTrfTxInf", tx, "Pty", "X0"],  # ultimate debtor: trap
              ["CdtTrfTxInf_Cdtr", "CdtTrfTxInf", tx, "Pty", f"X{payee}"], ["CdtTrfTxInf_CdtrAgt", "CdtTrfTxInf", tx, "BranchAndFinInstnId", "AGT-EXT"]]
    v["TxInf"]["RTR-1"] = {"OrgnlUETR": "UETR-0000", "RtrdIntrBkSttlmAmt": 480.0, "IntrBkSttlmDt": BURSTS[0]}  # a return: trap
    e.append(["TxInf_OrgnlTx", "TxInf", "RTR-1", "CdtTrfTxInf", "UETR-0000"])
    return {"vertices": v, "edges": e}


def bian() -> dict:
    v = {t: {} for t in ("PartyReferenceDataDirectoryEntry", "CustomerRelationshipManagementPlan", "CurrentAccountFacility",
                         "IssuedDeviceAllocation", "PaymentOrderProcedure", "PaymentExecutionProcedure", "PartyAuthenticationAssessment",
                         "AccessDevice", "NetworkAddress", "FraudDiagnosisAssessment", "CustomerCaseProcedure")}
    e = []
    v["AccessDevice"]["DEV-TOR"] = {"deviceFingerprint": "fp-tor", "deviceTrustLevel": "Unknown"}
    v["NetworkAddress"]["185.220.101.7"] = {"ipGeolocationCountry": "NL", "ipAnonymizerType": "TOR"}
    for a in range(N):
        v["PartyReferenceDataDirectoryEntry"][f"PRD-{a}"] = {"partyType": "Individual", "partyLegalNameHash": f"h{a}"}
        v["CustomerRelationshipManagementPlan"][f"CRM-{a}"] = {"customerRelationshipRole": "Owner"}
        v["CurrentAccountFacility"][f"CAF-{a}"] = {"accountIdentificationIBAN": f"GB00BIAN{a:08d}", "facilityStatus": "Active",
                                                   "facilityStartDate": datetime(2024, 2, 1 + a)}
        v["IssuedDeviceAllocation"][f"CARD-{a}"] = {"issuedDeviceType": "DebitCard", "cardScheme": "VISA"}  # a card named "device": trap
        v["AccessDevice"][f"DEV-{a}"] = {"deviceFingerprint": f"fp-{a}", "deviceTrustLevel": "Trusted"}
        v["NetworkAddress"][f"81.2.69.{a}"] = {"ipGeolocationCountry": "GB", "ipAnonymizerType": "None"}
        e += [["hasRelationshipPlan", "PartyReferenceDataDirectoryEntry", f"PRD-{a}", "CustomerRelationshipManagementPlan", f"CRM-{a}"],
              ["planCoversFacility", "CustomerRelationshipManagementPlan", f"CRM-{a}", "CurrentAccountFacility", f"CAF-{a}"],
              ["issuedDeviceOnFacility", "IssuedDeviceAllocation", f"CARD-{a}", "CurrentAccountFacility", f"CAF-{a}"]]
    for p in (*range(4), 8, 9):
        v["PartyReferenceDataDirectoryEntry"][f"CPY-{p}"] = {"partyType": "Counterparty", "partyLegalNameHash": f"payee{p}"}
    for n, (a, when, amt, payee, kind) in enumerate(_history()):
        order, exe, auth = f"PO-{n:04d}", f"PE-{n:04d}", f"AUTH-{n:04d}"
        tor = kind == "ring"
        v["PaymentOrderProcedure"][order] = {"paymentOrderAmount": amt, "paymentOrderDateTime": when - timedelta(seconds=20)}
        v["PaymentExecutionProcedure"][exe] = {"paymentMechanismType": "InternetBanking", "paymentExecutionAmount": amt,
                                               "paymentExecutionDateTime": when, "paymentExecutionStatus": "Completed"}
        v["PartyAuthenticationAssessment"][auth] = {"authenticationChannel": "Online", "authenticationDateTime": when - timedelta(minutes=1),
                                                    "authenticationResult": "Success"}
        e += [["executesOrder", "PaymentExecutionProcedure", exe, "PaymentOrderProcedure", order],
              ["orderDebitsFacility", "PaymentOrderProcedure", order, "CurrentAccountFacility", f"CAF-{a}"],
              ["executionDebitsFacility", "PaymentExecutionProcedure", exe, "CurrentAccountFacility", f"CAF-{a}"],
              ["executionCreditsCounterparty", "PaymentExecutionProcedure", exe, "PartyReferenceDataDirectoryEntry", f"CPY-{payee}"],
              ["orderAuthenticatedBy", "PaymentOrderProcedure", order, "PartyAuthenticationAssessment", auth],
              ["authenticationForParty", "PartyAuthenticationAssessment", auth, "PartyReferenceDataDirectoryEntry", f"PRD-{a}"],
              ["authenticationUsedDevice", "PartyAuthenticationAssessment", auth, "AccessDevice", "DEV-TOR" if tor else f"DEV-{a}"],
              ["authenticationFromAddress", "PartyAuthenticationAssessment", auth, "NetworkAddress", "185.220.101.7" if tor else f"81.2.69.{a}"]]
    v["FraudDiagnosisAssessment"]["FDA-1"] = {"fraudScore": 0.91, "fraudDiagnosisResult": "Suspicious"}  # a score, not a case: trap
    return {"vertices": v, "edges": e}


def t24() -> dict:
    v = {t: {} for t in ("COMPANY", "CUSTOMER", "ACCOUNT", "FUNDS_TRANSFER", "STMT_ENTRY", "BENEFICIARY", "EB_EXTERNAL_USER", "PROTOCOL")}
    e = []
    v["COMPANY"]["GB0010001"] = {"COMPANY_NAME": "Head office", "LOCAL_CURRENCY": "GBP"}
    for a in range(N):
        v["CUSTOMER"][str(100100 + a)] = {"SHORT_NAME": f"CUST{a}", "KYC_COMPLETE": "YES", "LAST_KYC_REVIEW_DATE": "20250301"}
        v["ACCOUNT"][f"10010000{a}"] = {"CATEGORY": 1001, "CURRENCY": "GBP", "OPENING_DATE": f"202403{10 + a}", "POSTING_RESTRICT": 0}
        e.append(["ACCOUNT_CUSTOMER", "ACCOUNT", f"10010000{a}", "CUSTOMER", str(100100 + a)])
    for p in (*range(4), 8, 9):
        v["BENEFICIARY"][f"BEN{p:04d}"] = {"BEN_SHORT_NAME_HASH": f"payee{p}", "BEN_BIC": "EXTRDEFF"}
    for n, (a, when, amt, payee, _) in enumerate(_history()):
        ft = f"FT26{n:06d}"
        v["FUNDS_TRANSFER"][ft] = {"TRANSACTION_TYPE": "OT", "DEBIT_AMOUNT": amt, "DEBIT_CURRENCY": "GBP",
                                   "PROCESSING_DATE": when.strftime("%Y%m%d"), "DATE_TIME": when.strftime("%y%m%d%H%M"),
                                   "RECORD_STATUS": ""}
        e += [["FT_DEBIT_ACCT_NO", "FUNDS_TRANSFER", ft, "ACCOUNT", f"10010000{a}"],
              ["FT_BENEFICIARY", "FUNDS_TRANSFER", ft, "BENEFICIARY", f"BEN{payee:04d}"]]
        st = f"{ft}.1"  # the ledger posting the transfer produced: trap
        v["STMT_ENTRY"][st] = {"AMOUNT_LCY": -amt, "BOOKING_DATE": when.strftime("%Y%m%d"), "TRANS_REFERENCE": ft}
        e += [["STMT_ACCOUNT_NUMBER", "STMT_ENTRY", st, "ACCOUNT", f"10010000{a}"], ["STMT_TRANS_REFERENCE", "STMT_ENTRY", st, "FUNDS_TRANSFER", ft]]
    return {"vertices": v, "edges": e}


BANKS = {"Bank_ISO20022": iso20022, "Bank_BIAN": bian, "Bank_CoreT24": t24}
ACCOUNT0 = {"Bank_ISO20022": "A0", "Bank_BIAN": "CAF-0", "Bank_CoreT24": "100100000"}
