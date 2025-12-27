import os, json
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))

def verify():
    with driver.session() as s:
        query = """
        MATCH (a {mbid: '1a549459-3106-4662-be8e-5035230a7d86'})-[r:COLLABORATED_WITH]-(b)
        RETURN b.name, b.mbid, r.sharedReleases
        """
        res = s.run(query)
        
        print("Collaborators for Peter Roth:")
        for record in res:
            name = record["b.name"]
            mbid = record["b.mbid"]
            sr = record["r.sharedReleases"]
            print(f" - {name} ({mbid}): shared={sr[:50] if sr else 'None'}...")

if __name__ == "__main__":
    verify()
