import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))

with driver.session() as s:
    print("Inspecting element_ids...")
    # Get one relationship and its nodes
    cypher = "MATCH (a)-[r:CO_MEMBER]->(b) RETURN a, r, b LIMIT 1"
    res = s.run(cypher).single()
    a = res['a']
    r = res['r']
    b = res['b']
    
    print(f"Node A element_id: {a.element_id}")
    print(f"Node B element_id: {b.element_id}")
    print(f"Rel Start element_id: {r.start_node.element_id}")
    print(f"Rel End element_id:   {r.end_node.element_id}")
    
    match_start = (a.element_id == r.start_node.element_id)
    match_end = (b.element_id == r.end_node.element_id)
    print(f"Match Start? {match_start}")
    print(f"Match End?   {match_end}")

