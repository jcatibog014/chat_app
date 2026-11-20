"""Try Demo: a private sample team for each visitor, gone after 24 hours."""
from datetime import timedelta

from sqlalchemy import func, select

from paperplane import db, seed
from paperplane.dates import utcnow
from paperplane.models import DemoTeam, User

from .conftest import browser, is_logged_in, post, words


def start_demo(app):
    client = browser(app)
    response = post(client, "/demo")
    assert response.status_code == 302
    return client


def me(app, client):
    with client.session_transaction() as session:
        user_id = session["user_id"]
    with app.app_context():
        return db.session().get(User, user_id)


def test_try_demo_makes_a_team_and_logs_you_in_as_mika(app):
    client = start_demo(app)
    mika = me(app, client)
    assert mika.name == "Mika Santos" and mika.handle == "mika" and mika.is_demo and not mika.is_bot
    page = words(client.get("/chats", follow_redirects=True))
    assert "You are Mika" in page and "Demo" in page
    with app.app_context():
        team = db.session().scalars(select(User).where(User.demo_team_id == mika.demo_team_id)).all()
        assert sorted(person.handle for person in team) == ["ana", "ben", "carlo", "dana", "mika"]
        assert sum(person.is_bot for person in team) == 4


def test_each_visitor_gets_their_own_team(app):
    first, second = me(app, start_demo(app)), me(app, start_demo(app))
    assert first.demo_team_id != second.demo_team_id


def test_demo_people_cant_log_in_with_a_password(app, client):
    mika = me(app, start_demo(app))
    for password in ("", "password", mika.username):
        response = post(client, "/login", {"username": mika.username, "password": password})
        assert response.status_code == 401


def test_the_demo_cant_change_its_account(app):
    client = start_demo(app)
    response = post(client, "/settings", {"action": "name", "user_name": "Someone Else"})
    assert response.status_code == 403
    assert "can't be changed" in words(client.get("/settings"))


def test_an_old_demo_is_deleted_and_its_cookie_stops_working(app):
    client = start_demo(app)
    mika = me(app, client)
    with app.app_context():
        s = db.session()
        assert seed.purge(s, utcnow() + timedelta(hours=25)) == 1
        s.commit()
        assert s.scalar(select(func.count(DemoTeam.id))) == 0
        assert s.scalar(select(func.count(User.id))) == 0
    assert not is_logged_in(client)
    # A new demo never gets the old one's ids.
    newer = me(app, start_demo(app))
    assert newer.id > mika.id + 4 and newer.demo_team_id > mika.demo_team_id


def test_an_expired_demo_logs_out_even_before_it_is_deleted(app):
    client = start_demo(app)
    mika = me(app, client)
    with app.app_context():
        s = db.session()
        s.get(DemoTeam, mika.demo_team_id).expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert not is_logged_in(client)


def test_purge_demos_command(app):
    start_demo(app)
    result = app.test_cli_runner().invoke(args=["purge-demos"])
    assert result.exit_code == 0 and "Deleted 0 demos." in result.output
