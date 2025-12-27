import os, json
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))

def verify():
    with driver.session() as s:
        res = s.run("MATCH (n:Person|Group {name: 'Bubble Wrap Trap'}) RETURN n").single()
        if not res:
            print("❌ Bubble Wrap Trap node not found")
            return
            
        node = res["n"]
        props = dict(node)
        
        releases_json = props.get("releases")
        if not releases_json:
            print("❌ No releases found for Bubble Wrap Trap")
            return
            
        releases = json.loads(releases_json)
        print(f"Found {len(releases)} releases.")
        for r in releases:
            print(f" - {r.get('title')} ({r.get('date')}) [URL: {r.get('url')}]")
            
        if len(releases) > 0 and releases[0].get('url'):
            print("✅ Releases found and have URLs.")
        else:
            print("❌ Releases missing or logic failed.")

if __name__ == "__main__":
    verify()
