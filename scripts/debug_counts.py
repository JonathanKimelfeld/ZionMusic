import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))

with driver.session() as s:
    print("Checking MEMBER_OF count...")
    c = s.run("MATCH ()-[r:MEMBER_OF]->() RETURN count(r) as n").single()["n"]
    print(f"MEMBER_OF count: {c}")

    print("Checking CO_MEMBER count...")
    c = s.run("MATCH ()-[r:CO_MEMBER]->() RETURN count(r) as n").single()["n"]
    print(f"CO_MEMBER count: {c}")
    
    print("Sample CO_MEMBER:")
    res = s.run("MATCH (a)-[r:CO_MEMBER]->(b) RETURN a.name, b.name, r.weight LIMIT 5").data()
    for r in res:
        print(r)

