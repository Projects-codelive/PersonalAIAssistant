from app.agent.tools.phone_tools import make_phone_call, search_contacts, save_contact
from app.agent.tools.screen_tools import read_screen_state, tap_element, open_app, go_home
from app.agent.tools.messaging_tools import send_sms, send_whatsapp_message, send_email
from app.agent.tools.ecommerce_tools import (
    check_and_open_app,
    search_and_compare_products,
    scroll_screen,
    press_system_key,
    verify_privacy_and_order_safety,
)
from app.agent.tools.search_tools import (
    search_wikipedia,
    search_duckduckgo,
    search_arxiv,
    search_pubmed,
    SEARCH_TOOLS,
    search_knowledge_service,
)

ALL_TOOLS = [
    make_phone_call,
    send_sms,
    send_whatsapp_message,
    send_email,
    search_contacts,
    save_contact,
    read_screen_state,
    tap_element,
    open_app,
    go_home,
    check_and_open_app,
    search_and_compare_products,
    scroll_screen,
    press_system_key,
    verify_privacy_and_order_safety,
    *SEARCH_TOOLS,
]

__all__ = [
    "make_phone_call",
    "send_sms",
    "send_whatsapp_message",
    "send_email",
    "search_contacts",
    "save_contact",
    "read_screen_state",
    "tap_element",
    "open_app",
    "go_home",
    "check_and_open_app",
    "search_and_compare_products",
    "scroll_screen",
    "press_system_key",
    "verify_privacy_and_order_safety",
    "search_wikipedia",
    "search_duckduckgo",
    "search_arxiv",
    "search_pubmed",
    "SEARCH_TOOLS",
    "search_knowledge_service",
    "ALL_TOOLS",
]

