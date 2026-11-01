from datetime import datetime

import pytest

from app import tools
from app.clock import STORE_TZ
from app.tools import (
    create_support_ticket,
    get_order_status,
    get_product_details,
    get_store_policies,
    run_tool,
    search_products,
)

# Order 4 belongs to customer 1: Lucas Mendes da Silva, (67) 99812-3456.
ORDER_ID = 4
FULL_NAME = "Lucas Mendes da Silva"
PHONE = "(67) 99812-3456"


class TestOrderAuthorization:
    def test_requires_identification(self):
        assert get_order_status(ORDER_ID)["authorized"] is False

    @pytest.mark.parametrize("name", ["a", "Silva", "Lucas", "Lucas Silva", "Lucas Mendes da Silva Junior"])
    def test_rejects_partial_or_wrong_names(self, name):
        result = get_order_status(ORDER_ID, customer_name=name)
        assert result["authorized"] is False
        assert "order" not in result

    @pytest.mark.parametrize("name", [FULL_NAME, "lucas mendes silva", "LUCAS MENDES DA SILVA"])
    def test_accepts_full_name_variations(self, name):
        assert get_order_status(ORDER_ID, customer_name=name)["authorized"] is True

    @pytest.mark.parametrize("phone", [PHONE, "67998123456", "+55 67 99812-3456"])
    def test_accepts_phone_with_ddd(self, phone):
        assert get_order_status(ORDER_ID, phone=phone)["authorized"] is True

    def test_rejects_phone_without_ddd(self):
        assert get_order_status(ORDER_ID, phone="99812-3456")["authorized"] is False

    def test_missing_order_and_wrong_identity_look_the_same(self):
        missing = get_order_status(999, customer_name=FULL_NAME)
        mismatch = get_order_status(ORDER_ID, customer_name="Maria Souza")
        assert missing == mismatch

    def test_does_not_expose_contact_data(self):
        order = get_order_status(ORDER_ID, phone=PHONE)["order"]
        assert "phone" not in str(order).lower()
        assert "email" not in str(order).lower()


class TestAfterSales:
    def test_windows_are_computed_from_today(self, monkeypatch):
        # Order 4: purchased 2025-12-10, estimated delivery 2025-12-22.
        monkeypatch.setattr(tools, "now_local", lambda: datetime(2025, 12, 26, 10, tzinfo=STORE_TZ))
        after_sales = get_order_status(ORDER_ID, phone=PHONE)["order"]["after_sales"]
        assert after_sales["withdrawal_7_days"] == {"deadline": "2025-12-29", "within_deadline": True, "days_left": 3}
        assert after_sales["defect_exchange_30_days"]["deadline"] == "2026-01-09"

    def test_expired_windows(self, monkeypatch):
        monkeypatch.setattr(tools, "now_local", lambda: datetime(2026, 11, 1, 10, tzinfo=STORE_TZ))
        after_sales = get_order_status(ORDER_ID, phone=PHONE)["order"]["after_sales"]
        assert after_sales["withdrawal_7_days"]["within_deadline"] is False
        assert after_sales["legal_warranty_90_days"]["within_deadline"] is False

    def test_status_and_payment_in_portuguese(self):
        order = get_order_status(ORDER_ID, phone=PHONE)["order"]
        assert order["status_pt"] == "entregue"
        assert order["payment_method"] == "boleto bancário"


class TestCatalog:
    @pytest.mark.parametrize("category", ["violao", "Violões", "VIOLÕES", "violoes"])
    def test_category_is_accent_and_plural_insensitive(self, category):
        assert search_products(category=category)["total_count"] == 33

    def test_multi_word_query(self):
        result = search_products(query="violão nylon")
        assert result["total_count"] > 0

    def test_total_count_is_not_the_page_size(self):
        result = search_products(category="violões", max_price=1000, limit=5)
        assert result["returned"] == 5
        assert result["total_count"] > 5

    def test_only_available_by_default(self):
        names = [p["name"] for p in search_products(category="violões", limit=20)["products"]]
        assert all(p["available_for_sale"] for p in search_products(category="violões", limit=20)["products"])
        assert "Giannini GF-3D Dreadnought Sunburst" not in names  # stock 0

    def test_price_filter_uses_promotional_price(self):
        promo = get_product_details(product_id=94)["product"]
        assert promo["active_promotion"] is not None
        final = promo["active_promotion"]["promo_price_brl"]
        ids = [p["product_id"] for p in search_products(max_price=final, min_price=final, limit=20)["products"]]
        assert 94 in ids

    def test_pix_discount_does_not_stack_with_promotion(self):
        promo = get_product_details(product_id=94)["product"]
        assert promo["pix_price_brl"] is None

    def test_pix_discount_on_list_price(self):
        product = get_product_details(name="Takamine GD20")["product"]
        assert product["active_promotion"] is None
        assert product["pix_price_brl"] == round(product["list_price_brl"] * 0.95, 2)

    @pytest.mark.parametrize(
        ("product_id", "expected_max"),
        [(81, 6), (95, 12)],  # R$ 599,90 -> 6x de R$ 99,98 ; R$ 2.199 -> 12x
    )
    def test_installments_follow_policy(self, product_id, expected_max):
        card = get_product_details(product_id=product_id)["product"]["credit_card"]
        assert card["max_installments_no_interest"] == expected_max

    def test_unavailable_product_suggests_available_alternatives(self):
        result = get_product_details(product_id=96)
        assert result["product"]["available_for_sale"] is False
        assert result["available_alternatives"]
        for alt in result["available_alternatives"]:
            assert get_product_details(product_id=alt["product_id"])["product"]["available_for_sale"]

    def test_unknown_category(self):
        assert search_products(category="amplificadores")["total_count"] == 0


class TestPoliciesAndTickets:
    def test_return_question_hits_exchange_policy(self):
        sections = get_store_policies("me arrependi, posso devolver?")["sections"]
        assert sections[0]["section_id"] == "trocas"
        assert "7 (sete) dias" in sections[0]["content"]

    def test_ticket_requires_valid_contact(self):
        result = create_support_ticket("reclamacao", "Ana", "123", "violão chegou riscado")
        assert result["created"] is False

    def test_ticket_protocol(self):
        result = create_support_ticket("reclamacao", "Ana Ferreira", "ana@example.com", "violão chegou riscado")
        assert result["created"] is True
        assert result["protocol"].startswith("EMP-")

    def test_run_tool_hides_internal_errors(self):
        assert run_tool("does_not_exist") == {"error": "unknown_tool"}
        assert run_tool("get_product_details", {"bogus": 1})["error"] == "invalid_arguments"
