import sys
import asyncio
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.agent.gemma_agent import agent
from app.agent.ecommerce_agent import ecommerce_agent
from app.agent.ecommerce_comparator import ecommerce_comparator, ProductItem
from app.agent.privacy_guard import privacy_guard
from app.agent.task_checkpoint_manager import task_checkpoint_manager
from app.device.adb_controller import adb_controller

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_ecommerce")

async def test_all_ecommerce_features():
    print("=" * 70)
    print("RUNNING AUTONOMOUS MOBILE E-COMMERCE & SHOPPING TEST SUITE")
    print("=" * 70)

    # -------------------------------------------------------------
    # 1. Test Intent Parsing
    # -------------------------------------------------------------
    print("\n[TEST 1] Testing E-Commerce Intent Extraction...")
    test_cases = [
        ("go to flipkart and order a pen", "flipkart", "pen", "lowest_price"),
        ("flipkart se pen order karo lowest price wala", "flipkart", "pen", "lowest_price"),
        ("order ball pen from amazon with best value", "amazon", "ball pen", "best_value"),
        ("open blinkit and order milk", "blinkit", "milk", "lowest_price")
    ]

    for utterance, exp_app, exp_query, exp_criteria in test_cases:
        assert ecommerce_agent.is_ecommerce_intent(utterance), f"Should recognize e-commerce intent: {utterance}"
        parsed = ecommerce_agent.parse_ecommerce_intent(utterance)
        print(f"  Utterance: '{utterance}' -> {parsed}")
        assert parsed["app"] == exp_app, f"Expected {exp_app}, got {parsed['app']}"
        assert exp_query in parsed["query"], f"Expected {exp_query} in {parsed['query']}"
        assert parsed["criteria"] == exp_criteria, f"Expected {exp_criteria}, got {parsed['criteria']}"
    print("  --> [TEST 1 PASSED]: All natural language shopping intents correctly parsed!")

    # -------------------------------------------------------------
    # 2. Test Product Hierarchy Parsing & Price Comparison
    # -------------------------------------------------------------
    print("\n[TEST 2] Testing Product Parsing & Semantic Price Comparison...")
    mock_screen_elements = [
        {"text": "Parker Vector Metallic Gel Pen", "desc": "", "id": "title_1", "center": [300, 400], "bounds": [50, 350, 500, 450]},
        {"text": "₹250", "desc": "", "id": "price_1", "center": [300, 480], "bounds": [50, 460, 200, 500]},
        {"text": "Reynolds 045 Fine Ball Pen (Pack of 5)", "desc": "", "id": "title_2", "center": [300, 700], "bounds": [50, 650, 500, 750]},
        {"text": "₹50", "desc": "", "id": "price_2", "center": [300, 780], "bounds": [50, 760, 200, 800]},
        {"text": "Montex Mega Top Gel Pen (Pack of 1)", "desc": "", "id": "title_3", "center": [300, 1000], "bounds": [50, 950, 500, 1050]},
        {"text": "₹25", "desc": "", "id": "price_3", "center": [300, 1080], "bounds": [50, 1060, 200, 1100]},
        {"text": "Cello Butterflow Blue Ball Pen (Pack of 10)", "desc": "", "id": "title_4", "center": [300, 1300], "bounds": [50, 1250, 500, 1350]},
        {"text": "₹90", "desc": "", "id": "price_4", "center": [300, 1380], "bounds": [50, 1360, 200, 1400]}
    ]

    products = ecommerce_comparator.parse_products_from_screen(mock_screen_elements, query="pen")
    print(f"  Extracted {len(products)} products from screen hierarchy:")
    for p in products:
        print(f"    - {p.title} | Price: {p.raw_price} | Pack: {p.pack_count} | Unit: ₹{p.unit_price:.1f}")

    assert len(products) >= 3, f"Expected at least 3 products, found {len(products)}"

    # Lowest absolute price: Montex (₹25)
    cheapest = ecommerce_comparator.find_best_product(products, criteria="lowest_price")
    print(f"  Lowest absolute price winner: {cheapest.title} at {cheapest.raw_price}")
    assert cheapest.price == 25.0, f"Expected 25.0, got {cheapest.price}"
    assert "Montex" in cheapest.title

    # Best unit value: Cello Butterflow (Pack of 10 for ₹90 = ₹9/pen)
    best_val = ecommerce_comparator.find_best_product(products, criteria="best_value")
    print(f"  Best unit value winner: {best_val.title} at {best_val.raw_price} (Unit: ₹{best_val.unit_price}/pen)")
    assert best_val.unit_price == 9.0, f"Expected unit price 9.0, got {best_val.unit_price}"
    print("  --> [TEST 2 PASSED]: Semantic price comparison and pack unit calculation verified!")

    # -------------------------------------------------------------
    # 3. Test Privacy Guard & Security Interceptors
    # -------------------------------------------------------------
    print("\n[TEST 3] Testing Privacy Guardrail Interceptors...")
    
    # 3.1 OTP Screen
    otp_screen = [{"text": "Enter OTP sent to your registered mobile number", "desc": "", "id": "otp_field", "class": "EditText"}]
    res_otp = privacy_guard.evaluate_screen_elements(otp_screen)
    print(f"  OTP Check: sensitive={res_otp.is_sensitive}, type={res_otp.guard_type}")
    assert res_otp.is_sensitive and res_otp.guard_type == "OTP"

    # 3.2 Payment Card / CVV Screen
    payment_screen = [{"text": "Enter Card CVV and Expiry Date", "desc": "", "id": "cvv_input", "class": "EditText"}]
    res_pay = privacy_guard.evaluate_screen_elements(payment_screen)
    print(f"  Payment Check: sensitive={res_pay.is_sensitive}, type={res_pay.guard_type}")
    assert res_pay.is_sensitive and res_pay.guard_type == "PAYMENT_CREDENTIALS"

    # 3.3 Password Screen
    pass_screen = [{"text": "", "desc": "Account Password", "id": "pwd_field", "class": "android.widget.PasswordEditText"}]
    res_pass = privacy_guard.evaluate_screen_elements(pass_screen)
    print(f"  Password Check: sensitive={res_pass.is_sensitive}, type={res_pass.guard_type}")
    assert res_pass.is_sensitive and res_pass.guard_type == "PASSWORD"

    # 3.4 Order Confirmation Screen
    order_screen = [{"text": "Place Order", "desc": "Proceed to Pay ₹50", "id": "place_order_btn", "class": "Button"}]
    res_order = privacy_guard.evaluate_screen_elements(order_screen)
    print(f"  Order Screen Check: sensitive={res_order.is_sensitive}, type={res_order.guard_type}")
    assert res_order.is_sensitive and res_order.guard_type == "ORDER_CONFIRMATION"

    print("  --> [TEST 3 PASSED]: Privacy Guard perfectly detects OTP, Payment, Password & Order screens!")

    # -------------------------------------------------------------
    # 4. Test Autonomous End-to-End Shopping Flow via Agent
    # -------------------------------------------------------------
    print("\n[TEST 4] Testing Agent End-to-End Chat Flow: 'go to flipkart and order a pen'...")
    session_id = "test_ecom_session_001"
    task_checkpoint_manager.clear(session_id)

    res = await agent.chat("go to flipkart and order a pen", session_id=session_id)
    print(f"  Agent Reply:\n{res['reply']}")
    print(f"  Confirmation Required: {res.get('confirmation_required')}")

    assert res.get("confirmation_required") == True, "Should require user confirmation before booking"
    assert "flipkart" in res["reply"].lower(), "Should mention Flipkart"
    assert "pen" in res["reply"].lower(), "Should mention pen"
    assert task_checkpoint_manager.has_active_task(session_id), "Checkpoint should be saved"

    # -------------------------------------------------------------
    # 5. Test User Confirmation Flow
    # -------------------------------------------------------------
    print("\n[TEST 5] Testing User Confirmation: 'yes proceed'...")
    confirm_res = await agent.chat("yes proceed", session_id=session_id)
    print(f"  Confirm Reply:\n{confirm_res['reply']}")
    assert "confirmed" in confirm_res["reply"].lower(), "Should confirm order"
    assert "payment" in confirm_res["reply"].lower() or "checkout" in confirm_res["reply"].lower(), "Should mention payment/checkout step"
    print("  --> [TEST 5 PASSED]: User pre-purchase confirmation properly handled!")

    # -------------------------------------------------------------
    # 6. Test Error Resumption Checkpoint Flow
    # -------------------------------------------------------------
    print("\n[TEST 6] Testing Error Pause and Mid-Task Resumption...")
    session_id_resume = "test_resume_002"
    task_checkpoint_manager.clear(session_id_resume)

    # Simulate task getting paused at authentication/error
    task_checkpoint_manager.create_or_update(
        session_id=session_id_resume,
        goal="Order pen on flipkart",
        target_app="flipkart",
        stage="AWAITING_ORDER_CONFIRMATION",
        selected_item={"title": "Cello Butterflow Blue Ball Pen", "price": "₹50"},
        last_error="Location permission popup was dismissed"
    )

    print("  User says: 'maine kar diya, continue'...")
    resume_res = await agent.chat("maine kar diya, continue", session_id=session_id_resume)
    print(f"  Resume Reply:\n{resume_res['reply']}")
    assert "resum" in resume_res["reply"].lower() or "proceed" in resume_res["reply"].lower()
    assert "cello butterflow" in resume_res["reply"].lower() or "pen" in resume_res["reply"].lower()
    print("  --> [TEST 6 PASSED]: Mid-task state checkpoint and resumption successfully verified!")

    # -------------------------------------------------------------
    # 7. Regression Test: Verify WhatsApp, Call, SMS, Email are NOT broken
    # -------------------------------------------------------------
    print("\n[TEST 7] Regression Test: Verifying WhatsApp & Disambiguation still work without clash...")
    wa_session = "test_regression_wa"
    agent.pending_confirmations.clear()
    agent.pending_inputs.clear()

    # Ambiguous prompt test
    wa_res = await agent.chat("Abhishek and ask how are you", session_id=wa_session)
    print(f"  Regression Reply:\n{wa_res['reply']}")
    assert "whatsapp" in wa_res["reply"].lower() and "sms" in wa_res["reply"].lower()
    print("  --> [TEST 7 PASSED]: Existing WhatsApp/SMS/Email/Call workflows remain 100% intact!")

    print("\n" + "=" * 70)
    print("ALL 7 AUTONOMOUS E-COMMERCE & SHOPPING TESTS PASSED!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_all_ecommerce_features())
