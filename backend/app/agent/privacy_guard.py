import re
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class PrivacyCheckResult:
    is_sensitive: bool
    guard_type: str  # "NONE" | "OTP" | "PAYMENT_CREDENTIALS" | "PASSWORD" | "ORDER_CONFIRMATION"
    message: str
    requires_user_action: bool
    matched_term: Optional[str] = None

class PrivacyGuard:
    """
    Autonomous Mobile Agent Privacy & Security Guard.
    Inspects screen elements, input fields, and UI text to protect user privacy.
    Prevents the agent from autonomously typing passwords, submitting OTPs, or placing orders without explicit permission.
    """

    OTP_PATTERNS = [
        r"\botp\b",
        r"one\s*time\s*password",
        r"verification\s*code",
        r"enter\s*code\s*sent",
        r"enter\s*otp",
        r"resend\s*otp",
        r"auto-filling\s*code"
    ]

    PAYMENT_CREDENTIALS_PATTERNS = [
        r"\bcvv\b",
        r"\bcvc\b",
        r"card\s*number",
        r"debit\s*card",
        r"credit\s*card",
        r"expiry\s*date",
        r"valid\s*thru",
        r"upi\s*pin",
        r"enter\s*pin",
        r"\bmpin\b",
        r"net\s*banking",
        r"bank\s*password"
    ]

    PASSWORD_PATTERNS = [
        r"enter\s*password",
        r"your\s*password",
        r"account\s*password",
        r"login\s*pin"
    ]

    PHONE_NUMBER_LOGIN_PATTERNS = [
        r"log\s*in\s*to\s*complete\s*your\s*shopping",
        r"phone\s*number",
        r"mobile\s*number",
        r"enter\s*phone",
        r"enter\s*mobile",
        r"\+91",
        r"sign\s*in\s*to\s*continue",
        r"login\s*to\s*continue",
        r"use\s*email-id"
    ]

    ORDER_CONFIRMATION_PATTERNS = [
        r"place\s*order",
        r"buy\s*now",
        r"proceed\s*to\s*buy",
        r"proceed\s*to\s*pay",
        r"pay\s*now",
        r"order\s*summary",
        r"complete\s*purchase",
        r"make\s*payment"
    ]

    @classmethod
    def evaluate_screen_elements(cls, elements: List[Dict[str, Any]]) -> PrivacyCheckResult:
        """
        Evaluate a list of screen hierarchy elements (text, desc, id, class)
        against privacy and authorization guardrails.
        """
        combined_text_list = []
        has_password_field = False

        for el in elements:
            txt = el.get("text", "") or ""
            desc = el.get("desc", "") or ""
            res_id = el.get("id", "") or ""
            el_class = el.get("class", "").lower()

            if "password" in el_class or "password" in res_id.lower():
                has_password_field = True

            combined_text_list.append(f"{txt} {desc} {res_id}")

        all_text = " ".join(combined_text_list).lower()

        # 1. Check for OTP
        for pat in cls.OTP_PATTERNS:
            match = re.search(pat, all_text, re.IGNORECASE)
            if match:
                return PrivacyCheckResult(
                    is_sensitive=True,
                    guard_type="OTP",
                    message="We have reached an OTP verification screen. For your security and privacy, please enter the OTP on your phone screen.",
                    requires_user_action=True,
                    matched_term=match.group(0)
                )

        # 2. Check for Payment Credentials (Card / CVV / UPI PIN)
        for pat in cls.PAYMENT_CREDENTIALS_PATTERNS:
            match = re.search(pat, all_text, re.IGNORECASE)
            if match:
                return PrivacyCheckResult(
                    is_sensitive=True,
                    guard_type="PAYMENT_CREDENTIALS",
                    message="We have reached a secure payment screen (Card/CVV/UPI PIN). I cannot enter confidential financial details. Please enter your payment details on your phone.",
                    requires_user_action=True,
                    matched_term=match.group(0)
                )

        # 3. Check for Password
        if has_password_field:
            return PrivacyCheckResult(
                is_sensitive=True,
                guard_type="PASSWORD",
                message="A confidential password field is visible on screen. Please enter your password or authenticate with your fingerprint.",
                requires_user_action=True,
                matched_term="password_field"
            )

        for pat in cls.PASSWORD_PATTERNS:
            match = re.search(pat, all_text, re.IGNORECASE)
            if match:
                return PrivacyCheckResult(
                    is_sensitive=True,
                    guard_type="PASSWORD",
                    message="An account login password is required. Please sign in on your phone, then say 'continue'.",
                    requires_user_action=True,
                    matched_term=match.group(0)
                )

        # 4. Check for Phone Number / Mobile Login
        for pat in cls.PHONE_NUMBER_LOGIN_PATTERNS:
            match = re.search(pat, all_text, re.IGNORECASE)
            if match:
                return PrivacyCheckResult(
                    is_sensitive=True,
                    guard_type="PHONE_NUMBER_LOGIN",
                    message="Flipkart is asking for your mobile number to log in and complete your shopping. Please enter your mobile number on your phone screen, and inform me once done (say 'done' or 'continue') so I can move to the next step!",
                    requires_user_action=True,
                    matched_term=match.group(0)
                )

        # 5. Check for Order Confirmation / Place Order / Pay Now
        for pat in cls.ORDER_CONFIRMATION_PATTERNS:
            match = re.search(pat, all_text, re.IGNORECASE)
            if match:
                return PrivacyCheckResult(
                    is_sensitive=True,
                    guard_type="ORDER_CONFIRMATION",
                    message="The order is ready to be placed on screen. Before final booking, please confirm if you would like me to proceed or tap 'Place Order' on your device.",
                    requires_user_action=True,
                    matched_term=match.group(0)
                )

        return PrivacyCheckResult(
            is_sensitive=False,
            guard_type="NONE",
            message="",
            requires_user_action=False
        )

privacy_guard = PrivacyGuard()
