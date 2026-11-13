# La Montagne FPS — data credits

Reconstruction 1:1 of La Montagne (44620, INSEE 44101, Loire-Atlantique). 1 Unity unit = 1 m,
local frame = Lambert-93 (EPSG:2154) minus origin E 343552 / N 6684852 (4096 m × 4096 m square).

## IGN — Géoplateforme (data.geopf.fr), Licence Ouverte / Open Licence Etalab 2.0
Source: IGN — https://geoservices.ign.fr — accessed 2026-11-12
- RGE ALTI® 1 m (WMS layer ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES) — terrain, resampled to 2 m
- LiDAR HD MNH (canopy height model) 1 m — tree positions and heights
- LiDAR HD MNS (surface model) 0.5 m — church spire heights
- BD ORTHO® / HR.ORTHOIMAGERY.ORTHOPHOTOS 0.5 m — terrain texture and roof colours
- BD TOPO® V3 (WFS) — batiment (footprints, height, floors, wall/roof materials), troncon_de_route,
  troncon_de_voie_ferree, surface_hydrographique (Loire), zone_de_vegetation, commune,
  construction_ponctuelle (clochers), zone_d_activite_ou_d_interet (place names), haie (hedges, v2)

## OpenStreetMap
© OpenStreetMap contributors, ODbL 1.0 — https://www.openstreetmap.org/copyright
- Commune boundary relation (INSEE 44101), used to frame the area (via Overpass API).
- v2 (Overpass mirror maps.mail.ru, 2026-11-13): shops/amenities with names (shopfront signs), crossings (zebras),
  barrier=wall/fence/hedge/retaining_wall/guard_rail, bollards, benches, waste baskets, post boxes, bus stops/shelters,
  stop/give-way signs, traffic_sign=city_limit (town entry/exit signs), amenity=parking_space (parked cars).

## Panoramax street-level photos (reference only, not embedded in the game)
Used to choose facade styles, shutter/surround colours, church shape and street furniture; reference crops in v2/refs.
- Photos by **Nzau** — CC-BY-SA 4.0 (sequence of 2026-04-19, La Montagne bourg)
- Photos by **mamute** — Licence Ouverte Etalab 2.0
- https://panoramax.fr — API api.panoramax.xyz. No Google Street View imagery was used.

## Procedural / assumed (not from data)
- Street lamps (OSM has none tagged here): one every ~30 m on urban streets, alternating sides.
- Facade style per building is drawn from palettes observed on the photos (bourg vs pavillons), except two
  photo-matched overrides (REF_STYLES in Tools/build_city.py); car colours and parking-space fill (~68 %) are random.
- Sun position computed for 47.19 N / 1.68 W, 19 April 15:00 CEST.

## Tools
- bpypolyskel (straight-skeleton hipped roofs), GPL-3.0 — used offline in Tools/build_city.py only;
  it is not shipped in the game build. https://github.com/prochitecture/bpypolyskel
- Procedural textures, tree models, weapon and sound: generated (Tools/gen_textures.py, Tools/gen_textures_v2.py, editor scripts).
