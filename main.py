from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from config import Config
from models.competition import CompetitionIndex, Group
from parsers.fctt_partits_parser import parse_partits
from parsers.fctt_ranking_pdf_parser import parse_ranking
from parsers.pdf_parser import PdfParser
from scrapers.fctt_partits_scraper import FcttPartitsScraper, build_records
from scrapers.fctt_rankings_scraper import (
    FcttRankingsScraper,
    build_category_documents,
    build_player_documents,
)
from scrapers.fctt_scraper import FcttScraper, SeasonMismatchError
from scrapers.rfetm_rankings_scraper import (
    RfetmRankingsScraper,
    build_category_documents as build_rfetm_categories,
    build_player_documents as build_rfetm_players,
)
from scrapers.rfetm_scraper import RfetmScraper
from scrapers.rtbtt_calendar_scraper import RtbttCalendarScraper
from scrapers.rtbtt_scraper import RtbttScraper
from sources import (
    FCTT,
    FCTT_TERRITORIALS,
    RFETM,
    RTBTT,
    RTBTT_CALENDAR_INDEX,
    territorial_group_url,
)
from storage import make_storage
from storage.local_cache import LocalCache
from utils.http_client import HttpClient
from utils.logger import get_logger

console = Console()
logger = get_logger(__name__)


def _banner() -> None:
    console.print(
        "\n[bold magenta]🏓 TT Agent Scraper[/bold magenta]  "
        "[dim]Liga Territorial Barcelona[/dim]\n"
    )


def _build_config(ctx_cfg: Config, dry_run: bool, category: str, group: str) -> Config:
    """Return a config with CLI overrides applied."""
    from dataclasses import replace

    overrides: dict = {}
    if dry_run:
        overrides["dry_run"] = True
    if category:
        overrides["only_categories"] = [c.strip() for c in category.split(",") if c.strip()]
    if group:
        overrides["only_groups"] = [g.strip() for g in group.split(",") if g.strip()]
    return replace(ctx_cfg, **overrides) if overrides else ctx_cfg


def _filter_categories(categories, cfg: Config):
    if cfg.only_categories:
        categories = [c for c in categories if c.id in cfg.only_categories]
    if cfg.only_groups:
        for cat in categories:
            cat.groups = [g for g in cat.groups if g.id in cfg.only_groups]
        categories = [c for c in categories if c.groups]
    return categories


@click.group()
def cli():
    """TT Agent — scraper de competiciones de tenis de mesa."""


# ═══════════════════════════════════════════════════════════════ RTBTT commands

@cli.group()
def rtbtt() -> None:
    """Scraper para la RTBTT (Liga Territorial Barcelona)."""


@rtbtt.command("run")
@click.option("--category", default="", help="Filtrar por categoría (ej. PREF,1A)")
@click.option("--group", default="", help="Filtrar por grupo (ej. PREF_G1)")
@click.option("--jornada", default=0, type=int, help="Procesar solo esta jornada (ignora hashes, fuerza re-descarga)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en Firestore")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todo")
def rtbtt_run(category: str, group: str, jornada: int, dry_run: bool, force: bool) -> None:
    """Ejecutar el scraper completo de la RTBTT."""
    _banner()
    cfg = _build_config(Config.from_env(), dry_run, category, group)

    with HttpClient() as http:
        cache = LocalCache(cfg.cache_dir, readonly=cfg.dry_run, backend=cfg.storage_backend)
        firestore = make_storage(cfg)

        if not cfg.dry_run:
            console.print("🔑 Validando credenciales Firestore...")
            if not firestore.validate():
                console.print("[red]❌ Credenciales Firestore inválidas. Usa --dry-run para probar sin Firestore.[/red]")
                sys.exit(1)
            console.print("✅ Credenciales Firestore validadas\n")

        scraper = RtbttScraper(cfg, http)
        categories = scraper.get_index()
        categories = _filter_categories(categories, cfg)

        if not categories:
            console.print("[yellow]⚠️  No hay categorías/grupos que procesar con los filtros indicados.[/yellow]")
            return

        # Save competition root document
        competition = CompetitionIndex(
            competition_id=cfg.competition_id,
            name=RTBTT.name,
            organization=RTBTT.organization,
            season=RTBTT.season,
            source_url=cfg.index_url,
            categories=categories,
        )
        try:
            firestore.save_competition(competition, cfg.dry_run)
        except Exception as exc:
            logger.warning(f"No se pudo guardar competición: {exc}")

        if force:
            console.print("[dim]--force activo: se ignorarán los hashes de caché[/dim]\n")
        if jornada:
            console.print(f"[dim]--jornada {jornada}: solo se procesará esa jornada[/dim]\n")

        start = time.monotonic()
        total_stats = {
            "groups_processed": 0,
            "groups_skipped": 0,
            "matchdays": 0,
            "matches": 0,
            "individual_matches": 0,
            "errors": [],
        }

        for cat in categories:
            for grp in cat.groups:
                if force:
                    cache.invalidate_group(grp.id)  # grupo + todas sus jornadas
                stats = scraper.scrape_group(grp, cache, firestore, cfg.dry_run, only_jornada=jornada)
                if stats["skipped"]:
                    total_stats["groups_skipped"] += 1
                else:
                    total_stats["groups_processed"] += 1
                total_stats["matchdays"] += stats["matchdays"]
                total_stats["matches"] += stats["matches"]
                total_stats["individual_matches"] += stats["individual_matches"]
                total_stats["errors"].extend(stats.get("errors", []))

        elapsed = time.monotonic() - start

        # Summary
        console.print("\n[bold cyan]📊 RESUMEN[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Grupos procesados:", str(total_stats["groups_processed"]))
        summary.add_row("Grupos sin cambios:", str(total_stats["groups_skipped"]))
        summary.add_row("Jornadas nuevas:", str(total_stats["matchdays"]))
        summary.add_row("Partidos nuevos:", str(total_stats["matches"]))
        summary.add_row("Partidos individuales nuevos:", str(total_stats["individual_matches"]))
        summary.add_row("Errores:", str(len(total_stats["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)

        if total_stats["errors"]:
            console.print("\n[bold yellow]⚠️  ERRORES DURANTE LA EJECUCIÓN:[/bold yellow]")
            err_table = Table(show_header=True, header_style="bold red")
            err_table.add_column("Error")
            for err in total_stats["errors"]:
                err_table.add_row(err)
            console.print(err_table)


@rtbtt.command("calendar")
@click.option("--group", default="", help="Filtrar por grupo (ej. PREF_G1)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en el backend")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todos los PDF")
def rtbtt_calendar(group: str, dry_run: bool, force: bool) -> None:
    """Cargar el CALENDARIO RTBTT (fixtures + resultado por equipo) desde los PDF.

    Fuente calendaris_{temp}.html (distinta de las actas). Da los próximos
    partidos y el resultado por equipo, sin partidos individuales.
    """
    _banner()
    cfg = Config.from_env()

    with HttpClient() as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        storage = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando almacenamiento...")
            if not storage.validate():
                console.print(
                    "[red]❌ Almacenamiento no válido. Usa --dry-run para probar.[/red]"
                )
                sys.exit(1)
            console.print("✅ Almacenamiento validado\n")

        scraper = RtbttCalendarScraper(cfg, http)
        groups = scraper.list_groups()
        if group:
            gf = [g.strip() for g in group.split(",") if g.strip()]
            groups = [g for g in groups if g.id in gf]
        if not groups:
            console.print("[yellow]⚠️  No hay grupos con los filtros indicados.[/yellow]")
            return

        competition = CompetitionIndex(
            competition_id=RTBTT.id,
            name=RTBTT.name,
            organization=RTBTT.organization,
            season=RTBTT.season,
            source_url=RTBTT_CALENDAR_INDEX,
            categories=[],
        )
        try:
            storage.save_competition(competition, dry_run)
        except Exception as exc:
            logger.warning(f"No se pudo guardar competición raíz: {exc}")

        start = time.monotonic()
        totals = {"groups": 0, "skipped": 0, "matchdays": 0, "matches": 0,
                  "fixtures": 0, "errors": []}
        for grp in groups:
            if force:
                cache.invalidate_group(grp.id)
            st = scraper.scrape_group(grp, cache, storage, dry_run, force=force)
            if st["skipped"]:
                totals["skipped"] += 1
            else:
                totals["groups"] += 1
            totals["matchdays"] += st["matchdays"]
            totals["matches"] += st["matches"]
            totals["fixtures"] += st["fixtures"]
            totals["errors"].extend(st.get("errors", []))
        elapsed = time.monotonic() - start

        console.print("\n[bold cyan]📊 RESUMEN RTBTT CALENDARIO[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Grupos procesados:", str(totals["groups"]))
        summary.add_row("Grupos sin cambios:", str(totals["skipped"]))
        summary.add_row("Jornadas:", str(totals["matchdays"]))
        summary.add_row("Encuentros jugados:", str(totals["matches"]))
        summary.add_row("Partidos de calendario:", str(totals["fixtures"]))
        summary.add_row("Errores:", str(len(totals["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)
        for err in totals["errors"]:
            console.print(f"  [red]• {err}[/red]")


@rtbtt.command("download")
@click.option("--group", required=True, help="ID del grupo (ej. PREF_G1)")
def rtbtt_download(group: str) -> None:
    """Descargar PDFs de un grupo sin procesar."""
    _banner()
    cfg = Config.from_env()
    cfg_filtered = _build_config(cfg, False, "", group)

    with HttpClient() as http:
        cache = LocalCache(cfg.cache_dir, backend=cfg.storage_backend)
        scraper = RtbttScraper(cfg_filtered, http)
        categories = scraper.get_index()
        categories = _filter_categories(categories, cfg_filtered)

        for cat in categories:
            for grp in cat.groups:
                console.print(f"⬇️  Descargando PDFs del grupo {grp.id}...")
                html = http.fetch_text(grp.source_url)
                matchdays_info, _ = __import__("parsers.html_parser", fromlist=["HtmlParser"]).HtmlParser.parse_group_page(
                    html, cfg.base_url, grp.id
                )
                for info in matchdays_info:
                    if info["acta_url"]:
                        scraper._download_pdf(
                            info["acta_url"], grp.id, info["number"], cache
                        )
        console.print("✅ Descarga completada")


@rtbtt.command("parse")
@click.option("--group", required=True, help="ID del grupo (ej. PREF_G1)")
@click.option("--jornada", default=0, type=int, help="Parsear solo esta jornada")
@click.option("--debug-pdf", is_flag=True, help="Mostrar contenido raw extraído del PDF")
def rtbtt_parse(group: str, jornada: int, debug_pdf: bool) -> None:
    """Parsear PDFs ya descargados y mostrar los resultados extraídos."""
    _banner()
    cfg = Config.from_env()
    pdfs_dir = Path(cfg.pdfs_dir)
    parser = PdfParser()

    pattern = f"{group}_jornada{jornada}.pdf" if jornada else f"{group}_jornada*.pdf"
    pdfs = sorted(pdfs_dir.glob(pattern))
    if not pdfs:
        console.print(f"[yellow]No se encontraron PDFs para '{pattern}' en {pdfs_dir}[/yellow]")
        return

    total_matches = 0
    total_individuals = 0

    for pdf_path in pdfs:
        stem_suffix = pdf_path.stem.split("jornada")[-1]
        matchday_n = int("".join(filter(str.isdigit, stem_suffix)) or "0")

        console.rule(f"[bold cyan]📄 {pdf_path.name}  (Jornada {matchday_n})[/bold cyan]")
        matches = parser.parse_acta(str(pdf_path), group, matchday_n, debug=debug_pdf)

        if not matches:
            console.print("  [yellow]⚠️  No se pudieron extraer datos de este PDF[/yellow]")
            continue

        for match in matches:
            # Match header
            date_str = f"  [dim]{match.date.strftime('%d/%m/%Y')}[/dim]" if match.date else ""
            console.print(
                f"\n  [bold cyan]{match.team_local_name}[/bold cyan]"
                f"  [bold green]{match.score_local} - {match.score_visitor}[/bold green]"
                f"  [bold cyan]{match.team_visitor_name}[/bold cyan]"
                f"{date_str}"
            )

            if match.individual_matches:
                from rich.box import SIMPLE
                ind_table = Table(show_header=True, header_style="bold dim", box=SIMPLE,
                                  padding=(0, 1))
                ind_table.add_column("Or", style="dim", width=3, justify="right")
                ind_table.add_column("Lic", style="dim", width=6)
                ind_table.add_column("Jugador Local", min_width=18)
                ind_table.add_column("Lic", style="dim", width=6)
                ind_table.add_column("Jugador Visitante", min_width=18)
                ind_table.add_column("Sets", style="cyan", width=5, justify="center")
                ind_table.add_column("Ganador", width=10)

                for im in match.individual_matches:
                    sl = im.sets_won_local
                    sv = im.sets_won_visitor
                    sets_str = f"{sl}-{sv}"
                    if im.winner == "local":
                        winner_cell = "[green]Local[/green]"
                        sets_cell = f"[green]{sets_str}[/green]"
                    else:
                        winner_cell = "[yellow]Visitante[/yellow]"
                        sets_cell = f"[yellow]{sets_str}[/yellow]"

                    if getattr(im, "is_doubles", False):
                        local_name = (
                            f"{im.player_local_name} /\n{im.player_local_name_2}"
                        )
                        visitor_name = (
                            f"{im.player_visitor_name} /\n{im.player_visitor_name_2}"
                        )
                        local_lic = f"{im.player_local_license}"
                        visitor_lic = f"{im.player_visitor_license}"
                        order_cell = f"[bold magenta]{im.order}D[/bold magenta]"
                    else:
                        local_name = im.player_local_name or "[dim]?[/dim]"
                        visitor_name = im.player_visitor_name or "[dim]?[/dim]"
                        local_lic = im.player_local_license or ""
                        visitor_lic = im.player_visitor_license or ""
                        order_cell = str(im.order)

                    ind_table.add_row(
                        order_cell,
                        local_lic,
                        local_name,
                        visitor_lic,
                        visitor_name,
                        sets_cell,
                        winner_cell,
                    )
                console.print(ind_table)
            else:
                console.print("  [dim](Sin partidos individuales extraídos)[/dim]")

            total_matches += 1
            total_individuals += len(match.individual_matches)

    console.rule()
    console.print(
        f"[bold]Total:[/bold] {len(pdfs)} PDF(s)  ·  "
        f"{total_matches} encuentros  ·  {total_individuals} partidos individuales"
    )


@rtbtt.command("list")
def rtbtt_list() -> None:
    """Listar categorías y grupos disponibles en la RTBTT."""
    _banner()
    cfg = Config.from_env()

    with HttpClient() as http:
        scraper = RtbttScraper(cfg, http)
        categories = scraper.get_index()

    if not categories:
        console.print("[yellow]No se encontraron categorías.[/yellow]")
        return

    table = Table(title="Competiciones disponibles", show_header=True, header_style="bold cyan")
    table.add_column("Categoría")
    table.add_column("ID")
    table.add_column("Grupos")

    for cat in categories:
        group_ids = ", ".join(g.id for g in cat.groups)
        table.add_row(cat.name, cat.id, group_ids)

    console.print(table)


@cli.command()
def validate() -> None:
    """Validar credenciales Firestore."""
    _banner()
    cfg = Config.from_env()
    firestore = make_storage(cfg)
    ok = firestore.validate()
    if ok:
        console.print("[green]✅ Credenciales Firestore válidas[/green]")
    else:
        console.print("[red]❌ Error validando credenciales Firestore[/red]")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════ RFETM commands

@cli.group()
def rfetm() -> None:
    """Scraper para la RFETM (federación española de tenis de mesa)."""


@rfetm.command("run")
@click.option("--category", default="", help="Filtrar por categoría (ej. liga1_M)")
@click.option("--group", default="", help="Filtrar por grupo (ej. liga1_g0_M)")
@click.option("--jornada", default=0, type=int, help="Procesar solo esta jornada (ignora hashes)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en Firestore")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todo")
def rfetm_run(category: str, group: str, jornada: int, dry_run: bool, force: bool) -> None:
    """Ejecutar el scraper de la RFETM."""
    _banner()
    cfg = Config.from_env()

    with HttpClient() as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        firestore = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando credenciales Firestore...")
            if not firestore.validate():
                console.print(
                    "[red]❌ Credenciales Firestore inválidas. "
                    "Usa --dry-run para probar sin Firestore.[/red]"
                )
                sys.exit(1)
            console.print("✅ Credenciales Firestore validadas\n")

        scraper = RfetmScraper(http)
        categories = scraper.get_index()

        # Apply filters
        if category:
            cat_filter = [c.strip() for c in category.split(",") if c.strip()]
            categories = [c for c in categories if c.id in cat_filter]
        if group:
            grp_filter = [g.strip() for g in group.split(",") if g.strip()]
            for cat in categories:
                cat.groups = [g for g in cat.groups if g.id in grp_filter]
            categories = [c for c in categories if c.groups]

        if not categories:
            console.print(
                "[yellow]⚠️  No hay categorías/grupos con los filtros indicados.[/yellow]"
            )
            return

        # Save competition root
        competition = CompetitionIndex(
            competition_id=RFETM.id,
            name=RFETM.name,
            organization=RFETM.organization,
            season=RFETM.season,
            source_url=RFETM.index_url,
            categories=categories,
        )
        try:
            firestore.save_competition(competition, dry_run)
        except Exception as exc:
            logger.warning(f"No se pudo guardar competición raíz: {exc}")

        if force:
            console.print("[dim]--force activo: se ignorarán los hashes de caché[/dim]\n")
        if jornada:
            console.print(f"[dim]--jornada {jornada}: solo se procesará esa jornada[/dim]\n")

        start = time.monotonic()
        total_stats = {
            "groups_processed": 0,
            "groups_skipped": 0,
            "matchdays": 0,
            "matches": 0,
            "individual_matches": 0,
            "fixtures": 0,
            "errors": [],
        }

        for cat in categories:
            for grp in cat.groups:
                if force:
                    cache.invalidate_group(grp.id)  # grupo + todas sus jornadas
                stats = scraper.scrape_group(grp, cache, firestore, dry_run, only_jornada=jornada)
                if stats["skipped"]:
                    total_stats["groups_skipped"] += 1
                else:
                    total_stats["groups_processed"] += 1
                total_stats["matchdays"] += stats["matchdays"]
                total_stats["matches"] += stats["matches"]
                total_stats["individual_matches"] += stats["individual_matches"]
                total_stats["fixtures"] += stats.get("fixtures", 0)
                total_stats["errors"].extend(stats.get("errors", []))

        elapsed = time.monotonic() - start

        console.print("\n[bold cyan]📊 RESUMEN RFETM[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Grupos procesados:", str(total_stats["groups_processed"]))
        summary.add_row("Jornadas nuevas:", str(total_stats["matchdays"]))
        summary.add_row("Encuentros nuevos:", str(total_stats["matches"]))
        summary.add_row("Partidos de calendario:", str(total_stats["fixtures"]))
        summary.add_row("Partidos individuales nuevos:", str(total_stats["individual_matches"]))
        summary.add_row("Errores:", str(len(total_stats["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)

        if total_stats["errors"]:
            console.print("\n[bold yellow]⚠️  ERRORES:[/bold yellow]")
            for err in total_stats["errors"]:
                console.print(f"  [red]• {err}[/red]")


@rfetm.command("rankings")
@click.option("--edition", default="", help="Solo este mes (ej. 2026-06)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en Firestore")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todo")
def rfetm_rankings(edition: str, dry_run: bool, force: bool) -> None:
    """Cargar los rànquings mensuales de la RFETM."""
    _banner()
    cfg = Config.from_env()

    with HttpClient() as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        firestore = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando credenciales Firestore...")
            if not firestore.validate():
                console.print(
                    "[red]❌ Credenciales Firestore inválidas. "
                    "Usa --dry-run para probar sin Firestore.[/red]"
                )
                sys.exit(1)
            console.print("✅ Credenciales Firestore validadas\n")

        scraper = RfetmRankingsScraper(http)
        refs = scraper.get_index()
        if edition:
            refs = [r for r in refs if r.id == edition]
        if not refs:
            console.print("[yellow]⚠️  No hay meses con los filtros indicados.[/yellow]")
            return

        start = time.monotonic()
        total = {"editions": 0, "players": 0, "skipped": 0, "categories": 0, "errors": []}
        parsed: list = []
        pending_hashes: dict[str, str] = {}

        for ref in refs:
            edicion, stats = scraper.scrape_edition(ref, cache, force)
            total["players"] += stats["players"]
            total["skipped"] += stats["skipped"]
            total["errors"].extend(stats["errors"])
            pending_hashes.update(stats["hashes"])
            if edicion is None:
                continue
            total["editions"] += 1
            parsed.append(edicion)

            categorias = build_rfetm_categories(edicion)
            total["categories"] += len(categorias)
            firestore.save_ranking_edition(
                RFETM.id, edicion.id, edicion.to_dict(), categorias, dry_run
            )

        # Los jugadores se escriben UNA vez con todas las ediciones fundidas.
        players = build_rfetm_players(parsed)
        if players:
            full_load = not edition
            console.print(
                f"\n💾 Escribiendo {len(players)} jugadores "
                f"({'sobrescribiendo' if full_load else 'fusionando'})..."
            )
            firestore.save_ranking_players(
                RFETM.id, list(players.items()), dry_run, merge=not full_load
            )
            firestore.save_ranking_root(
                RFETM.id,
                {
                    "competition_id": RFETM.id,
                    "organization": RFETM.organization,
                    "season": RFETM.season,
                    "source_url": RFETM.rankings_index,
                    "last_edition": parsed[-1].id if parsed else "",
                    "player_count": len(players),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                dry_run,
            )

        for key, value in pending_hashes.items():
            cache.set_matchday_hash(key, value)

        elapsed = time.monotonic() - start
        console.print("\n[bold cyan]📊 RESUMEN RÀNQUINGS RFETM[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Meses procesados:", str(total["editions"]))
        summary.add_row("Páginas sin cambios:", str(total["skipped"]))
        summary.add_row("Filas parseadas:", str(total["players"]))
        summary.add_row("Documentos de categoría:", str(total["categories"]))
        summary.add_row("Jugadores únicos:", str(len(players)))
        summary.add_row("Errores:", str(len(total["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)

        if total["errors"]:
            console.print("\n[bold yellow]⚠️  ERRORES:[/bold yellow]")
            for err in total["errors"][:10]:
                console.print(f"  [red]• {err}[/red]")


@rfetm.command("list")
def rfetm_list() -> None:
    """Listar todas las ligas y grupos disponibles en la RFETM."""
    _banner()
    with HttpClient() as http:
        scraper = RfetmScraper(http)
        categories = scraper.get_index()

    if not categories:
        console.print("[yellow]No se encontraron categorías.[/yellow]")
        return

    table = Table(
        title="RFETM — Ligas disponibles",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Categoría (--category)")
    table.add_column("Nombre")
    table.add_column("Grupos (--group)")

    for cat in categories:
        group_ids = ", ".join(g.id for g in cat.groups)
        table.add_row(cat.id, cat.name, group_ids)

    console.print(table)


# ═══════════════════════════════════════════════════════════════ FCTT commands

@cli.group()
def fctt() -> None:
    """Scraper para la FCTT (federació catalana de tennis taula)."""


@fctt.command("calendar")
@click.option("--category", default="", help="Filtrar por categoría (ej. TDM)")
@click.option("--group", default="", help="Filtrar por grupo (ej. TDM_G1)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en el backend")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todo")
def fctt_calendar(category: str, group: str, dry_run: bool, force: bool) -> None:
    """Cargar el CALENDARIO FCTT (fixtures + resultado por equipo) desde los
    div.match-container de cada grupo. Da los próximos partidos; sin partidos
    individuales (esos vienen del acta, con el markup nuevo, cuando se jueguen).
    """
    _banner()
    cfg = Config.from_env()

    with HttpClient(rate_limit_seconds=2.5) as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        storage = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando almacenamiento...")
            if not storage.validate():
                console.print("[red]❌ Almacenamiento no válido. Usa --dry-run.[/red]")
                sys.exit(1)
            console.print("✅ Almacenamiento validado\n")

        scraper = FcttScraper(http)
        categories = scraper.get_index()
        if category:
            cf = [c.strip() for c in category.split(",") if c.strip()]
            categories = [c for c in categories if c.id in cf]
        if group:
            gf = [g.strip() for g in group.split(",") if g.strip()]
            for cat in categories:
                cat.groups = [g for g in cat.groups if g.id in gf]
            categories = [c for c in categories if c.groups]
        if not categories:
            console.print("[yellow]⚠️  No hay categorías/grupos con los filtros.[/yellow]")
            return

        competition = CompetitionIndex(
            competition_id=FCTT.id, name=FCTT.name, organization=FCTT.organization,
            season=FCTT.season, source_url=FCTT.index_url, categories=categories,
        )
        try:
            storage.save_competition(competition, dry_run)
        except Exception as exc:
            logger.warning(f"No se pudo guardar competición raíz: {exc}")

        start = time.monotonic()
        totals = {"groups": 0, "skipped": 0, "matchdays": 0, "matches": 0,
                  "fixtures": 0, "errors": []}
        for cat in categories:
            for grp in cat.groups:
                if force:
                    cache.invalidate_group(grp.id)
                try:
                    st = scraper.scrape_group_calendar(grp, cache, storage, dry_run, force=force)
                except SeasonMismatchError as exc:
                    console.print(f"\n[red]❌ {exc}[/red]")
                    sys.exit(1)
                if st["skipped"]:
                    totals["skipped"] += 1
                else:
                    totals["groups"] += 1
                totals["matchdays"] += st["matchdays"]
                totals["matches"] += st["matches"]
                totals["fixtures"] += st["fixtures"]
                totals["errors"].extend(st.get("errors", []))
        elapsed = time.monotonic() - start

        console.print("\n[bold cyan]📊 RESUMEN FCTT CALENDARIO[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Grupos procesados:", str(totals["groups"]))
        summary.add_row("Grupos sin cambios:", str(totals["skipped"]))
        summary.add_row("Jornadas:", str(totals["matchdays"]))
        summary.add_row("Encuentros jugados:", str(totals["matches"]))
        summary.add_row("Partidos de calendario:", str(totals["fixtures"]))
        summary.add_row("Errores:", str(len(totals["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)
        for err in totals["errors"]:
            console.print(f"  [red]• {err}[/red]")


@fctt.command("territorial")
@click.option("--rep", required=True, type=click.Choice(["rtb", "rtg", "rtt"]),
              help="Representación territorial: rtb (Barcelona), rtg (Girona), rtt (Tarragona)")
@click.option("--group", default="", help="Filtrar por slug de grupo (ej. rtb-pref)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en el backend")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todos los grupos")
def fctt_territorial(rep: str, group: str, dry_run: bool, force: bool) -> None:
    """Cargar el CALENDARIO de una liga territorial de fctt.cat (RTB/RTG/RTT).

    Mismo sistema que TDM (/lligues/<slug>/). Categoría y nombre se derivan del h1
    (los slugs son irregulares); el group_id es el slug. Sin partidos individuales
    (esos están en la página de equipo; carga aparte).
    """
    _banner()
    terr = FCTT_TERRITORIALS[rep]
    cfg = Config.from_env()

    with HttpClient(rate_limit_seconds=2.5) as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        storage = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando almacenamiento...")
            if not storage.validate():
                console.print("[red]❌ Almacenamiento no válido. Usa --dry-run.[/red]")
                sys.exit(1)
            console.print("✅ Almacenamiento validado\n")

        slugs = list(terr.slugs)
        if group:
            gf = [g.strip() for g in group.split(",") if g.strip()]
            slugs = [s for s in slugs if s in gf]
        if not slugs:
            console.print("[yellow]⚠️  No hay grupos con el filtro indicado.[/yellow]")
            return

        competition = CompetitionIndex(
            competition_id=terr.id, name=terr.name, organization=terr.organization,
            season=FCTT.season, source_url=FCTT.index_url, categories=[],
        )
        try:
            storage.save_competition(competition, dry_run)
        except Exception as exc:
            logger.warning(f"No se pudo guardar competición raíz: {exc}")

        console.print(f"[dim]{terr.name} → {competition.competition_id} · {len(slugs)} grupos[/dim]\n")
        scraper = FcttScraper(http)
        start = time.monotonic()
        totals = {"groups": 0, "skipped": 0, "matchdays": 0, "matches": 0,
                  "fixtures": 0, "errors": []}
        for slug in slugs:
            if force:
                cache.invalidate_group(slug)
            stub = Group(id=slug, name=slug, category_id="", phase="regular",
                         source_url=territorial_group_url(slug))
            try:
                st = scraper.scrape_group_calendar(
                    stub, cache, storage, dry_run, force=force,
                    competition_id=terr.id, derive_meta=True,
                )
            except SeasonMismatchError as exc:
                console.print(f"\n[red]❌ {exc}[/red]")
                sys.exit(1)
            if st["skipped"]:
                totals["skipped"] += 1
            else:
                totals["groups"] += 1
            totals["matchdays"] += st["matchdays"]
            totals["matches"] += st["matches"]
            totals["fixtures"] += st["fixtures"]
            totals["errors"].extend(st.get("errors", []))
        elapsed = time.monotonic() - start

        console.print(f"\n[bold cyan]📊 RESUMEN {terr.name}[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Grupos procesados:", str(totals["groups"]))
        summary.add_row("Grupos sin cambios:", str(totals["skipped"]))
        summary.add_row("Jornadas:", str(totals["matchdays"]))
        summary.add_row("Encuentros jugados:", str(totals["matches"]))
        summary.add_row("Partidos de calendario:", str(totals["fixtures"]))
        summary.add_row("Errores:", str(len(totals["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)
        for err in totals["errors"]:
            console.print(f"  [red]• {err}[/red]")


@fctt.command("actas")
@click.option("--rep", required=True,
              type=click.Choice(["estatals", "rtb", "rtg", "rtt"]),
              help="estatals (TDM) o una territorial (rtb/rtg/rtt)")
@click.option("--group", default="", help="Filtrar por grupo/slug")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en el backend")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todas las páginas de equipo")
def fctt_actas(rep: str, group: str, dry_run: bool, force: bool) -> None:
    """Cargar los partidos individuales (actas) de fctt.cat desde las páginas de
    equipo. Requiere el calendario ya cargado (usa sus marcadores para saber qué
    equipos han jugado como local). Cada encuentro se lee una vez (equipo local).
    """
    _banner()
    cfg = Config.from_env()

    with HttpClient(rate_limit_seconds=2.5) as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        storage = make_storage(cfg)
        if not dry_run:
            console.print("🔑 Validando almacenamiento...")
            if not storage.validate():
                console.print("[red]❌ Almacenamiento no válido. Usa --dry-run.[/red]")
                sys.exit(1)
            console.print("✅ Almacenamiento validado\n")

        scraper = FcttScraper(http)
        # (group_url, group_id, competition_id) por grupo
        targets: list[tuple[str, str, str]] = []
        if rep == "estatals":
            for cat in scraper.get_index():
                for g in cat.groups:
                    targets.append((g.source_url, g.id, FCTT.id))
        else:
            terr = FCTT_TERRITORIALS[rep]
            for slug in terr.slugs:
                targets.append((territorial_group_url(slug), slug, terr.id))
        if group:
            gf = [g.strip() for g in group.split(",") if g.strip()]
            targets = [t for t in targets if t[1] in gf]
        if not targets:
            console.print("[yellow]⚠️  No hay grupos con el filtro indicado.[/yellow]")
            return

        console.print(f"[dim]actas · {rep} · {len(targets)} grupos[/dim]\n")
        start = time.monotonic()
        totals = {"groups": 0, "teams": 0, "encounters": 0, "individual": 0, "errors": []}
        for url, gid, comp_id in targets:
            try:
                st = scraper.scrape_group_actas(url, gid, comp_id, cache, storage, dry_run, force=force)
            except SeasonMismatchError as exc:
                console.print(f"\n[red]❌ {exc}[/red]")
                sys.exit(1)
            totals["groups"] += 0 if st["skipped"] else 1
            totals["teams"] += st["teams"]
            totals["encounters"] += st["encounters"]
            totals["individual"] += st["individual"]
            totals["errors"].extend(st.get("errors", []))
        elapsed = time.monotonic() - start

        console.print(f"\n[bold cyan]📊 RESUMEN ACTAS ({rep})[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Grupos:", str(totals["groups"]))
        summary.add_row("Equipos leídos:", str(totals["teams"]))
        summary.add_row("Encuentros con acta:", str(totals["encounters"]))
        summary.add_row("Partidos individuales:", str(totals["individual"]))
        summary.add_row("Errores:", str(len(totals["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)
        for err in totals["errors"][:20]:
            console.print(f"  [red]• {err}[/red]")


@fctt.command("run")
@click.option("--category", default="", help="Filtrar por categoría (ej. TDM)")
@click.option("--group", default="", help="Filtrar por grupo (ej. TDM_G1)")
@click.option("--jornada", default=0, type=int, help="Procesar solo esta jornada (ignora hashes)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en Firestore")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todo")
def fctt_run(category: str, group: str, jornada: int, dry_run: bool, force: bool) -> None:
    """Ejecutar el scraper de la FCTT (TDM y Copa Catalana Femenina)."""
    _banner()
    cfg = Config.from_env()

    # fctt.cat empieza a servir páginas vacías (con HTTP 200) si se le encadenan
    # peticiones a 1/s, así que a esta fuente se le va más despacio.
    with HttpClient(rate_limit_seconds=2.5) as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        firestore = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando credenciales Firestore...")
            if not firestore.validate():
                console.print(
                    "[red]❌ Credenciales Firestore inválidas. "
                    "Usa --dry-run para probar sin Firestore.[/red]"
                )
                sys.exit(1)
            console.print("✅ Credenciales Firestore validadas\n")

        scraper = FcttScraper(http)
        categories = scraper.get_index()

        # Apply filters
        if category:
            cat_filter = [c.strip() for c in category.split(",") if c.strip()]
            categories = [c for c in categories if c.id in cat_filter]
        if group:
            grp_filter = [g.strip() for g in group.split(",") if g.strip()]
            for cat in categories:
                cat.groups = [g for g in cat.groups if g.id in grp_filter]
            categories = [c for c in categories if c.groups]

        if not categories:
            console.print(
                "[yellow]⚠️  No hay categorías/grupos con los filtros indicados.[/yellow]"
            )
            return

        # Save competition root
        competition = CompetitionIndex(
            competition_id=FCTT.id,
            name=FCTT.name,
            organization=FCTT.organization,
            season=FCTT.season,
            source_url=FCTT.index_url,
            categories=categories,
        )
        try:
            firestore.save_competition(competition, dry_run)
        except Exception as exc:
            logger.warning(f"No se pudo guardar competición raíz: {exc}")

        if force:
            console.print("[dim]--force activo: se ignorarán los hashes de caché[/dim]\n")
        if jornada:
            console.print(f"[dim]--jornada {jornada}: solo se procesará esa jornada[/dim]\n")

        start = time.monotonic()
        total_stats = {
            "groups_processed": 0,
            "groups_skipped": 0,
            "matchdays": 0,
            "matches": 0,
            "individual_matches": 0,
            "errors": [],
        }

        for cat in categories:
            for grp in cat.groups:
                if force:
                    cache.invalidate_group(grp.id)  # grupo + todas sus jornadas
                try:
                    stats = scraper.scrape_group(
                        grp, cache, firestore, dry_run, only_jornada=jornada
                    )
                except SeasonMismatchError as exc:
                    # No seguimos: escribiríamos la temporada nueva encima de la
                    # anterior, que comparte números de jornada.
                    console.print(f"\n[red]❌ {exc}[/red]")
                    sys.exit(1)
                if stats["skipped"]:
                    total_stats["groups_skipped"] += 1
                else:
                    total_stats["groups_processed"] += 1
                total_stats["matchdays"] += stats["matchdays"]
                total_stats["matches"] += stats["matches"]
                total_stats["individual_matches"] += stats["individual_matches"]
                total_stats["errors"].extend(stats.get("errors", []))

        elapsed = time.monotonic() - start

        console.print("\n[bold cyan]📊 RESUMEN FCTT[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Grupos procesados:", str(total_stats["groups_processed"]))
        summary.add_row("Jornadas nuevas:", str(total_stats["matchdays"]))
        summary.add_row("Encuentros nuevos:", str(total_stats["matches"]))
        summary.add_row("Partidos individuales nuevos:", str(total_stats["individual_matches"]))
        summary.add_row("Errores:", str(len(total_stats["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)

        if total_stats["errors"]:
            console.print("\n[bold yellow]⚠️  ERRORES:[/bold yellow]")
            for err in total_stats["errors"]:
                console.print(f"  [red]• {err}[/red]")


@fctt.command("rankings")
@click.option("--edition", default="", help="Solo esta edición (ej. 10, ini)")
@click.option("--category", default="", help="Solo esta categoría (ej. SEM)")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en Firestore")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todo")
def fctt_rankings(edition: str, category: str, dry_run: bool, force: bool) -> None:
    """Cargar los rànquings mensuales de la FCTT."""
    _banner()
    cfg = Config.from_env()

    with HttpClient(rate_limit_seconds=2.5) as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        firestore = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando credenciales Firestore...")
            if not firestore.validate():
                console.print(
                    "[red]❌ Credenciales Firestore inválidas. "
                    "Usa --dry-run para probar sin Firestore.[/red]"
                )
                sys.exit(1)
            console.print("✅ Credenciales Firestore validadas\n")

        scraper = FcttRankingsScraper(http, cfg.pdfs_dir)
        refs = scraper.get_index()
        if edition:
            refs = [r for r in refs if r.number == edition]
        if not refs:
            console.print("[yellow]⚠️  No hay ediciones con los filtros indicados.[/yellow]")
            return

        start = time.monotonic()
        total = {"editions": 0, "categories": 0, "skipped": 0, "entries": 0, "errors": []}
        parsed: list = []
        pending_hashes: dict[str, str] = {}

        for ref in refs:
            edicion, stats = scraper.scrape_edition(ref, cache, category, force)
            total["categories"] += stats["categories"]
            total["skipped"] += stats["skipped"]
            total["entries"] += stats["entries"]
            total["errors"].extend(stats["errors"])
            pending_hashes.update(stats["hashes"])
            if edicion is None:
                continue
            total["editions"] += 1
            parsed.append(edicion)

            firestore.save_ranking_edition(
                FCTT.id,
                edicion.number,
                edicion.to_dict(),
                build_category_documents(edicion),
                dry_run,
            )

        # Los jugadores se escriben UNA vez con todas las ediciones fundidas:
        # hacerlo edición a edición multiplicaría las escrituras por 10.
        players = build_player_documents(parsed)
        if players:
            # Carga completa (sin filtros) → se sobrescribe: el payload ya trae
            # todo el histórico y así desaparecen las claves que ya no existan.
            # Con filtros solo tenemos parte, así que hay que fusionar.
            full_load = not edition and not category
            console.print(
                f"\n💾 Escribiendo {len(players)} jugadores "
                f"({'sobrescribiendo' if full_load else 'fusionando'})..."
            )
            firestore.save_ranking_players(
                FCTT.id, list(players.items()), dry_run, merge=not full_load
            )
            firestore.save_ranking_root(
                FCTT.id,
                {
                    "competition_id": FCTT.id,
                    "organization": FCTT.organization,
                    "season": FCTT.season,
                    "source_url": FCTT.rankings_index,
                    "last_edition": parsed[-1].number if parsed else "",
                    "player_count": len(players),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                dry_run,
            )

        # Los hashes se confirman al final y solo si todo lo anterior ha ido
        # bien: si la escritura falla, la próxima ejecución vuelve a parsear
        # (desde los PDFs ya descargados) en vez de darlo por hecho.
        for key, value in pending_hashes.items():
            cache.set_matchday_hash(key, value)

        elapsed = time.monotonic() - start
        console.print("\n[bold cyan]📊 RESUMEN RÀNQUINGS FCTT[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Ediciones procesadas:", str(total["editions"]))
        summary.add_row("Categorías nuevas:", str(total["categories"]))
        summary.add_row("Categorías sin cambios:", str(total["skipped"]))
        summary.add_row("Filas parseadas:", str(total["entries"]))
        summary.add_row("Jugadores únicos:", str(len(players)))
        summary.add_row("Errores:", str(len(total["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)

        if total["errors"]:
            console.print("\n[bold yellow]⚠️  ERRORES:[/bold yellow]")
            for err in total["errors"][:10]:
                console.print(f"  [red]• {err}[/red]")


@fctt.command("matches")
@click.option("--edition", default="", help="Solo esta edición (ej. 10)")
@click.option("--player", "players_filter", multiple=True,
              help="Reescribir solo estos jugadores (repetible). Ignora los hashes.")
@click.option("--dry-run/--no-dry-run", default=False, help="No escribe en Firestore")
@click.option("--force", is_flag=True, help="Ignorar hashes — reprocesar todo")
def fctt_matches(edition: str, players_filter: tuple, dry_run: bool, force: bool) -> None:
    """Cargar el histórico de partidos por jugador (`Partits_Jugats`).

    `--player` sirve para reparar a unos pocos jugadores sin reescribir la
    temporada entera: se parsea todo igual —hace falta para el `record`— pero
    solo se escribe lo suyo.
    """
    _banner()
    cfg = Config.from_env()

    with HttpClient(rate_limit_seconds=2.5) as http:
        cache = LocalCache(cfg.cache_dir, readonly=dry_run, backend=cfg.storage_backend)
        firestore = make_storage(cfg)

        if not dry_run:
            console.print("🔑 Validando credenciales Firestore...")
            if not firestore.validate():
                console.print(
                    "[red]❌ Credenciales Firestore inválidas. "
                    "Usa --dry-run para probar sin Firestore.[/red]"
                )
                sys.exit(1)
            console.print("✅ Credenciales Firestore validadas\n")

        scraper = FcttPartitsScraper(http, cfg.pdfs_dir)
        refs = scraper.get_index()
        if edition:
            refs = [r for r in refs if r.number == edition]
        if not refs:
            console.print("[yellow]⚠️  No hay ediciones con los filtros indicados.[/yellow]")
            return

        start = time.monotonic()
        total = {"editions": 0, "rows": 0, "matches": 0, "external": 0, "errors": []}
        all_logs: list = []          # todas las ediciones — hacen falta para el record
        to_write: list = []          # solo las que han cambiado
        pending_hashes: dict[str, str] = {}

        for ref in refs:
            logs, stats = scraper.scrape_edition(ref, cache, force)
            total["rows"] += stats["rows"]
            total["matches"] += stats["matches"]
            total["external"] += stats["external"]
            total["errors"].extend(stats["errors"])
            pending_hashes.update(stats["hashes"])
            if not logs:
                continue
            total["editions"] += 1
            all_logs.extend(logs)
            if players_filter:
                # Reparación dirigida: los hashes dicen que la edición ya está
                # hecha, pero justo por eso hay que ignorarlos.
                to_write.extend(l for l in logs if l.player_code in players_filter)
            elif stats["changed"]:
                to_write.extend(logs)

        if to_write:
            console.print(
                f"\n💾 Escribiendo {len(to_write)} documentos de partidos "
                f"({len({log.player_code for log in to_write})} jugadores)..."
            )
            firestore.save_player_match_logs(
                FCTT.id,
                [(log.player_code, log.edition, log.to_dict()) for log in to_write],
                dry_run,
            )

        # El `record` resume la temporada entera, así que solo se puede calcular
        # con todas las ediciones delante. Con --edition tenemos una sola y
        # escribirlo dejaría un resumen falso en la ficha del jugador.
        records: dict = {}
        if edition:
            console.print(
                "[dim]record: no se recalcula con --edition "
                "(necesita todas las ediciones).[/dim]"
            )
        elif players_filter and to_write:
            # Aquí sí se recalcula: se han parseado todas las ediciones, así que
            # el resumen de estos jugadores sale completo.
            records = build_records(all_logs, only=set(players_filter))
            console.print(f"💾 Actualizando el resumen de {len(records)} jugadores...")
            firestore.save_player_records(FCTT.id, list(records.items()), dry_run)
        elif to_write:
            # Se reescribe el de **todos** los jugadores, no solo el de los que
            # jugaron en las ediciones nuevas. Si la carga se ha hecho por
            # tandas con --edition —obligatorio en la histórica, que no cabe en
            # la cuota diaria—, quien solo jugó en una tanda anterior nunca
            # llegó a tener resumen. Son ~3.000 escrituras y solo ocurre cuando
            # algo ha cambiado.
            records = build_records(
                all_logs, only={log.player_code for log in all_logs}
            )
            console.print(f"💾 Actualizando el resumen de {len(records)} jugadores...")
            firestore.save_player_records(FCTT.id, list(records.items()), dry_run)

        # Los hashes se confirman al final y solo si las escrituras han ido
        # bien: si algo falla, la próxima ejecución reintenta en vez de darlo
        # por hecho.
        for key, value in pending_hashes.items():
            cache.set_matchday_hash(key, value)

        elapsed = time.monotonic() - start
        console.print("\n[bold cyan]📊 RESUMEN PARTITS JUGATS FCTT[/bold cyan]")
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_row("Ediciones procesadas:", str(total["editions"]))
        summary.add_row("Filas parseadas:", str(total["rows"]))
        summary.add_row("Documentos escritos:", str(len(to_write)))
        summary.add_row("Resúmenes actualizados:", str(len(records)))
        summary.add_row(
            "Rivales sin licencia FCTT:",
            f"{total['external']} "
            f"({total['external'] / total['matches']:.1%})" if total["matches"] else "0",
        )
        summary.add_row("Errores:", str(len(total["errors"])))
        summary.add_row("Tiempo total:", f"{elapsed:.1f}s")
        console.print(summary)

        if total["errors"]:
            console.print("\n[bold yellow]⚠️  ERRORES:[/bold yellow]")
            for err in total["errors"][:10]:
                console.print(f"  [red]• {err}[/red]")


@fctt.command("verify")
@click.option("--edition", default="", help="Solo esta edición (por defecto, todas las locales)")
def fctt_verify(edition: str) -> None:
    """Cuadrar los partidos jugados contra el ranking del mismo mes.

    Los dos PDFs de una edición son independientes, así que se comprueban el uno
    contra el otro: los partidos de un jugador tienen que sumar exactamente el
    PJ/PG/PP de su fila en el ranking, y sus ΔRK, el ΔRK del periodo. Si el
    emparejado del espejo atribuye un partido al jugador equivocado, salta aquí.

    No toca la red ni Firestore: trabaja sobre los PDFs ya descargados.
    """
    _banner()
    cfg = Config.from_env()
    pdfs = Path(cfg.pdfs_dir)

    ediciones = sorted(
        p.stem for p in (pdfs / "partits").glob("*.pdf")
        if not edition or p.stem == edition
    )
    if not ediciones:
        console.print(
            "[yellow]⚠️  No hay PDFs de partidos en local. "
            "Ejecuta antes `fctt matches`.[/yellow]"
        )
        return

    tabla = Table(show_header=True, header_style="bold cyan")
    for columna in ("Edición", "Jugadores", "PJ/PG/PP", "ΔRK", "Sin ranking"):
        tabla.add_column(columna, justify="right" if columna != "Edición" else "left")

    for numero in ediciones:
        ranking = _ranking_por_jugador(pdfs / "rankings", numero)
        if not ranking:
            console.print(
                f"[yellow]⚠️  Edición {numero}: no hay rànquings en local, "
                f"no se puede cuadrar.[/yellow]"
            )
            continue

        jugado: dict[str, list] = {}
        for row in parse_partits(str(pdfs / "partits" / f"{numero}.pdf")):
            acc = jugado.setdefault(row.licence, [0.0, 0, 0, 0])
            acc[0] += row.d_rk
            acc[1] += 1
            acc[2] += 1 if row.sets > row.opp_sets else 0
            acc[3] += 1 if row.sets < row.opp_sets else 0

        cuentas_mal = drk_mal = ausentes = 0
        for code, (d_rk, pj, pg, pp) in jugado.items():
            if code not in ranking:
                ausentes += 1
                continue
            r_drk, r_pj, r_pg, r_pp = ranking[code]
            if (pj, pg, pp) != (r_pj, r_pg, r_pp):
                cuentas_mal += 1
            # La fuente publica cada ΔRK redondeado a un decimal, así que sumar
            # N partidos arrastra hasta 0,05 × N de error frente al total que
            # publica el ranking. No es una discrepancia, es el redondeo.
            if abs(d_rk - r_drk) > 0.05 * (pj + 1) + 0.15:
                drk_mal += 1

        color = "green" if not cuentas_mal and not drk_mal else "red"
        tabla.add_row(
            numero,
            str(len(jugado)),
            f"[{color}]{len(jugado) - cuentas_mal - ausentes}/{len(jugado) - ausentes}[/{color}]",
            f"[{color}]{len(jugado) - drk_mal - ausentes}/{len(jugado) - ausentes}[/{color}]",
            str(ausentes),
        )

    console.print(tabla)
    console.print(
        "[dim]«Sin ranking» son jugadores que compitieron pero no tienen fila en "
        "el ranking de ese mes (sin clasificar, baja de licencia…).[/dim]"
    )


def _ranking_por_jugador(carpeta: Path, edition: str) -> dict[str, tuple]:
    """(ΔRK, PJ, PG, PP) de cada jugador en una edición del ranking."""
    ranking: dict[str, tuple] = {}
    for pdf in sorted(carpeta.glob(f"{edition}_*.pdf")):
        categoria = pdf.stem.split("_", 1)[1]
        if categoria[:1].isdigit():
            continue          # sobras del renombrado de categorías (40M → V40M)
        for entry in parse_ranking(str(pdf)):
            # El rating es global: un jugador que aparece en varias categorías
            # trae la misma fila en todas.
            ranking.setdefault(
                entry.player_code, (entry.d_rk, entry.pj, entry.pg, entry.pp)
            )
    return ranking


@fctt.command("list")
def fctt_list() -> None:
    """Listar las categorías y grupos disponibles en la FCTT."""
    _banner()
    with HttpClient() as http:
        scraper = FcttScraper(http)
        categories = scraper.get_index()

    if not categories:
        console.print("[yellow]No se encontraron categorías.[/yellow]")
        return

    table = Table(
        title="FCTT — Competiciones disponibles",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Categoría (--category)")
    table.add_column("Nombre")
    table.add_column("Grupos (--group)")

    for cat in categories:
        group_ids = ", ".join(g.id for g in cat.groups)
        table.add_row(cat.id, cat.name, group_ids)

    console.print(table)


if __name__ == "__main__":
    cli()
