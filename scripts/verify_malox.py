import os, json
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))

def verify():
    with driver.session() as s:
        res = s.run("MATCH (n:Person|Group {name: 'Malox'}) RETURN n").single()
        if not res:
            print("❌ Malox node not found")
            return
            
        node = res["n"]
        props = dict(node)
        
        print(f"Name: {props.get('name')}")
        print(f"Discogs URL: {props.get('discogsUrl')}")
        print(f"External Links: {props.get('externalLinks')}")
        
        links = []
        if props.get('externalLinks'):
            links = json.loads(props['externalLinks'])
            print(f"Parsed External Links ({len(links)}):")
            for l in links:
                print(f" - {l['label']}: {l['url']}")
                
        if props.get('discogsUrl') or any(l['label'] == 'Discogs' for l in links):
            print("✅ Discogs link found.")
        else:
            print("❌ Discogs link MISSING.")

if __name__ == "__main__":
    verify()
