"""커스텀 CLI 명령. `flask --app run create-admin` 등으로 사용."""
import click
from flask.cli import with_appcontext

from . import models


def register_cli(app):
    app.cli.add_command(create_admin)


@click.command("create-admin")
@click.option("--username", prompt=True)
@click.option("--name", prompt=True, default="관리사무소")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@with_appcontext
def create_admin(username, name, password):
    """관리사무소(admin) 계정을 생성한다. (Supabase REST)"""
    username = username.strip().lower()
    if models.users_get_by_username(username):
        click.echo(f"이미 존재하는 아이디입니다: {username}")
        return
    models.users_create({
        "username": username,
        "password_hash": models.make_password_hash(password),
        "name": name,
        "dong": "-",
        "ho": "-",
        "role": "admin",
        "status": "approved",
    })
    click.echo(f"관리자 계정 생성 완료: {username}")
