import requests
import sys

HEADERS = {"User-Agent": "ZionMusicVal/1.0 ( test@example.com )"}

def get_id(name):
    url = "https://musicbrainz.org/ws/2/artist"
    params = {"query": f"name:{name}", "fmt": "json", "limit": 1}
    try:
        resp = requests.get(url, params=params, headers=HEADERS)
        data = resp.json()
        if data.get("artists"):
            artist = data["artists"][0]
            print(f"{name}: {artist['id']} ({artist.get('name')}, {artist.get('country')})")
            return artist['id']
    except Exception as e:
        print(f"Error fetching {name}: {e}")
    return None

if __name__ == "__main__":
    names = ["Malox", "Bubble Wrap Trap", "Peter Roth", "Arik Einstein"]
    ids = []
    for n in names:
        ids.append(get_id(n))
