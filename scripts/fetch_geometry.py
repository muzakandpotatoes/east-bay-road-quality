"""Download the street geometry the join needs from the cities' ArcGIS services."""
import json, os, sys
import arcgis

LAYERS = {
    # Oakland publishes its StreetSaver pavement sections directly, keyed by
    # STREETID/SECTIONID -- the same keys the P-TAP report prints.
    "oakland_pci_sections.geojson": (
        "https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services"
        "/Paving_PCI_2024Values/FeatureServer/0", "*"),
    # Berkeley publishes no section geometry, only the centerline network.
    "berkeley_centerlines.geojson": (
        "https://gis.cityofberkeley.info/arcgis/rest/services/Planning/Accela/MapServer/3",
        "OBJECTID,CENTERLINEID,FULLNAME,STREET_NAME,STREET_TYPE,"
        "MUNILEFT,MUNIRIGHT,ROADCLASS,ONEWAYDIR"),
}


def main(raw_dir):
    for name, (url, fields) in LAYERS.items():
        path = os.path.join(raw_dir, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"  {name}: already present")
            continue
        print(f"  {name}:")
        json.dump(arcgis.fetch_geojson(url, out_fields=fields), open(path, "w"))


if __name__ == "__main__":
    main(sys.argv[1])
