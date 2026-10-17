import re
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ProductItem:
    title: str
    price: float
    raw_price: str
    rating: Optional[str]
    pack_count: int
    unit_price: float
    center: List[int]
    bounds: List[int]

class ECommerceComparator:
    """
    Intelligent E-Commerce Product Parser & Comparator for Autonomous Mobile Agents.
    Extracts product listings from mobile screen hierarchies (Flipkart, Amazon, Web)
    and identifies the lowest price or best value item matching user criteria.
    """

    PRICE_PATTERN = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)", re.IGNORECASE)
    PACK_PATTERN = re.compile(r"(?:pack\s*(?:of|:)?|set\s*(?:of|:)?|qty\s*:\s*|count\s*:\s*)\s*(\d+)", re.IGNORECASE)

    @classmethod
    def extract_price_value(cls, text: str, el_id: str = "") -> Optional[float]:
        """Extract clean numeric float price from string like '₹49' or 'Rs. 1,299'"""
        if not text:
            return None
        # Check explicit currency indicator
        m = cls.PRICE_PATTERN.search(text)
        if m:
            val_str = m.group(1).replace(",", "")
            try:
                val = float(val_str)
                if val > 0:
                    return val
            except ValueError:
                pass

        # If element id or class explicitly denotes price (e.g., 'price_1')
        if "price" in el_id.lower():
            m2 = re.search(r"([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)", text)
            if m2:
                try:
                    return float(m2.group(1).replace(",", ""))
                except ValueError:
                    pass

        return None

    @classmethod
    def parse_products_from_screen(cls, elements: List[Dict[str, Any]], query: str = "") -> List[ProductItem]:
        """
        Parses visible on-screen elements to identify e-commerce product cards,
        matching titles, prices, ratings, and click targets.
        """
        products: List[ProductItem] = []
        if not elements:
            return products

        query_terms = [t.lower() for t in query.split() if len(t) > 2]

        for i, el in enumerate(elements):
            text = (el.get("text", "") or "").strip()
            desc = (el.get("desc", "") or "").strip()
            full_label = f"{text} {desc}".strip()
            if not full_label:
                continue

            # Check if this element or nearby elements have price
            price_val = cls.extract_price_value(full_label, el.get("id", ""))

            # Case A: Combined element contains both title and price (common in Flipkart web / accessibility)
            if price_val and any(term in full_label.lower() for term in query_terms) and len(full_label) > 10:
                pack_match = cls.PACK_PATTERN.search(full_label)
                pack_cnt = int(pack_match.group(1)) if pack_match else 1
                unit_price = price_val / max(pack_cnt, 1)

                products.append(ProductItem(
                    title=full_label.split("₹")[0].split("Rs")[0].strip() or full_label[:50],
                    price=price_val,
                    raw_price=f"₹{price_val:.0f}",
                    rating=None,
                    pack_count=pack_cnt,
                    unit_price=unit_price,
                    center=el.get("center", [540, 1000]),
                    bounds=el.get("bounds", [0, 0, 1080, 2400])
                ))
                continue

            # Case B: Element is a product title, and subsequent element contains price
            is_title_candidate = len(full_label) >= 8 and (
                any(term in full_label.lower() for term in query_terms) or
                not query_terms
            )

            if is_title_candidate:
                # Look ahead up to 8 elements for price (handling MRP vs discounted selling price)
                prices_found = []
                for j in range(i + 1, min(i + 8, len(elements))):
                    next_label = f"{elements[j].get('text', '')} {elements[j].get('desc', '')}".strip()
                    pv = cls.extract_price_value(next_label, elements[j].get("id", ""))
                    if pv:
                        prices_found.append((pv, next_label))

                if prices_found:
                    # Pick the discounted selling price (minimum price in card)
                    found_price, found_price_raw = min(prices_found, key=lambda x: x[0])

                    pack_match = cls.PACK_PATTERN.search(full_label)
                    pack_cnt = int(pack_match.group(1)) if pack_match else 1
                    unit_price = found_price / max(pack_cnt, 1)

                    products.append(ProductItem(
                        title=full_label,
                        price=found_price,
                        raw_price=f"₹{found_price:.0f}",
                        rating=None,
                        pack_count=pack_cnt,
                        unit_price=unit_price,
                        center=el.get("center", [540, 1000]),
                        bounds=el.get("bounds", [0, 0, 1080, 2400])
                    ))

        # Deduplicate products by 2D coordinates (supports both 1-column list and 2-column grid)
        unique_products: List[ProductItem] = []
        for p in products:
            # Check if this item is at the same screen position (within 50px in both X and Y)
            is_dup = any(
                abs(p.center[0] - u.center[0]) < 60 and abs(p.center[1] - u.center[1]) < 60
                for u in unique_products
            )
            # Or if title is identical
            is_same_title = any(p.title.strip() == u.title.strip() for u in unique_products)
            if not is_dup and not is_same_title:
                unique_products.append(p)

        return unique_products

    @classmethod
    def find_best_product(
        cls,
        products: List[ProductItem],
        criteria: str = "lowest_price"
    ) -> Optional[ProductItem]:
        """
        Ranks extracted products based on user criteria:
        - 'lowest_price' / 'cheapest': picks the minimum price
        - 'best_value': picks the lowest unit price (considering pack counts)
        """
        if not products:
            return None

        if "value" in criteria.lower() or "unit" in criteria.lower():
            # Sort by unit price
            sorted_items = sorted(products, key=lambda x: x.unit_price)
        else:
            # Default: lowest absolute price
            sorted_items = sorted(products, key=lambda x: x.price)

        return sorted_items[0]

ecommerce_comparator = ECommerceComparator()
