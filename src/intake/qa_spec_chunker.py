"""Split unstructured QA specification PDFs (TestLink, etc.) into test-case chunks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

# TestLink / enterprise case headers
_CASE_HEADER = re.compile(
    r"Caso de Prueba\s+(EMPRESARIAL[-\s]?(\d+))\s*:\s*(.+?)(?:\s*\[Versión|\n)",
    re.I,
)
_CASE_SPLIT = re.compile(r"(?=Caso de Prueba\s+EMPRESARIAL[-\s]?\d+\s*:)", re.I)
_INC_PATTERN = re.compile(r"\b(INC\d+)\b", re.I)
_MANUAL_HINTS = re.compile(r"(d[ií]as?\s+h[aá]biles?|fin de semana|d[ií]a festivo|calendario)", re.I)
_UI_HINTS = re.compile(
    r"(inscripcion|login|ingresar|validar|actualizar|consulta|pago|continuar|boton|botón|módulo)",
    re.I,
)


@dataclass
class QaSpecChunk:
    case_id: str
    title: str
    text: str
    source_filename: str = ""

    @property
    def obligation_id(self) -> str:
        digits = re.search(r"(\d+)", self.case_id)
        if digits:
            return f"OBL-EMP-{digits.group(1)}"
        return f"OBL-{self.case_id.replace(' ', '-')}"


def is_qa_spec_text(text: str, filename: str = "") -> bool:
    """Heuristic: document looks like a TestLink / QA test specification."""
    if not text or len(text) < 400:
        return False
    name = filename.lower()
    if any(tok in name for tok in ("prueba", "testlink", "test case", "qa spec", "inc")):
        if _CASE_SPLIT.search(text) or "caso de prueba" in text.lower():
            return True
    hits = len(_CASE_HEADER.findall(text))
    if hits >= 1:
        return True
    if "caso de prueba" in text.lower() and ("pasos" in text.lower() or "precondicion" in text.lower()):
        return True
    return False


def chunk_qa_spec_text(text: str, *, source_filename: str = "") -> List[QaSpecChunk]:
    """Split QA spec text into one chunk per test case."""
    if not text.strip():
        return []

    parts = _CASE_SPLIT.split(text)
    chunks: List[QaSpecChunk] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        match = _CASE_HEADER.search(part)
        if not match:
            continue
        case_id = match.group(1).replace(" ", "-").upper()
        if not case_id.startswith("EMPRESARIAL"):
            case_id = f"EMPRESARIAL-{match.group(2)}"
        title = match.group(3).strip()
        chunks.append(
            QaSpecChunk(
                case_id=case_id,
                title=title,
                text=part,
                source_filename=source_filename,
            )
        )

    if not chunks and is_qa_spec_text(text, source_filename):
        chunks.append(
            QaSpecChunk(
                case_id="SPEC-001",
                title=source_filename or "QA specification",
                text=text,
                source_filename=source_filename,
            )
        )
    return chunks


def chunk_documents(documents: List[dict]) -> List[QaSpecChunk]:
    """Extract QA spec chunks from all intake documents."""
    all_chunks: List[QaSpecChunk] = []
    for doc in documents:
        text = doc.get("raw_text") or ""
        filename = doc.get("filename") or doc.get("id") or ""
        if not is_qa_spec_text(text, filename):
            continue
        all_chunks.extend(chunk_qa_spec_text(text, source_filename=filename))
    return all_chunks


def _section_lines(text: str, start_label: str, stop_labels: tuple[str, ...]) -> List[str]:
    lower = text.lower()
    start = lower.find(start_label.lower())
    if start < 0:
        return []
    segment = text[start + len(start_label) :]
    end = len(segment)
    for stop in stop_labels:
        idx = segment.lower().find(stop.lower())
        if idx > 0:
            end = min(end, idx)
    lines = [ln.strip() for ln in segment[:end].splitlines() if ln.strip()]
    cleaned: List[str] = []
    for ln in lines:
        if ln.isdigit() and len(ln) <= 2:
            continue
        if ln.lower() in {"nº:", "nº", "pasos:", "resultados esperados:"}:
            continue
        cleaned.append(ln)
    return cleaned[:20]


def parse_chunk_to_obligation_dict(chunk: QaSpecChunk) -> Dict:
    """Deterministic parse of a single test-case chunk into obligation shape."""
    text = chunk.text
    preconditions = _section_lines(
        text,
        "Precondiciones:",
        ("pasos:", "nº:", "resultados esperados:"),
    )
    steps = _section_lines(
        text,
        "Pasos:",
        ("resultados esperados:", "tipo de ejecución", "duración estimada"),
    )
    expected = _section_lines(
        text,
        "Resultados Esperados:",
        ("tipo de ejecución", "duración estimada", "requisitos", "keywords", "caso de prueba"),
    )

    if not steps:
        for ln in text.splitlines():
            low = ln.lower()
            if any(tok in low for tok in ("ingresar", "dar clic", "selecciona", "validar", "actualizar", "buscar")):
                steps.append(ln.strip())
    if not expected:
        for ln in text.splitlines():
            if "visualiza" in ln.lower() or "estado" in ln.lower():
                expected.append(ln.strip())

    summary_match = re.search(r"Resumen:\s*(.+?)(?:Precondiciones:|Pasos:|$)", text, re.I | re.S)
    summary = summary_match.group(1).strip()[:500] if summary_match else chunk.title

    from src.automation.automation_classifier import apply_automation_classification

    base = {
        "id": chunk.obligation_id,
        "external_id": chunk.case_id,
        "title": f"{chunk.case_id}: {chunk.title}",
        "preconditions": preconditions[:8],
        "steps": steps[:15] or [summary[:200]],
        "expected_results": expected[:12] or [summary[:200]],
        "required_assets": [],
        "source_filename": chunk.source_filename,
        "excerpt": text[:600],
    }
    if re.search(r"usuario|token|login|acceso", text, re.I):
        base["required_assets"].append("login_user")
    if "archivo plano" in text.lower():
        base["required_assets"].append("plain_file")
    return apply_automation_classification(base, bank_portal=True)


def obligations_from_chunks(chunks: List[QaSpecChunk]) -> List[Dict]:
    """Build obligation dicts from parsed chunks (dedupe by case_id)."""
    seen: set[str] = set()
    out: List[Dict] = []
    for chunk in chunks:
        key = chunk.case_id
        if key in seen:
            continue
        seen.add(key)
        out.append(parse_chunk_to_obligation_dict(chunk))
    return out


def extract_inc_id(text: str) -> str:
    match = _INC_PATTERN.search(text)
    return match.group(1).upper() if match else ""


def summarize_chunks_for_llm(chunks: List[QaSpecChunk], *, max_chunks: int = 24, per_chunk: int = 3500) -> str:
    """Compact multi-case summary for LLM prompts within budget."""
    parts: List[str] = []
    for chunk in chunks[:max_chunks]:
        body = chunk.text[:per_chunk]
        parts.append(f"=== {chunk.case_id}: {chunk.title} ===\n{body}")
    return "\n\n".join(parts)
