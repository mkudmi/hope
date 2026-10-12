from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections

from core.models import Operation
from core.services import DomainError, prepare_operation


@pytest.mark.django_db(transaction=True)
def test_postgres_serializes_two_manual_renewals(sub, admin):
    if connection.vendor != "postgresql":
        pytest.skip("Production row locking requires PostgreSQL")

    def submit():
        try:
            prepare_operation(
                sub.pk, admin, kind="renew", reason="Concurrent manual action", version=sub.version, months=1
            )
            return "queued"
        except DomainError:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(results) == ["conflict", "queued"]
    assert Operation.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_postgres_serializes_emergency_hour(sub, user):
    if connection.vendor != "postgresql":
        pytest.skip("Production row locking requires PostgreSQL")
    from tests.test_emergency import expire

    expire(sub)

    def submit():
        try:
            prepare_operation(sub.pk, user, kind="emergency", reason="", version=sub.version)
            return "queued"
        except DomainError:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(results) == ["conflict", "queued"]
    assert Operation.objects.filter(kind="emergency").count() == 1


@pytest.mark.django_db(transaction=True)
def test_postgres_serializes_purchase_and_manual_provision(admin, settings):
    if connection.vendor != "postgresql":
        pytest.skip("Production row locking requires PostgreSQL")
    settings.TARIFFS_APPROVED = True
    from core.models import PurchaseOrder, TelegramIdentity, User
    from core.orders import bot_contact, offer_key, place_order, plans, prepare_order, review_order

    contact = bot_contact(101, 101, "Alice")
    plan = plans()[1]

    def buy():
        try:
            return place_order(contact, plan["code"], offer_key(plan)).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        keys = list(pool.map(lambda _: buy(), range(2)))
    assert keys[0] == keys[1] and PurchaseOrder.objects.count() == 1
    review_order(keys[0], admin, "confirm", "Verified manually")

    def provision():
        try:
            return prepare_order(keys[0], admin).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        keys = list(pool.map(lambda _: provision(), range(2)))
    assert keys[0] == keys[1] and Operation.objects.count() == 1
    assert User.objects.filter(is_staff=False).count() == 1
    assert TelegramIdentity.objects.count() == 1
