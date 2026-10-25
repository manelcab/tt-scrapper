"""Parser de los PDF de CALENDARIO de la RTBTT (`lligues{temp}/{GRUP}.pdf`,
listados por `calendaris_{temp}.html`).

A diferencia de las actas por jornada (`actes_{temp}.html` → `parse_acta`, con
partidos individuales y licencias), estos PDF traen, por grupo:

- una tabla de clasificación (posición, equipo, EJ/EG/EP, Pts), y
- el calendario completo: por jornada, cada encuentro como
  `dd/mm/yy HH:MM  LOCAL  <nº>  VISITANTE  [res_local res_visitant]`.

La RTBTT **actualiza el mismo PDF** semana a semana: al jugarse un encuentro
aparecen sus dos últimos números (el marcador por EQUIPO, p. ej. 4 2). Por eso
esto devuelve encuentros con `status='scheduled'` (sin marcador) o `'completed'`
(con marcador), pero **nunca partidos individuales**: esos solo están en las actas.

`DESCANSA` en cualquiera de los dos lados = jornada de descanso (grupos impares):
no es un encuentro, se ignora.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import pdfplumber

from models.match import Match
from models.team import Team
from sources import season_year_for_month
from utils.text import slugify

# `dd/mm/yy HH:MM <resto>`
_DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})\s+(.*)$")
# fila de clasificación: pos, nombre, y 7 enteros (EJ EG EP PG PP CdE Pts)
_STAND_RE = re.compile(
    r"^(\d+)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$"
)
_JOR_RE = re.compile(r"^Jornada\s+(\d+)")
# marcador por equipo = los DOS últimos enteros de la fila (tras anotaciones
# tipo "C.Camp" de cambio de sede que la RTBTT intercala antes del resultado).
_SCORE_RE = re.compile(r"(\d+)\s+(\d+)\s*$")
_BYE = "DESCANSA"


def parse_calendar_pdf(pdf_path: str) -> tuple[str, list[Team], list[Match]]:
    """Devuelve (título del grupo, clasificación, encuentros) de un PDF de grupo."""
    lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(l.rstrip() for l in text.split("\n"))

    title = _parse_title(lines)
    teams = _parse_standings(lines)
    names = sorted((t.name for t in teams), key=len, reverse=True)  # match más largo
    matches = _parse_fixtures(lines, names)
    return title, teams, matches


def _parse_title(lines: list[str]) -> str:
    """Nombre del grupo: la línea justo antes de la cabecera 'Equip EJ EG …'."""
    for i, l in enumerate(lines):
        if l.startswith("Equip ") and "EJ" in l:
            for prev in reversed(lines[:i]):
                if prev.strip():
                    return prev.strip()
            break
    return ""


def _parse_standings(lines: list[str]) -> list[Team]:
    teams: list[Team] = []
    for l in lines:
        if _JOR_RE.match(l):
            break  # la clasificación va antes del calendario
        m = _STAND_RE.match(l)
        if not m:
            continue
        name = m.group(2).strip()
        teams.append(Team(
            id=slugify(name),
            name=name,
            club="",
            position=int(m.group(1)),
            points=int(m.group(9)),
            matches_played=int(m.group(3)),
            matches_won=int(m.group(4)),
            matches_lost=int(m.group(5)),
        ))
    return teams


def _parse_fixtures(lines: list[str], names: list[str]) -> list[Match]:
    def team_prefix(text: str) -> Optional[str]:
        for n in names:
            if text == n or text.startswith(n + " "):
                return n
        return None

    matches: list[Match] = []
    jornada = 0
    for l in lines:
        jm = _JOR_RE.match(l)
        if jm:
            jornada = int(jm.group(1))
            continue
        dm = _DATE_RE.match(l)
        if not dm or jornada == 0:
            continue
        date_s, time_s, rest = dm.group(1), dm.group(2), dm.group(3).strip()
        if _BYE in rest:
            continue  # jornada de descanso, no es un encuentro

        local = team_prefix(rest)
        if not local:
            continue
        after_local = rest[len(local):].strip()
        nm = re.match(r"^(\d+)\s+(.*)$", after_local)  # nº de partido
        if not nm:
            continue
        after_num = nm.group(2).strip()
        visitor = team_prefix(after_num)
        if not visitor:
            continue
        tail = after_num[len(visitor):].strip()

        sm = _SCORE_RE.search(tail)
        score_local = int(sm.group(1)) if sm else None
        score_visitor = int(sm.group(2)) if sm else None

        matches.append(Match(
            id=f"{jornada}_{slugify(local)}_{slugify(visitor)}",
            matchday_number=jornada,
            date=_parse_datetime(date_s, time_s),
            team_local_id=slugify(local),
            team_local_name=local,
            team_visitor_id=slugify(visitor),
            team_visitor_name=visitor,
            score_local=score_local,
            score_visitor=score_visitor,
            acta_url="",
            status="completed" if score_local is not None else "scheduled",
            individual_matches=[],
        ))
    return matches


def _parse_datetime(date_s: str, time_s: str) -> Optional[datetime]:
    # El año se DERIVA del mes + la temporada (no se lee el 'yy' del PDF): la RTBTT
    # lo teclea mal en algunos bloques (p. ej. la última jornada de veteranos como
    # '17/02/26' cuando es 2027). Ver sources.season_year_for_month.
    dm = re.match(r"^(\d{2})/(\d{2})/\d{2}$", date_s)
    if not dm:
        return None
    day, month = int(dm.group(1)), int(dm.group(2))
    hh, mm = 0, 0
    tm = re.match(r"^(\d{2}):(\d{2})$", time_s or "")
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
    try:
        return datetime(season_year_for_month(month), month, day, hh, mm)
    except ValueError:
        return None
