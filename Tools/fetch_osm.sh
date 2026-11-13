#!/bin/bash
# Re-runnable: OSM extras (trees, POIs, street furniture) for La Montagne bbox via Overpass (© OpenStreetMap contributors, ODbL)
BB="47.166,-1.708,47.205,-1.657"
for EP in https://overpass.kumi.systems/api/interpreter https://overpass-api.de/api/interpreter; do
curl -sS -m 200 -w "HTTP %{http_code}\n" -A "GrokBot-LaMontagneFPS/1.0" --data-urlencode "data=[out:json][timeout:180];(node[natural=tree]($BB);node[amenity]($BB);node[shop]($BB);node[highway=street_lamp]($BB);way[building][building!=yes]($BB);way[highway][name]($BB);node[historic]($BB);node[tourism]($BB););out geom;" "$EP" -o osm.json && [ -s osm.json ] && break
done
