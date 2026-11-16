# ARR Feasibility Analysis — Phase 1 (India)

Screens a land parcel for Afforestation, Reforestation and Revegetation
eligibility under Verra VM0047. Upload a boundary, get back a per-pixel map of
which land qualifies and how many hectares that comes to.

Built for India: the default forest definition is the Forest Survey of India
threshold that India reports under to UNFCCC and FAO.

---

## What Phase 1 does

- Loads a project boundary from KML, KMZ, GeoJSON, or a zipped Shapefile, or
  from a shape drawn on the map
- Runs a three-class eligibility mask in Google Earth Engine — eligible,
  flagged for review, ineligible
- Renders that mask as a coloured overlay on satellite imagery
- Reports hectares in each class, plus a breakdown of *why* land was excluded
- Prints every assumption and limitation alongside the result

Not yet built: the LULC classification layer (Phase 3) and the carbon credit
estimator (Phase 4).

---

## The eligibility test

VM0047 requires land to have been **non-forest for the full lookback period**
— normally ten years — before project start, against the host country's forest
definition.

That cannot be answered from one dataset, which is the main thing this module
gets right:

| Dataset | Catches |
|---|---|
| ESA WorldCover (2021) | Land that is forest **now**, including natural regrowth since 2000 |
| Hansen `lossyear` | Land that was forest **until recently** and so fails the lookback |
| Hansen `treecover2000` | The year-2000 canopy baseline |

Using `treecover2000` alone — a snapshot from the year 2000 — would miss land
that has reforested naturally since, and would wrongly pass it as eligible.

### Three classes, not two

| Class | Colour | Meaning |
|---|---|---|
| Eligible | green | Passes every test on current public data |
| Flagged | amber | Wetland, slope above 30°, protected-area overlap, or forest loss just outside the window |
| Ineligible | grey | Currently forest, forest at baseline, lost within the window, built-up, water, or mangrove |

The flagged class is where practical value sits. 200 ha flagged for wetland
tells a developer exactly what to ground-truth; the same 200 ha silently
dropped into "ineligible" is lost project area.

### India's forest definition

Two ship, and they give different answers:

- **FSI / UNFCCC (default)** — 10% canopy, 1 ha minimum, 2 m height. What
  India reports under internationally.
- **CDM A/R (stricter canopy)** — 15% crown cover, 0.05 ha minimum. Verify
  against the current DNA submission before relying on it for a real project.

A *lower* canopy threshold classifies more land as forest, which makes *less*
land eligible. The 10% default is therefore the conservative choice.

---

## Setup

### 1. Earth Engine access

1. Register for non-commercial use at
   [signup.earthengine.google.com](https://signup.earthengine.google.com)
2. In [Google Cloud Console](https://console.cloud.google.com), create a
   project and enable the **Earth Engine API**
3. For local development that is all you need — run `earthengine authenticate`
   once
4. For deployment, create a service account with the **Earth Engine Resource
   Viewer** role and download its JSON key

### 2. Install and run

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # then set GEE_PROJECT
earthengine authenticate          # local auth, once

streamlit run streamlit_app.py
```

Test it with `samples/sample_bundelkhand.kml` — roughly 500 ha of degraded
land in Madhya Pradesh.

### 3. Deploy to Streamlit Community Cloud

Point [share.streamlit.io](https://share.streamlit.io) at this repo with
`streamlit_app.py` as the entry point, then add under **Secrets**:

```toml
GEE_PROJECT = "your-gcp-project-id"
GEE_CREDENTIALS_JSON = '{"type":"service_account", ...}'
```

The service account JSON goes in as a single line. It must never reach the
repository — `.env` and `*.json` are already in `.gitignore`.

---

## Layout

```
app/
  config.py        Dataset pins, India presets, adaptive scale, class palette
  geometry.py      Boundary parsing, validation, equal-area hectares
  gee_init.py      Earth Engine auth — service account and local paths
  eligibility.py   The three-class mask, tile rendering, area reduction
  mapping.py       Folium map, basemaps, legend
streamlit_app.py   UI
tests/
  test_classification_logic.py   Exhaustive class-algebra check
samples/
  sample_bundelkhand.kml
```

Everything in `app/` is a pure function taking a geometry and parameters and
returning a dictionary. The Streamlit UI is deliberately thin, so a FastAPI
layer can call the same functions when this moves to the production stack.

---

## Known limitations

These are printed in the app alongside every result, not buried here.

- **Data currency gap.** Hansen loss data ends 2023 and WorldCover is the 2021
  epoch. For a 2026 project start, recent clearing will not appear.
- **No peat layer.** No free global peat or organic-soil dataset exists at
  usable resolution. Herbaceous wetland is a proxy, which is why those pixels
  are flagged rather than classified either way. Planting on drained peat can
  make a project net-negative.
- **Adaptive scale.** Areas above 5,000 ha run at 30 m and above 50,000 ha at
  100 m, because a 10 m reduction over a large polygon exceeds Earth Engine's
  pixel limit. The scale used is always displayed.
- **Overlapping exclusion reasons.** A pixel can fail several tests at once,
  so the reason breakdown does not sum to the ineligible total.
- **Indicative only.** Every figure comes from global public datasets. This
  does not replace a ground survey, and it is not a PDD.

---

## Testing

```bash
python3 tests/test_classification_logic.py
```

Checks all 2,048 combinations of the eleven per-pixel conditions and asserts
that the three classes stay mutually exclusive and exhaustive. Overlapping
classes would double-count hectares and overstate eligible area without
raising an error, so this property is worth holding onto as the rules grow.
