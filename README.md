# La Montagne FPS

Un FPS en Unity (URP) dont le décor reproduit **La Montagne (44620, Loire-Atlantique) à l'échelle 1:1**, construit à partir de données ouvertes : relief, bâtiments, routes, arbres, rues et mobilier urbain.

▶ **Jouer dans le navigateur (ordinateur et mobile) :** https://dorianG-epitech.github.io/LaMontagneFPS/

## Commandes

**Ordinateur :** clic pour capturer la souris · ZQSD/WASD pour se déplacer · Shift pour sprinter · Espace pour sauter · Ctrl pour s'accroupir · clic gauche pour tirer · clic droit pour viser · R pour recharger · Échap pour libérer la souris.

**Mobile (en paysage) :** joystick à gauche (il apparaît là où tu poses le pouce) · glisser sur la moitié droite pour regarder · boutons **TIR** (on peut viser en glissant dessus), **VISER**, **SAUT**, **R** (recharger), **ACCR.** (accroupi, bascule) et **SPRINT** (bascule).

## Contenu

- Zone de 4 × 4 km centrée sur le bourg (Lambert-93), relief IGN RGE ALTI, photo aérienne BD ORTHO au sol.
- ~11 000 bâtiments BD TOPO (hauteur, étages, toits à deux pans ou en croupe), façades par style (bourg / pavillons) avec portes, garages et vitrines portant le nom réel des commerces (OpenStreetMap).
- Église Notre-Dame-de-l'Assomption modélisée d'après des photos Panoramax (hauteur du clocher par LiDAR HD).
- ~49 000 arbres placés depuis le modèle de hauteur de canopée LiDAR HD, haies, murets, clôtures.
- Marquages au sol, passages piétons, bancs, potelets, arrêts de bus, panneaux d'entrée de ville, voitures garées sur les places OSM.
- La version navigateur réduit les distances d'affichage, la taille des textures et les ombres pour tourner sur téléphone (WebGL 2).

## Structure

- `Assets/` — scènes, scripts (`Assets/LaMontagne/Scripts`), outils éditeur (`Assets/Editor`), template web (`Assets/WebGLTemplates/LaMontagne`).
- `Assets/LaMontagne/Resources/LaMontagne/city_web_*.bytes` — maillages de la ville au format compact (généré par `Tools/pack_web.py`).
- `Tools/` — chaîne de données en Python : `fetch_ign.py` (téléchargement IGN), `build_city.py` (génération de la ville), `gen_textures*.py`, `pack_web.py`.
- `docs/` — build WebGL prêt à l'emploi servi par GitHub Pages.

## Régénérer

1. `python3 Tools/fetch_ign.py` (données IGN) puis récupérer les données OSM (voir `Tools/`).
2. `python3 Tools/build_city.py` → `ImportData/` (non versionné : trop volumineux).
3. Unity 6000.3 : menu **LaMontagne › Build Scene**.
4. `python3 Tools/pack_web.py`, puis build web : `Unity -batchmode -buildTarget WebGL -executeMethod LaMontagneWebBuild.Build` → `Builds/WebGL` (à copier dans `docs/`).

Sans `ImportData/`, le projet charge la ville depuis `city_web_*.bytes`.

## Crédits et licences des données

- **IGN — Géoplateforme**, Licence Ouverte / Open Licence **Etalab 2.0** : RGE ALTI®, BD ORTHO®, BD TOPO® V3, LiDAR HD (MNH, MNS). Source : IGN — https://geoservices.ign.fr
- **OpenStreetMap** © contributeurs OpenStreetMap, licence **ODbL 1.0** — https://www.openstreetmap.org/copyright (commerces, passages piétons, clôtures, mobilier, panneaux, places de stationnement).
- **Panoramax** — photos de rue utilisées uniquement comme références visuelles (non incluses dans le jeu) : photos de *Nzau* (CC-BY-SA 4.0) et *mamute* (Etalab 2.0). https://panoramax.fr — aucune image Google Street View n'a été utilisée.
- **bpypolyskel** (GPL-3.0) — utilisé hors ligne dans `Tools/` pour les toits ; non inclus dans le jeu.
- Textures, arbres, arme et sons : générés procéduralement. Les lampadaires sont placés de façon procédurale (absents d'OSM ici).

Détail complet : [CREDITS.md](CREDITS.md).
