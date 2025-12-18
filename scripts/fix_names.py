import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Ensure we can import from parent directory if needed
sys.path.append(os.getcwd())

# Load environment variables
load_dotenv()

# Neo4j connection details
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password") # Fallback to default, but env should provide it

driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

def fix_list_names():
    print("🔧 Starting Name Fixer...")
    
    with driver.session() as session:
        # Find nodes where name is likely a list
        # We can't easily check type in WHERE in all versions, 
        # but we can try to fetch ALL nodes and filter in Python to be safe.
        
        print("   Fetching all Person nodes to check for list-names...")
        
        # We fetch ID and Name. If name is a list, the driver will return it as a list.
        result = session.run("MATCH (n:Person) RETURN elementId(n) as id, n.name as name")
        
        count = 0
        fixed = 0
        
        for record in result:
            node_id = record["id"]
            name_val = record["name"]
            
            count += 1
            if count % 1000 == 0:
                print(f"   Checked {count} nodes...")
            
            if isinstance(name_val, list):
                # Found one!
                # Prefer the first non-empty string, or just the first element
                new_name = str(name_val[0]) if name_val else "Unknown"
                
                print(f"   ⚠️ Found list name for ID {node_id}: {name_val} -> Fixing to '{new_name}'")
                
                # Update in DB
                session.run("""
                    MATCH (n) 
                    WHERE elementId(n) = $id 
                    SET n.name = $new_name
                """, id=node_id, new_name=new_name)
                
                fixed += 1
        
        print(f"✅ Finished. scanned {count} nodes, fixed {fixed} list-names.")

if __name__ == "__main__":
    fix_list_names()

