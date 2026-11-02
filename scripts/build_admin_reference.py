"""Build the Vietnam administrative-unit reference CSVs (GSO / TCTK codes) used by Gold.

Source: the `vietnamadminunits` package (MIT, data from the General Statistics Office), version 1.0.4.
    pip download vietnamadminunits==1.0.4 --no-deps && unzip the wheel, then
    python scripts/build_admin_reference.py <path to vietnamadminunits/data>

Writes
  src/reference/vn_new_ward_to_old_district.csv  one row per new (after 01/07/2025) ward:
      province / ward names and GSO codes, ward centroid, the old (before 2025) districts it came from
      (district_old = the one most of its old wards came from; district_old_code only when there is exactly one)
  src/reference/vn_old_districts.csv             one row per old district (GSO code, name, short name, the new
      province it is in now (e.g. Bình Dương's old districts -> 79 Thành phố Hồ Chí Minh),
      unaccented keys and aliases such as 'q1', 'quan 2' -> TP Thu Duc)
"""
import csv, json, os, sqlite3, sys
from collections import Counter, defaultdict

data_dir = sys.argv[1]
out_dir = os.path.join(os.path.dirname(__file__), "..", "src", "reference")
db = sqlite3.connect(os.path.join(data_dir, "dataset.db"))
conv = json.load(open(os.path.join(data_dir, "converter_2025.json"), encoding="utf-8"))

legacy = {}   # (provinceKey, districtKey) -> (provinceCode, districtCode, district, districtShort)
for pk, dk, pc, dc, dn, ds in db.execute(
        "SELECT DISTINCT provinceKey, districtKey, provinceCode, districtCode, district, districtShort FROM admin_units_legacy"):
    legacy[(pk, dk)] = (pc, dc, dn, ds)

# new ward -> old districts (one entry per old ward that went into it)
sources = defaultdict(list)
for new_prov, wards in conv["DICT_PROVINCE_WARD_NO_DIVIDED"].items():
    for new_ward, olds in wards.items():
        for old in olds:
            p, d, _ = old.split("_")
            sources[(new_prov, new_ward)].append((p, d))
for new_prov, olds in conv["DICT_PROVINCE_WARD_DIVIDED"].items():
    for old, targets in olds.items():
        p, d, _ = old.split("_")
        for t in targets:
            sources[(new_prov, t["newWardKey"])].append((p, d))

rows = []
for (prov, prov_key, prov_code, ward, ward_short, ward_key, ward_short_key, ward_code, lat, lon) in db.execute(
        "SELECT province, provinceKey, provinceCode, ward, wardShort, wardKey, wardShortKey, wardCode, wardLat, wardLon "
        "FROM admin_units ORDER BY provinceCode, wardCode"):
    olds = [legacy[k] for k in sources.get((prov_key, ward_key), []) if k in legacy]
    counts = Counter(olds)
    ordered = [o for o, _ in counts.most_common()]
    main = ordered[0] if ordered else None
    rows.append({
        "province_key": prov_key, "province": prov, "province_code": prov_code,
        "ward_short_key": ward_short_key, "ward_key": ward_key, "ward": ward, "ward_code": ward_code,
        "ward_lat": lat, "ward_lon": lon,
        "district_old": main[2] if main else "",
        "district_old_code": main[1] if main and len(ordered) == 1 else "",
        "districts_old": "|".join(o[2] for o in ordered),
        "districts_old_codes": "|".join(o[1] for o in ordered),
    })
with open(os.path.join(out_dir, "vn_new_ward_to_old_district.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)

# old districts; aliases for HCMC districts merged into TP Thu Duc in 2021 (still said in videos)
EXTRA_ALIASES = {"769": ["quan 2", "q2", "quan 9", "q9", "thu duc"]}
def unaccent(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
new_of_old = {old: new for new, olds in conv["DICT_PROVINCE"].items() for old in olds}
new_prov = {k: (c, n) for k, c, n in db.execute("SELECT DISTINCT provinceKey, provinceCode, province FROM admin_units")}
drows = []
for (pk, dk), (pc, dc, dn, ds) in sorted(legacy.items(), key=lambda x: x[1][1]):
    short = unaccent(ds)
    aliases = {short, unaccent(dn)}
    num = ds.removeprefix("Quận ").strip()
    if num.isdigit():   # numbered districts: 'Quận 7', 'quan 7', 'q7', 'q 7'
        aliases |= {f"quan {num}", f"q{num}", f"q {num}"}
    aliases |= set(EXTRA_ALIASES.get(dc, []))
    npc, npn = new_prov[new_of_old[pk]]
    drows.append({"new_province_code": npc, "new_province": npn, "province_code": pc, "province_key": pk, "district_code": dc, "district": dn, "district_short": ds,
                  "district_key": dk, "aliases": "|".join(sorted(aliases))})
with open(os.path.join(out_dir, "vn_old_districts.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(drows[0]))
    w.writeheader(); w.writerows(drows)
print(f"{len(rows)} new wards, {len(drows)} old districts")
