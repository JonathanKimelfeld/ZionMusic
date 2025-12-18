import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.environ["NEO4J_URI"]
USER = os.environ["NEO4J_USER"]
PWD = os.environ["NEO4J_PASSWORD"]

CONSTRAINTS = [
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.personId IS UNIQUE",
    "CREATE CONSTRAINT group_id IF NOT EXISTS FOR (g:Group) REQUIRE g.groupId IS UNIQUE",
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (e:Event) REQUIRE e.eventId IS UNIQUE",
    "CREATE CONSTRAINT venue_id IF NOT EXISTS FOR (v:Venue) REQUIRE v.venueId IS UNIQUE",

    "CREATE INDEX person_mbid IF NOT EXISTS FOR (p:Person) ON (p.mbid)",
    "CREATE INDEX person_wikidata IF NOT EXISTS FOR (p:Person) ON (p.wikidataQid)",
    "CREATE INDEX group_mbid IF NOT EXISTS FOR (g:Group) ON (g.mbid)",
    "CREATE INDEX group_wikidata IF NOT EXISTS FOR (g:Group) ON (g.wikidataQid)",
]

def main():
    driver = GraphDatabase.driver(URI, auth=(USER, PWD))
    with driver.session() as s:
        for stmt in CONSTRAINTS:
            s.run(stmt)
    driver.close()
    print("✅ Neo4j constraints/indexes created.")

if __name__ == "__main__":
    main()
