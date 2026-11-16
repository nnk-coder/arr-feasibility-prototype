# ARR Feasibility Analysis — v1.0 (India)

Screens a land parcel for Afforestation, Reforestation and Revegetation
eligibility under Verra VM0047. Upload a boundary, get back a per-pixel map of
which land qualifies and how many hectares that comes to.

Built for India: the default forest definition is the Forest Survey of India
threshold that India reports under to UNFCCC and FAO.

---

## What it does

Screens land for Afforestation, Reforestation and Revegetation eligibility
under Verra VM0047, estimates the carbon it could generate, and says plainly
what would stop the project.

1. **Parcels in** — KML, KMZ, GeoJSON or zipped Shapefiles, several at once,
   plus shapes drawn on the map. Files and drawings stack. Each parcel takes
   its name from the file where one exists.
2. **Eligibility** — a per-pixel three-class mask (eligible / flagged /
   ineligible) rendered as a map overlay, with a breakdown of what excluded
   each hectare.
3. **Land cover** — ESA WorldCover at 10 m with the official palette, an area
   breakdown, and a note on what each class means for ARR.
4. **Carbon** — a low / central / high credit trajectory over the crediting
   period, net of baseline, leakage, uncertainty and non-permanence buffer.
5. **Summary** — a written feasibility assessment built from the computed
   figures. Works with no API key; richer with one.
6. **Export** — JSON, GeoJSON parcels with their scores, and CSV series.

### Governing principle

**Code computes, AI interprets.** Every auditable number comes from Earth
Engine or deterministic Python. The language model, when used at all,
receives those numbers and writes prose around them — it never produces a
figure. `tests/test_narrative.py` enforces this by checking that every number
in the generated report traces back to an input.

### Per-parcel scoring

Scoring N parcels naively costs N Earth Engine round trips. Instead the parcel
index is painted into a raster and packed with the class code into one value
(`parcel_id * 10 + class`), so a single grouped reduction returns every
(parcel, class) area. Per-parcel mode costs the same as merged mode whatever
the parcel count.

Overlapping ground is assigned to whichever parcel paints last, so parcel
areas sum to the merged total rather than double counting. The app warns when
parcels overlap.

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

### Carbon estimate

Two methodological points drive the design, both places feasibility estimates
commonly go wrong.

**Density does not multiply the rate.** The sequestration rate is per
hectare, so multiplying by trees per hectare would double count. Density
instead drives a stocking adequacy check — below roughly 400 effective
trees/ha the stand may never meet the forest definition at all — and a yield
scalar against the density the rate factor assumes. Survival is folded into
that scalar rather than applied separately, and the scalar is capped at 1.0,
because over-planting raises mortality rather than yield.

**The growth curve is conditional.** Published per-hectare factors are usually
a mean annual increment, which already averages in the slow establishment
years. Applying a sigmoid on top of an MAI figure discounts the ramp-up twice.
The curve therefore only applies when the rate is a peak/mature figure. The
default is MAI with no curve, which fails conservative rather than inflated.

The range spans survival (65/80/92%), non-permanence buffer (25/15/10%,
following the VCS AFOLU Non-Permanence Risk Tool), uncertainty and leakage.
Expect roughly a 2× spread between low and high — that gap is real, and
showing it is what keeps a feasibility study credible.

---

## Setup

### 1. Earth Engine access

1. Register for non-commercial use at
   [signup.earthengine.google.com](https://signup.earthengine.google.com)
2. In [Google Cloud Console](https://console.cloud.google.com), create a
   project and enable the **Earth Engine API**
3. For local development that is all you need — run `earthengine authenticate`
   once
4. For deployment, create a service account and download its JSON key. Grant
   it **two** roles:
   - **Earth Engine Resource Writer** — the Viewer role only grants
     `earthengine.maps.get`, and rendering the eligibility overlay needs
     `earthengine.maps.create`. The app never writes to your assets; Google
     classifies tile generation as creating a map resource.
   - **Service Usage Consumer** — permission to call APIs on the project.

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
  validation.py    Pure checks - band names, credential keys, error scrubbing.
                   No third-party imports, so its tests run anywhere.
  config.py        Dataset pins, India presets, adaptive scale, class palette
  carbon.py        Credit estimator - pure Python, no GEE, no LLM
  lulc.py          Land cover palette, labels, ARR relevance, summarisation
  narrative.py     Feasibility report - deterministic, optional Gemini
  imagery.py       Date-filtered Sentinel-2 composite
  geometry.py      Boundary parsing, validation, equal-area hectares
  gee_init.py      Earth Engine auth — service account and local paths
  eligibility.py   The three-class mask, tile rendering, area reduction
  mapping.py       Folium map, basemaps, legend
streamlit_app.py   UI
sync_to_repo.sh    Copies this release into a git repo, replacing whole
                   directories so no stale file survives
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
./run_tests.sh
```

No Earth Engine credentials needed. The script runs in two groups:

**No dependencies required** — classification logic, credential validation,
band names, and the parcel grouping key. These import only `app/validation.py`
and `app/config.py`, which use nothing outside the standard library, so they
run on a fresh checkout before `pip install`.

**Requires installed packages** — the geometry pipeline and parcel
accumulation. Skipped with instructions if the dependencies are missing,
rather than failing with import tracebacks.

Run it before every push.

Checks all 2,048 combinations of the eleven per-pixel conditions and asserts
that the three classes stay mutually exclusive and exhaustive. Overlapping
classes would double-count hectares and overstate eligible area without
raising an error, so this property is worth holding onto as the rules grow.
