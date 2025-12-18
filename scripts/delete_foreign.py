import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

sys.path.append(os.getcwd())
load_dotenv()

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def delete_foreign():
    print("🧹 Starting Foreign Node Cleanup...")
    
    with driver.session() as s:
        # 1. Delete nodes explicitly marked as non-Israeli
        # (Where country is known and is NOT IL/Israel)
        # Note: 'country' might be "IL" or "Israel". 
        # 'area' might be "Tel Aviv", "Israel", etc.
        
        # Query: Find nodes with country/area that clearly exclude Israel
        # Be careful not to delete nodes with NULL country (we don't know yet).
        
        cypher = """
        MATCH (n)
        WHERE (n:Person OR n:Group)
          AND n.country IS NOT NULL
          AND n.country <> 'IL'
          AND n.country <> 'Israel'
          AND NOT (n.area CONTAINS 'Israel' OR n.area CONTAINS 'Tel Aviv' OR n.area CONTAINS 'Jerusalem' OR n.area CONTAINS 'Haifa')
        RETURN elementId(n) as id, n.name as name, n.country as country, n.area as area
        """
        
        candidates = s.run(cypher).data()
        
        print(f"   Found {len(candidates)} nodes explicitly marked as Foreign.")
        
        count = 0
        for c in candidates:
            # specialized check?
            # e.g. if country is "US", "GB", "DE" -> Delete
            print(f"   🗑️ Deleting {c['name']} ({c['country']}/{c['area']})")
            s.run("MATCH (n) WHERE elementId(n)=$id DETACH DELETE n", id=c['id'])
            count += 1
            
        print(f"✅ Deleted {count} foreign nodes.")

if __name__ == "__main__":
    delete_foreign()

