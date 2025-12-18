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

def create_index():
    print("🏗️ Creating Fulltext Search Index 'nameIndex'...")
    with driver.session() as s:
        # Create index on Name and Aliases for both Person and Group
        s.run("""
        CREATE FULLTEXT INDEX nameIndex IF NOT EXISTS 
        FOR (n:Person|Group) 
        ON EACH [n.name, n.aliases]
        """)
    print("✅ Fulltext index created/verified.")

if __name__ == "__main__":
    create_index()

