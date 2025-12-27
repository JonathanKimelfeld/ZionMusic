import os, json
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))

def verify():
    with driver.session() as s:
        # Find edge match by MBID
        query = """
        MATCH (a {mbid: '1a549459-3106-4662-be8e-5035230a7d86'})-[r:COLLABORATED_WITH]-(b {mbid: 'd3c90d1b-f529-404b-939d-41873386d4b4'})
        RETURN r, a.name as name1, b.name as name2
        """
        res = s.run(query).single()

        if not res:
            print("❌ No COLLABORATED_WITH edge found between Peter Roth and Arik Einstein (by MBID).")
            return
        
        print(f"✅ Edge found between {res['name1']} and {res['name2']}.")
        rel = res["r"]
        props = dict(rel)
        sr = props.get("sharedReleases")
        
        if not sr:
            print("❌ No sharedReleases property on edge.")
            return
            
        releases = json.loads(sr)
        print(f"Found {len(releases)} shared releases:")
        for r in releases:
            print(f" - {r.get('title')} ({r.get('date')})")
            
        if any("Regaim" in r.get("title", "") or "רגעים" in r.get("title", "") for r in releases):
             print("✅ 'Regaim' found in shared releases.")
        else:
             print("⚠️ 'Regaim' NOT found. Check filtering.")
             
        if releases[0].get('url'):
            print("✅ URL present in shared releases.")
        else:
            print("❌ URL missing in shared releases.")

if __name__ == "__main__":
    verify()
