import re

SUPPORTED_APPS = ['flipkart', 'amazon', 'myntra', 'blinkit', 'zepto', 'swiggy', 'zomato']

def extract_product_query(text: str) -> str:
    m = text.lower().strip()
    for app in SUPPORTED_APPS:
        m = re.sub(rf'\b{app}\b', ' ', m)
    noise_words = [
        r'\b(?:go\s+to|open|jao|jaao|kholo)\b',
        r'\b(?:and|aur|then|fir|bhi)\b',
        r'\b(?:se|pe|par|me|mein|on|in|from|using|to|for|with|at|having)\b',
        r'\b(?:bhai|yaar|please|zara|ek|kuch|na|do|kar\s+do|kar\s+do\s+na)\b',
        r'\b(?:order|buy|purchase|kharido|kharid|manga|mangwao|dhoondh|dhoondo|dhundh|dhundo|book|le\s+lo|lena\s+hai|lana|lao|get|find|search)\b',
        r'\b(?:lowest|price|cheapest|cheap|sasta|sasti|sabse\s+sasta|best|value|low|rate|wala|wali|rate\s+ka)\b',
        r'\b(?:a|an|the)\b',
        r'\b(?:karo|karna\s+hai|kariye)\b'
    ]
    cleaned = m
    for pat in noise_words:
        cleaned = re.sub(pat, ' ', cleaned, flags=re.IGNORECASE)
    tokens = [w for w in cleaned.split() if len(w) >= 2]
    return ' '.join(tokens) if tokens else 'pen'

tests = [
    'bhai flipkart se sabse sasta pen dhundh ke book kar do na yaar',
    'yaar flipkart pe jaao ek sasta pen dhoondh ke order karo',
    'flipkart pe jao aur lowest price wala pen order karo',
    'go to flipkart and order a pen with lowest price',
    'flipkart se ek sasta pen manga do',
    'order ball pen from amazon with best value',
    'open blinkit and order milk'
]

print("=== TESTING PRODUCT QUERY EXTRACTION ===")
for t in tests:
    print(f'Input: "{t}" -> Query: "{extract_product_query(t)}"')
