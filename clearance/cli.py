"""Command line.

    clearance run packet.pdf --pack medicare-pap-osa-initial
    clearance packs
    clearance check packs/my-new-pack.yaml
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .engine.pack import PackError, list_packs, load_pack

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Intake and qualification engine for DME orders (synthetic data only).")


@app.command()
def run(packet: Path = typer.Argument(..., exists=True, dir_okay=False, help="Faxed packet PDF"),
        pack: str = typer.Option(..., "--pack", "-p", help="Criteria pack id or path"),
        out: Path | None = typer.Option(None, help="Write the decision JSON here"),
        letter: Path | None = typer.Option(None, help="Write the outreach letter here")):
    """Process one packet: extract, check every rule, route, and draft any outreach."""
    from .pipeline import Pipeline

    try:
        criteria = load_pack(pack)
    except PackError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(2) from err
    decision = Pipeline().run(packet, criteria, order_id=packet.stem)
    payload = decision.model_dump(mode="json", exclude={"extraction": {"pages"}})
    text = json.dumps(payload, indent=2)
    if out:
        out.write_text(text)
    else:
        typer.echo(text)
    if decision.outreach:
        if letter:
            letter.write_text(decision.outreach)
        else:
            typer.echo("\n--- outreach draft ---\n" + decision.outreach)
    typer.echo(f"\n{packet.name}: {decision.route.value.upper()}  " + " | ".join(decision.reasons),
               err=True)


@app.command()
def packs():
    """List installed criteria packs."""
    for p in list_packs():
        typer.echo(f"{p.pack_id:<28} v{p.version:<7} {len(p.rules)} rules  {p.item.name}")


@app.command()
def check(path: Path = typer.Argument(..., exists=True, dir_okay=False)):
    """Validate a pack file and print readable problems."""
    try:
        p = load_pack(path)
    except PackError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from err
    typer.echo(f"{path.name}: valid ({len(p.rules)} rules, {len(p.extraction.fields)} fields)")


if __name__ == "__main__":
    app()
