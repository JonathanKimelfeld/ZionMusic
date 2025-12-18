from subprocess import run

DECADES = [
    (1950, 1959, "1950s"),
    (1960, 1969, "1960s"),
    (1970, 1979, "1970s"),
    (1980, 1989, "1980s"),
    (1990, 1999, "1990s"),
    (2000, 2009, "2000s"),
    (2010, 2019, "2010s"),
    (2020, 2029, "2020s"),
]

for y1, y2, label in DECADES:
    run(["python", "scripts/recompute_comember.py", str(y1), str(y2), label])
    print("✔", label)
