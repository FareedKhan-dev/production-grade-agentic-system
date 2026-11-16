"""Registration, login and the two kinds of tokens."""

from datetime import (
    UTC,
    datetime,
    timedelta,
)

import jwt

from conftest import (
    PASSWORD,
    bearer,
    new_session,
    register,
)
from src.utils.auth import create_access_token


def login(client, email, password=PASSWORD):
    return client.post(
        "/api/v1/auth/login", data={"username": email, "password": password, "grant_type": "password"}
    )


def test_passwords_with_html_special_characters_can_log_in(client):
    user = register(client)
    response = login(client, user["email"])
    assert response.status_code == 200, response.text
    assert response.json()["token_type"] == "bearer"


def test_login_ignores_the_case_of_the_email(client):
    user = register(client)
    assert login(client, user["email"].upper()).status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_answer(client):
    user = register(client)
    wrong_password = login(client, user["email"], "Wr0ng&Password!")
    unknown_email = login(client, "nobody-" + user["email"])
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_passwords_longer_than_bcrypt_reads_are_refused(client):
    # 39 characters but 74 bytes: bcrypt would silently ignore the last two
    response = client.post(
        "/api/v1/auth/register", json={"email": "long-password@example.com", "password": "Aa1!" + "é" * 35}
    )
    assert response.status_code == 422


def test_the_same_email_cannot_register_twice(client):
    user = register(client)
    response = client.post("/api/v1/auth/register", json={"email": user["email"], "password": PASSWORD})
    assert response.status_code == 400


def test_a_user_token_does_not_open_a_chat_session(client):
    user = register(client)
    response = client.get("/api/v1/chatbot/messages", headers=bearer(user["token"]["access_token"]))
    assert response.status_code == 401


def test_a_session_token_does_not_act_as_the_user(client):
    session = new_session(client)
    assert client.get("/api/v1/auth/sessions", headers=session["headers"]).status_code == 401
    assert client.post("/api/v1/auth/session", headers=session["headers"]).status_code == 401


def test_a_token_signed_with_the_published_compose_default_is_rejected(client):
    user = register(client)
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"sub": str(user["id"]), "type": "user", "iat": now, "exp": now + timedelta(hours=1)},
        "supersecretkeythatshouldbechangedforproduction",
        algorithm="HS256",
    )
    assert client.post("/api/v1/auth/session", headers=bearer(forged)).status_code == 401


def test_an_unsigned_token_is_rejected(client):
    user = register(client)
    now = datetime.now(UTC)
    unsigned = jwt.encode(
        {"sub": str(user["id"]), "type": "user", "iat": now, "exp": now + timedelta(hours=1)}, None, algorithm="none"
    )
    assert client.post("/api/v1/auth/session", headers=bearer(unsigned)).status_code == 401


def test_an_expired_token_is_rejected(client):
    user = register(client)
    expired = create_access_token(str(user["id"]), "user", expires_delta=timedelta(seconds=-1))
    assert client.post("/api/v1/auth/session", headers=bearer(expired.access_token)).status_code == 401


def test_a_deleted_sessions_token_stops_working(client):
    session = new_session(client)
    response = client.delete(f"/api/v1/auth/session/{session['session_id']}", headers=session["headers"])
    assert response.status_code == 200
    assert client.get("/api/v1/chatbot/messages", headers=session["headers"]).status_code == 401


def test_a_session_cannot_rename_another(client):
    mine, theirs = new_session(client), new_session(client)
    response = client.patch(
        f"/api/v1/auth/session/{theirs['session_id']}/name", data={"name": "mine now"}, headers=mine["headers"]
    )
    assert response.status_code == 403


def test_session_names_are_stored_once_and_listed_as_saved(client):
    session = new_session(client)
    url = f"/api/v1/auth/session/{session['session_id']}/name"

    renamed = client.patch(url, data={"name": "Q&A <draft>"}, headers=session["headers"])
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Q&A draft"

    # Too long for the response model: refused up front instead of breaking the session list
    assert client.patch(url, data={"name": "n" * 101}, headers=session["headers"]).status_code == 422

    listed = client.get("/api/v1/auth/sessions", headers=bearer(session["user"]["token"]["access_token"]))
    assert listed.status_code == 200, listed.text
    assert [s["name"] for s in listed.json()] == ["Q&A draft"]
