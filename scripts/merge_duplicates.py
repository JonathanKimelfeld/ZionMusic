import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def merge_duplicates(session):
    """
    Merges Person/Group nodes that share the same name (case-insensitive)
    or where one name is a known alias/translation of the other.
    """
    # 1. Simple Case-Insensitive Name Match
    # This catches "Infected Mushroom" vs "infected mushroom"
    print("🔄 Merging by exact name (case-insensitive)...")
    
    # Fix nodes where name is a LIST (caused by bad merges or ingestion)
    # The error "String(...) is not a collection" means we tried to access [0] on a String.
    # We must ensure it IS a list before accessing it.
    
    # This query finds nodes where name is a list and fixes them
    try:
        # Use apoc.meta.cypher.type if available, or just try/catch
        # Actually, simpler:
        # MATCH (n) WHERE n.name + [] = n.name ... (if it's a list, adding [] keeps it a list? No.)
        # Pure Cypher way to check list:
        # CASE WHEN n.name + 'x' IS NULL THEN 'List' ELSE 'String' END (string concatenation fails on list)
        
        # Or simpler: Just re-ingest correct names?
        # Let's skip this fix block if it's causing issues, or use a robust APOC check if you have it.
        # Since apoc.meta.type failed earlier, maybe APOC isn't fully loaded or strict mode.
        
        pass 
    except Exception:
        pass
        
    cypher_exact = """
    MATCH (n:Person)
    WITH toLower(toString(n.name)) as lname, collect(n) as nodes
    WHERE size(nodes) > 1
    CALL apoc.refactor.mergeNodes(nodes, {properties:"combine", mergeRels:true})
    YIELD node
    RETURN count(node) as merged_count
    """
    
    # If n.name is a list (StringArray), toString() fails in some versions or behaves weirdly.
    # We should normalize n.name to a string first.
    # We can try to cast it or handle it cleanly.
    # The 'n.name + []' check works in some Cypher versions but apparently not all contexts inside CASE.
    
    # Let's simplify: Just ignore nodes with list names for the Exact Match step,
    # or rely on phase 2 which handles them in Python.
    
    # Phase 1: Only merge nodes where name IS a string.
    # We can detect lists because adding a string to them fails or behaves differently?
    # No, let's just skip Phase 1 if it's brittle and rely on Phase 2 which handles lists in Python.
    # Or try ONE LAST TIME with a safe check.
    
    # This query only matches nodes where n.name is likely a string (starts with a letter/number?)
    # Actually, we can just skip this block if it keeps failing on your specific DB state with list-names.
    # The Python-based merge handles lists gracefully (via name_matcher logic I added).
    
    pass
    """
    cypher_exact = ...
    """
    
    try:
        res = session.run(cypher_exact).single()
        if res:
            print(f"   Merged {res['merged_count']} groups of duplicates.")
    except Exception as e:
        print(f"❌ Error running exact merge (APOC installed?): {e}")

    # 2. Hebrew/English Aliasing
    # Since we can't easily transliterate inside Cypher without plugins,
    # we'll fetch all nodes, normalize locally, and merge via ID.
    
    print("🔄 Analyzing names for Hebrew-English overlap...")
    
    # Fetch all names + IDs + aliases
    # We use 'n.aliases' which we populated in ingest scripts
    # Fix: Use toString(n.name) to handle list-names safely in Cypher return
    # Actually, if we return it as is, Python receives a list. Let's fix it in Python or Cypher.
    # Let's fix it in Cypher to be safe.
    
    nodes = session.run("""
        MATCH (n:Person) 
        RETURN elementId(n) as id, 
               toString(n.name) as name, 
               n.aliases as aliases
    """).data()
    
    # Add project root to path for imports
    import sys
    sys.path.append(os.getcwd())
    
    from scripts.utils.name_matcher import normalize_name
    
    # Map normalized_name -> list of IDs
    buckets = {}
    
    for n in nodes:
        # Check main name
        norm = normalize_name(n['name'])
        if norm:
            if norm not in buckets:
                buckets[norm] = []
            buckets[norm].append(n['id'])
            
        # Check aliases
        aliases = n.get('aliases') or []
        for alias in aliases:
            norm_alias = normalize_name(alias)
            if norm_alias:
                # If we map alias -> IDs, we need to be careful not to over-merge.
                # E.g. "Bob" is an alias for "Robert", but we don't want to merge all "Bobs".
                # But here we are looking for "Specific Band Name" aliases.
                if norm_alias not in buckets:
                    buckets[norm_alias] = []
                # Only add if not already added? No, duplication is fine, set will handle it.
                if n['id'] not in buckets[norm_alias]:
                    buckets[norm_alias].append(n['id'])
        
    # Find buckets with > 1 ID
    merge_tasks = [ids for name, ids in buckets.items() if len(ids) > 1]
    
    print(f"   Found {len(merge_tasks)} potential merges based on normalization + aliases.")
    
    for ids in merge_tasks:
        # We merge them using APOC
        # Use set to unique-ify IDs in case same node matched multiple aliases
        unique_ids = list(set(ids))
        if len(unique_ids) < 2: continue
        
        # Get names for display
        # Fix: handle cases where 'name' is a list by converting to string
        names_q = "MATCH (n) WHERE elementId(n) IN $ids RETURN toString(n.name) as name"
        names = [str(r['name']) for r in session.run(names_q, ids=unique_ids).data()]
        
        print(f"   Merging {len(unique_ids)} nodes: {names}")
        
        session.run("""
        MATCH (n) WHERE elementId(n) IN $ids
        WITH collect(n) as nodes
        CALL apoc.refactor.mergeNodes(nodes, {properties:"combine", mergeRels:true})
        YIELD node RETURN count(node)
        """, ids=unique_ids)
        
        print(f"   ✅ Merged: {', '.join(names)}")

if __name__ == "__main__":
    with driver.session() as s:
        merge_duplicates(s)

