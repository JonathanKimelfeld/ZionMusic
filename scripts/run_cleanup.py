import os
from neo4j import GraphDatabase

# Connection details (from ingest_one.py)
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))

driver = GraphDatabase.driver(URI, auth=AUTH)

def run_cleanup():
    with open("scripts/cleanup.cypher", "r") as f:
        query = f.read()
    
    queries = query.split(";")
    with driver.session() as session:
        for q in queries:
            if q.strip():
                print(f"Executing: {q.strip()}")
                session.run(q)
    print("Cleanup complete.")

if __name__ == "__main__":
    run_cleanup()
    driver.close()
