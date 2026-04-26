"""Typer CLI: bot, doctor, list-reminders, etc."""

from __future__ import annotations

import asyncio

import typer

from nudgr import __version__
from nudgr.observability.logging import configure_logging, logger

app = typer.Typer(
    name="nudgr",
    help="Voice-first Telegram reminder bot.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _bootstrap() -> None:
    configure_logging()


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"nudgr {__version__}")


@app.command()
def doctor() -> None:
    """Validate env, database, LLM access, Telegram token."""
    from nudgr.doctor import print_checks, run_all

    logger.info("Running doctor checks…")
    checks = asyncio.run(run_all())
    ok = print_checks(checks)
    if ok:
        logger.info("All checks passed.")
        raise typer.Exit(code=0)
    logger.error("One or more checks failed — fix and re-run.")
    raise typer.Exit(code=1)


@app.command()
def bot() -> None:
    """Run the long-running Telegram bot (polling + scheduler loop)."""
    from nudgr.bot import run_bot

    asyncio.run(run_bot())


@app.command("list-reminders")
def list_reminders(
    limit: int = typer.Option(20, "--limit", "-n"),
    status: str = typer.Option(
        "active", "--status", help="active | done | cancelled | expired | all"
    ),
) -> None:
    """List reminders from the DB (debug helper)."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from nudgr.db.models import Reminder
    from nudgr.db.session import session_scope

    with session_scope() as s:
        stmt = select(Reminder).order_by(Reminder.created_at.desc()).limit(limit)
        if status != "all":
            stmt = stmt.where(Reminder.status == status)
        rows = s.execute(stmt).scalars().all()
        if not rows:
            typer.echo("(no rows)")
            return
        now = datetime.now(timezone.utc)
        for r in rows:
            eta = "—"
            if r.next_ping_at:
                delta_min = int((r.next_ping_at - now).total_seconds() / 60)
                if delta_min >= 0:
                    eta = f"in {delta_min}m"
                else:
                    eta = f"{-delta_min}m ago (overdue)"
            typer.echo(
                f"{r.status:<10} 🔁{r.ping_count}  next={eta:<15}  {r.text[:80]}"
            )


@app.command("send-test")
def send_test(
    text: str = typer.Argument("Test reminder from CLI"),
    minutes: int = typer.Option(1, "--in", help="Fire in N minutes."),
) -> None:
    """Insert a test reminder bypassing the parser (e.g. to verify the scheduler)."""
    from datetime import datetime, timedelta, timezone

    from nudgr.config import settings
    from nudgr.db.models import Reminder, User
    from nudgr.db.session import session_scope
    from sqlalchemy import select

    if settings.telegram_user_id == 0:
        logger.error("TELEGRAM_USER_ID not set — can't insert test reminder.")
        raise typer.Exit(code=2)

    fire_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    with session_scope() as s:
        user = s.execute(
            select(User).where(User.telegram_user_id == settings.telegram_user_id)
        ).scalar_one_or_none()
        if user is None:
            user = User(
                telegram_user_id=settings.telegram_user_id,
                timezone=settings.timezone,
            )
            s.add(user)
            s.flush()
        s.add(
            Reminder(
                user_id=user.id,
                chat_id=settings.telegram_user_id,
                text=text,
                input_kind="text",
                fire_at=fire_at,
                next_ping_at=fire_at,
                status="active",
            )
        )
    logger.info(f"Inserted test reminder fire_at={fire_at.isoformat(timespec='seconds')}")


if __name__ == "__main__":
    app()
