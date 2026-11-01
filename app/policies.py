"""Split the store policy PDF into searchable sections.

Why not heavy RAG: the manual has 8 pages. Section retrieval by topic
keywords is simpler, cheaper and easier to explain in an interview.
If the corpus grew a lot, we would swap this for embeddings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from .db import policy_pdf_path


@dataclass(frozen=True)
class PolicySection:
    section_id: str
    title: str
    topics: tuple[str, ...]
    text: str


def _extract_pdf_text() -> str:
    doc = pymupdf.open(policy_pdf_path())
    parts = [page.get_text() for page in doc]
    text = "\n".join(parts)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _slice(text: str, start: str, end: str | None = None) -> str:
    start_idx = text.find(start)
    if start_idx < 0:
        return ""
    if end is None:
        return text[start_idx:].strip()
    end_idx = text.find(end, start_idx + len(start))
    if end_idx < 0:
        return text[start_idx:].strip()
    return text[start_idx:end_idx].strip()


def build_policy_sections() -> list[PolicySection]:
    text = _extract_pdf_text()

    raw_sections = [
        (
            "sobre",
            "Sobre a Empório da Música",
            ("sobre", "missão", "empresa", "cnpj", "endereço", "contato", "telefone", "email", "e-mail", "fundação"),
            _slice(text, "1. Sobre a Empório da Música", "2. Horário de Funcionamento"),
        ),
        (
            "horario",
            "Horário de Funcionamento",
            ("horário", "horario", "funcionamento", "abre", "fecha", "expediente", "sabado", "sábado", "domingo", "feriado"),
            _slice(text, "2. Horário de Funcionamento", "3. Formas de Pagamento"),
        ),
        (
            "pagamento",
            "Formas de Pagamento",
            ("pagamento", "pix", "cartão", "cartao", "crédito", "credito", "débito", "debito", "boleto", "parcela", "parcelamento", "juros"),
            _slice(text, "3. Formas de Pagamento", "4. Política de Trocas e Devoluções"),
        ),
        (
            "trocas",
            "Política de Trocas e Devoluções",
            ("troca", "devolução", "devolucao", "arrependimento", "reembolso", "defeito", "preferência", "preferencia"),
            _slice(text, "4. Política de Trocas e Devoluções", "5. Política de Frete e Entregas"),
        ),
        (
            "frete",
            "Política de Frete e Entregas",
            ("frete", "entrega", "prazo", "correios", "sedex", "pac", "jadlog", "rastreamento", "rastreio", "cep"),
            _slice(text, "5. Política de Frete e Entregas", "6. Promoções e Descontos"),
        ),
        (
            "promocoes",
            "Promoções e Descontos",
            ("promoção", "promocao", "desconto", "black friday", "aniversário", "aniversario", "cumulativ"),
            _slice(text, "6. Promoções e Descontos", "7. Atendimento via WhatsApp"),
        ),
        (
            "whatsapp",
            "Atendimento via WhatsApp",
            ("whatsapp", "atendimento", "tom", "assistente", "fora do escopo", "acessório", "acessorio"),
            _slice(text, "7. Atendimento via WhatsApp", "8. Garantia"),
        ),
        (
            "garantia",
            "Garantia",
            ("garantia", "fabricante", "defeito", "mau uso"),
            _slice(text, "8. Garantia", "9. Privacidade e Proteção de Dados"),
        ),
        (
            "privacidade",
            "Privacidade e Proteção de Dados",
            ("privacidade", "lgpd", "dados", "exclusão", "exclusao"),
            _slice(text, "9. Privacidade e Proteção de Dados", "10. Disposições Finais"),
        ),
        (
            "finais",
            "Disposições Finais",
            ("disposição", "disposicao", "atualização", "atualizacao"),
            _slice(text, "10. Disposições Finais", None),
        ),
    ]

    sections: list[PolicySection] = []
    for section_id, title, topics, body in raw_sections:
        cleaned = re.sub(r"Página \d+", "", body).strip()
        if cleaned:
            sections.append(PolicySection(section_id, title, topics, cleaned))
    return sections


_SECTIONS: list[PolicySection] | None = None


def get_sections() -> list[PolicySection]:
    global _SECTIONS
    if _SECTIONS is None:
        _SECTIONS = build_policy_sections()
    return _SECTIONS


def search_policies(query: str, limit: int = 2) -> list[PolicySection]:
    q = (query or "").lower().strip()
    if not q:
        return get_sections()[:limit]

    scored: list[tuple[int, PolicySection]] = []
    for section in get_sections():
        score = 0
        hay = f"{section.title}\n{section.text}".lower()
        for topic in section.topics:
            if topic in q:
                score += 5
        for token in re.findall(r"[a-zà-ú0-9]+", q):
            if len(token) < 3:
                continue
            if token in hay:
                score += 1
            if token in section.section_id or token in section.title.lower():
                score += 2
        if score:
            scored.append((score, section))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        # Fallback: return overview + hours so the agent still has grounding.
        return [s for s in get_sections() if s.section_id in {"sobre", "horario"}][:limit]
    return [section for _, section in scored[:limit]]
