"""
Tests for JWT authentication endpoints and route access control.

Covers:
- POST /api/auth/register/ — account creation, validation, duplicate check
- POST /api/auth/token/   — login returns access + refresh tokens
- GET  /api/route/        — returns 401 without token, 200 with valid token
"""
import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def existing_user(db):
    return User.objects.create_user(username="testuser", password="testpass123")


def auth_client(user):
    """Returns an APIClient pre-loaded with a valid Bearer token for the given user."""
    c = APIClient()
    token = RefreshToken.for_user(user)
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return c


# ── Registration ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRegisterView:
    def test_successful_registration_returns_201(self, client):
        res = client.post("/api/auth/register/", {
            "username": "newuser",
            "password": "securepass1",
            "password2": "securepass1",
        }, format="json")
        assert res.status_code == 201

    def test_successful_registration_creates_user(self, client):
        client.post("/api/auth/register/", {
            "username": "newuser2",
            "password": "securepass1",
            "password2": "securepass1",
        }, format="json")
        assert User.objects.filter(username="newuser2").exists()

    def test_response_contains_message(self, client):
        res = client.post("/api/auth/register/", {
            "username": "newuser3",
            "password": "securepass1",
            "password2": "securepass1",
        }, format="json")
        assert "message" in res.json()

    def test_mismatched_passwords_returns_400(self, client):
        res = client.post("/api/auth/register/", {
            "username": "baduser",
            "password": "securepass1",
            "password2": "differentpass",
        }, format="json")
        assert res.status_code == 400
        assert "error" in res.json()

    def test_short_password_returns_400(self, client):
        res = client.post("/api/auth/register/", {
            "username": "baduser",
            "password": "short",
            "password2": "short",
        }, format="json")
        assert res.status_code == 400

    def test_missing_username_returns_400(self, client):
        res = client.post("/api/auth/register/", {
            "username": "",
            "password": "securepass1",
            "password2": "securepass1",
        }, format="json")
        assert res.status_code == 400

    def test_duplicate_username_returns_400(self, client, existing_user):
        res = client.post("/api/auth/register/", {
            "username": "testuser",
            "password": "securepass1",
            "password2": "securepass1",
        }, format="json")
        assert res.status_code == 400
        assert "taken" in res.json()["error"].lower()


# ── Token obtain ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenView:
    def test_valid_credentials_return_tokens(self, client, existing_user):
        res = client.post("/api/auth/token/", {
            "username": "testuser",
            "password": "testpass123",
        }, format="json")
        assert res.status_code == 200
        assert "access" in res.json()
        assert "refresh" in res.json()

    def test_invalid_password_returns_401(self, client, existing_user):
        res = client.post("/api/auth/token/", {
            "username": "testuser",
            "password": "wrongpassword",
        }, format="json")
        assert res.status_code == 401

    def test_nonexistent_user_returns_401(self, client):
        res = client.post("/api/auth/token/", {
            "username": "ghost",
            "password": "doesntmatter",
        }, format="json")
        assert res.status_code == 401


# ── Route endpoint access control ────────────────────────────────────────────

@pytest.mark.django_db
class TestRouteAuth:
    def test_route_without_token_returns_401(self, client):
        res = client.get("/api/route/?start=Chicago, IL&end=Denver, CO")
        assert res.status_code == 401

    def test_route_with_invalid_token_returns_401(self, client):
        client.credentials(HTTP_AUTHORIZATION="Bearer not.a.real.token")
        res = client.get("/api/route/?start=Chicago, IL&end=Denver, CO")
        assert res.status_code == 401

    def test_route_with_valid_token_passes_auth(self, existing_user):
        from unittest.mock import patch
        c = auth_client(existing_user)
        with patch("route_planner.views.geocode_address", return_value=(None, None)):
            res = c.get("/api/route/?start=Chicago, IL&end=Denver, CO")
        # geocode returns None so we get 400, but NOT 401 — auth passed
        assert res.status_code == 400

    def test_health_endpoint_requires_no_token(self, client):
        res = client.get("/api/health/")
        assert res.status_code == 200
