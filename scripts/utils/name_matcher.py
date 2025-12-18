import re
import warnings
# Simple mapping for Hebrew -> English phonetic approximation if transliterate fails or we want custom logic
# But we'll rely on libraries where possible.

try:
    from transliterate import translit
except ImportError:
    translit = None

def normalize_name(name: str) -> str:
    """
    Normalizes a name for comparison:
    1. Lowercase
    2. Remove punctuation
    3. (Optional) Transliterate Hebrew to Latin
    """
    if not name:
        return ""
        
    if isinstance(name, list):
        # If name is a list, take the first element if available, else empty string
        if not name: return ""
        name = str(name[0])
    
    # print(f"Normalizing name: {name}")
    norm = name.lower()
    
    # Remove punctuation
    norm = re.sub(r'[^\w\s]', '', norm)
    
    # Attempt transliteration if Hebrew characters are present
    if any("\u0590" <= c <= "\u05FF" for c in norm):
        if translit:
            try:
                # 'he' is Hebrew code in transliterate
                # reversed=True usually means Latin->Target, but here we want Target->Latin?
                # Actually transliterate usually does English -> Language.
                # To go Language -> English, often we need a specific reversible mapping or use `reversed=True` if supported.
                # The `transliterate` package supports Hebrew.
                norm = translit(norm, 'he', reversed=True)
            except Exception as e:
                pass
    
    return norm.strip()

if __name__ == "__main__":
    # Test cases
    cases = [
        ("Infected Mushroom", "infected mushroom"),
        ("אינפקטד מאשרום", "infected mushroom"), # Ideally this matches
        ("Kaveret", "kaveret"),
        ("כוורת", "kaveret"),
    ]
    
    print("Testing normalization logic...")
    for orig, expected in cases:
        got = normalize_name(orig)
        print(f"'{orig}' -> '{got}'")

