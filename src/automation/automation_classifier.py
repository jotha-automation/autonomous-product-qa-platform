"""Classify intake obligations as automated, partial, or manual (non-automatable)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Tuple

from src.intake.test_naming import merge_automation_tier

AutomationTier = Literal["automated", "partial", "manual"]

# Human-only channels: biometrics, physical devices, time/device-bound execution.
_NON_AUTOMATABLE_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"biometr", re.I), "biometrics_required"),
    (re.compile(r"huella\s+dactilar|reconocimiento\s+facial|face\s*id", re.I), "biometrics_required"),
    (re.compile(r"cajero\s+autom[aá]tico|\batm\b", re.I), "atm_physical_device"),
    (re.compile(r"dat[aá]fono|datafono|terminal\s+pos|\bpos\b", re.I), "payment_terminal"),
    (re.compile(r"tarjeta\s+f[ií]sica|chip\s+y\s+pin|band[aá]\s+magn", re.I), "physical_card"),
    (re.compile(r"pago\s+con\s+tarjeta\s+en\s+comercio|pago\s+presencial", re.I), "in_person_payment"),
    (re.compile(r"intervenci[oó]n\s+humana|validaci[oó]n\s+manual\s+del\s+operador", re.I), "human_operator"),
    (re.compile(r"presencial|en\s+sucursal|en\s+oficina", re.I), "in_person"),
    (re.compile(r"sms\s+otp|otp\s+por\s+sms|c[oó]digo\s+por\s+llamada", re.I), "out_of_band_otp"),
    (re.compile(r"correo\s+externo|fuera\s+del\s+portal|fuera\s+de\s+horario", re.I), "external_channel"),
    (re.compile(r"horario\s+de\s+atenci[oó]n|entre\s+las\s+\d|solo\s+d[ií]as?\s+h[aá]biles\s+de\s+\d", re.I), "time_window"),
    (re.compile(r"fin\s+de\s+semana|s[aá]bado|domingo|d[ií]a\s+festivo", re.I), "calendar_window"),
    (re.compile(r"dispositivo\s+m[oó]vil\s+del\s+usuario|tablet\s+del\s+cliente", re.I), "client_device"),
    (re.compile(r"impresora\s+f[ií]sica|escaneo\s+f[ií]sico", re.I), "physical_peripheral"),
    (re.compile(r"dispositivo\s+especial|hardware\s+externo|lector\s+de\s+tarjetas", re.I), "special_device"),
    (re.compile(r"solicitud\s+especial|aprobaci[oó]n\s+presencial|firma\s+manuscrita", re.I), "special_request"),
    (re.compile(r"no\s+automatiz|manual\s+exclusivamente|solo\s+manual|ejecutor\s+manual", re.I), "explicit_manual"),
]

_PARTIAL_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"archivo\s+plano|importar|adjunt", re.I), "file_upload"),
    (re.compile(r"correo|email|notificaci[oó]n", re.I), "notification"),
    (re.compile(r"aprobaci[oó]n\s+de\s+supervisor|segunda\s+firma", re.I), "approval_workflow"),
]

_BANK_PORTAL_AUTOMATABLE_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"d[ií]a[s]?\s+h[aá]bil", re.I),
    re.compile(r"par[aá]metro\s*=\s*\d+\s*d[ií]a", re.I),
    re.compile(r"login|ingresar|portal\s+empresarial", re.I),
    re.compile(r"inscripci[oó]n|validar|actualizar|consulta", re.I),
]


def _obligation_blob(obl: Dict[str, Any]) -> str:
    parts = [
        obl.get("title", ""),
        obl.get("external_id", ""),
        " ".join(obl.get("preconditions") or []),
        " ".join(obl.get("steps") or []),
        " ".join(obl.get("expected_results") or []),
        obl.get("excerpt", ""),
    ]
    return " ".join(str(p) for p in parts if p)


def detect_automation_blockers(obl: Dict[str, Any]) -> List[str]:
    blob = _obligation_blob(obl)
    blockers: List[str] = []
    for pattern, code in _NON_AUTOMATABLE_PATTERNS:
        if pattern.search(blob):
            blockers.append(code)
    return sorted(set(blockers))


def _bank_portal_simulable(obl: Dict[str, Any]) -> bool:
    blob = _obligation_blob(obl)
    return any(p.search(blob) for p in _BANK_PORTAL_AUTOMATABLE_PATTERNS)


def classify_automation_tier(
    obl: Dict[str, Any],
    *,
    bank_portal: bool = False,
) -> Tuple[AutomationTier, List[str], str]:
    """Return (tier, blockers, rationale) for a single obligation."""
    blockers = detect_automation_blockers(obl)
    blob = _obligation_blob(obl)

    if blockers:
        labels = ", ".join(blockers)
        return (
            "manual",
            blockers,
            f"Requiere ejecución manual o condiciones no reproducibles en automatización ({labels}).",
        )

    for pattern, code in _PARTIAL_PATTERNS:
        if pattern.search(blob):
            return (
                "partial",
                [code],
                "Flujo parcialmente automatizable; puede requerir validación manual complementaria.",
            )

    if bank_portal and _bank_portal_simulable(obl):
        if re.search(r"d[ií]a[s]?\s+h[aá]bil", blob, re.I):
            return (
                "automated",
                [],
                "Días hábiles simulables en portal demo (__aptpDemo).",
            )
        if len(obl.get("steps") or []) >= 2:
            return ("automated", [], "Flujo UI reproducible en portal empresarial demo.")

    steps = obl.get("steps") or []
    if len(steps) >= 2:
        return ("automated", [], "Pasos suficientes para automatización E2E.")

    return ("partial", [], "Cobertura parcial; faltan pasos o precondiciones explícitas.")


def apply_automation_classification(
    obl: Dict[str, Any],
    *,
    bank_portal: bool = False,
) -> Dict[str, Any]:
    tier, blockers, rationale = classify_automation_tier(obl, bank_portal=bank_portal)
    out = dict(obl)
    out["automation_tier"] = tier
    out["automation_blockers"] = blockers
    out["automation_rationale"] = rationale
    return out


def reconcile_automation_classification(
    obl: Dict[str, Any],
    proposed: Dict[str, Any],
    *,
    bank_portal: bool = False,
) -> Dict[str, Any]:
    """Fusiona heurística determinística con salida LLM; manual siempre prevalece."""
    baseline = apply_automation_classification(obl, bank_portal=bank_portal)
    llm_tier = proposed.get("automation_tier")
    merged_tier = merge_automation_tier(
        baseline.get("automation_tier", "partial"),
        llm_tier if llm_tier in {"automated", "partial", "manual"} else "partial",
    )
    blockers = sorted(
        set(baseline.get("automation_blockers") or [])
        | set(proposed.get("automation_blockers") or [])
    )
    rationale = proposed.get("automation_rationale") or baseline.get("automation_rationale") or ""
    if merged_tier == "manual" and baseline.get("automation_tier") == "manual":
        rationale = baseline.get("automation_rationale") or rationale
    out = {**obl, **{k: v for k, v in proposed.items() if v is not None}}
    out["automation_tier"] = merged_tier
    out["automation_blockers"] = blockers
    out["automation_rationale"] = rationale
    return out


def partition_obligations(
    obligations: List[Dict[str, Any]],
    *,
    bank_portal: bool = False,
) -> Dict[str, Any]:
    """Split obligations into automatable vs manual buckets for planning reports."""
    classified = [apply_automation_classification(o, bank_portal=bank_portal) for o in obligations if isinstance(o, dict)]
    automatable = [o for o in classified if o.get("automation_tier") != "manual"]
    manual = [o for o in classified if o.get("automation_tier") == "manual"]
    return {
        "obligations": classified,
        "automatable": automatable,
        "manual": manual,
        "automatable_count": len(automatable),
        "manual_count": len(manual),
    }
